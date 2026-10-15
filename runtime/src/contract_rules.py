from __future__ import annotations

import ast
from collections import defaultdict

from .ast_utils import dotted_name
from .facts import ModuleFacts
from .model import Finding
from .policy_common import (
    all_parameters,
    call_name,
    direct_body_nodes,
)

PAIR_ARGUMENT_COUNT = 2

MIN_PARALLEL_CONTRACT_ITEMS = 2
MIN_FIELD_ALIAS_CALLS = 2
# Exact known methods on builtin ``str`` returning a fresh value or scalar.
# They do not mutate a literal string; arbitrary receiver method names remain UNKNOWN.
PURE_STRING_METHODS = frozenset(
    {
        "capitalize",
        "casefold",
        "center",
        "count",
        "encode",
        "endswith",
        "expandtabs",
        "find",
        "format",
        "format_map",
        "index",
        "isalnum",
        "isalpha",
        "isascii",
        "isdecimal",
        "isdigit",
        "isidentifier",
        "islower",
        "isnumeric",
        "isprintable",
        "isspace",
        "istitle",
        "isupper",
        "join",
        "ljust",
        "lower",
        "lstrip",
        "partition",
        "removeprefix",
        "removesuffix",
        "replace",
        "rfind",
        "rindex",
        "rjust",
        "rpartition",
        "rsplit",
        "rstrip",
        "split",
        "splitlines",
        "startswith",
        "strip",
        "swapcase",
        "title",
        "translate",
        "upper",
        "zfill",
    }
)

# Closed, statically bound stdlib AST classes used by type-probe analysis.
# Do not dynamically inspect ``ast`` through vars/getattr/__dict__: an unknown
# node kind is UNKNOWN, never a reason to emit a QG060 Critical.
_CANONICAL_AST_CLASSES: dict[str, type[ast.AST]] = {
    cls.__name__: cls
    for cls in (
        ast.AST,
        ast.stmt,
        ast.expr,
        ast.pattern,
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.Delete,
        ast.Return,
        ast.Raise,
        ast.Assert,
        ast.Expr,
        ast.Pass,
        ast.Break,
        ast.Continue,
        ast.Import,
        ast.ImportFrom,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.If,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.Match,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Global,
        ast.Nonlocal,
        ast.Call,
        ast.Compare,
        ast.Attribute,
        ast.Subscript,
        ast.Name,
        ast.Constant,
        ast.Tuple,
        ast.List,
        ast.Set,
        ast.Dict,
        ast.NamedExpr,
        ast.BoolOp,
        ast.BinOp,
        ast.UnaryOp,
        ast.IfExp,
        ast.Lambda,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.Await,
        ast.Yield,
        ast.YieldFrom,
        ast.Starred,
        ast.Slice,
        ast.JoinedStr,
        ast.FormattedValue,
        ast.MatchAs,
        ast.MatchOr,
        ast.MatchValue,
        ast.MatchSingleton,
        ast.MatchSequence,
        ast.MatchMapping,
        ast.MatchClass,
        ast.MatchStar,
        ast.arguments,
        ast.arg,
        ast.keyword,
        ast.comprehension,
        ast.alias,
        ast.ExceptHandler,
    )
}


MAPPING_PROTOCOL_METHODS = {
    "clear",
    "copy",
    "get",
    "items",
    "keys",
    "pop",
    "popitem",
    "setdefault",
    "update",
    "values",
}


def check_contract_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
) -> list[Finding]:
    """
    检查消费方猜测、修补或双协议访问内部数据的模式。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。

    Returns:
        契约消费相关发现列表。
    """
    nodes = direct_body_nodes(node)
    findings: list[Finding] = []
    findings.extend(check_type_probe_rules(facts, node, nodes, qualname))
    findings.extend(check_required_mapping_default_rules(facts, node, nodes, qualname))
    findings.extend(check_length_contract_rules(facts, node, nodes, qualname))
    findings.extend(check_field_contract_rules(facts, node, nodes, qualname))
    findings.extend(check_runtime_contract_rules(facts, node, nodes, qualname))
    return findings


def check_required_mapping_default_rules(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """Detect a required concrete mapping silently accepting ``None`` as empty.

    Only a real, unshadowed ``dict`` formal without a default is sufficient
    evidence. Optional/untyped inputs, fields, aliases and name-only guesses
    intentionally stay outside this bounded rule.
    """
    positional = (*function.args.posonlyargs, *function.args.args)
    defaulted = {arg.arg for arg in positional[len(positional) - len(function.args.defaults):]}
    defaulted.update(
        arg.arg for arg, default in zip(function.args.kwonlyargs, function.args.kw_defaults)
        if default is not None
    )
    required = {
        arg.arg
        for arg in all_parameters(function)
        if arg.arg not in defaulted
        and arg.annotation is not None
        and root_annotation_name(arg.annotation) == "dict"
        and _builtin_type_unshadowed(facts, function, "dict")
        and not _scope_rebinds(function, arg.arg)
    }
    findings: list[Finding] = []
    for item in nodes:
        if not (
            isinstance(item, ast.BoolOp) and isinstance(item.op, ast.Or)
            and len(item.values) == 2
            and isinstance(item.values[0], ast.Name)
            and item.values[0].id in required
            and isinstance(item.values[1], ast.Dict)
            and not item.values[1].keys
        ):
            continue
        name = item.values[0].id
        findings.append(
            facts.make_finding(
                item,
                "QG066",
                f"必需字典参数 `{name}` 被 `or {{}}` 静默转换为可用空结构。",
                symbol=qualname,
                severity="warning",
                confidence="high",
                suggestion="保留必需输入的非空契约；不合法输入应在边界显式拒绝。",
                evidence={"parameter": name, "annotation": "dict", "classification": "PROVEN_REQUIRED_CONTRACT"},
            )
        )
    return findings


def check_type_probe_rules(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """
    检查内部对象类型猜测和临时容器包装。

    Args:
        facts: 当前模块静态事实。
        function: 当前函数定义。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        类型猜测相关发现列表。
    """
    parameter_annotations = {
        argument.arg: argument.annotation
        for argument in all_parameters(function)
        if argument.annotation is not None
    }
    findings: list[Finding] = []
    for item in nodes:
        if (
            isinstance(item, ast.Call)
            and call_name(item) == "isinstance"
            and len(item.args) == 2
            and not item.keywords
            and _builtin_isinstance_unshadowed(facts, function)
        ):
            checked = dotted_name(item.args[0])
            if checked in parameter_annotations:
                annotation = parameter_annotations[checked]
                if root_annotation_name(annotation) in {
                    "Any",
                    "object",
                    "Union",
                    "Optional",
                }:
                    continue
                relation, provenance = _type_probe_relation(
                    facts, function, annotation, item.args[1]
                )
                if _scope_rebinds(function, checked):
                    # The annotated formal is no longer an authoritative type
                    # source after a local assignment/augmented assignment.
                    relation, provenance = "unknown", "parameter_rebound"
                if _verified_registration_guard(
                    facts, function, item, checked, annotation, item.args[1]
                ):
                    continue
                if relation == "subtype":
                    # A formal descendant of the declared input is a legitimate
                    # type discriminant. Its usefulness belongs to behavioral/
                    # structure review, not an unproven type-contract CRITICAL.
                    continue
                # An UNKNOWN import/hierarchy and an input validation guard are
                # not evidence that a type probe is a quality defect. Source-
                # bound, statically disjoint concrete builtins are the only
                # type-contract violation this rule currently proves.  In
                # particular, do not flood users with REVIEW warnings merely
                # because an external DSL subtype could not be resolved.
                definitive = relation == "disjoint_builtin"
                if not definitive:
                    continue
                findings.append(
                    facts.make_finding(
                        item,
                        "QG060",
                        (
                            f"`{checked}` 的类型判别与声明契约静态不相容。"
                            if definitive
                            else f"`{checked}` 的运行时类型判别尚无可核实的必要性证明。"
                        ),
                        symbol=qualname,
                        severity="critical" if definitive else "warning",
                        confidence="high" if definitive else "medium",
                        suggestion=(
                            "按已声明的输入类型直接使用；如确实存在多个合法类型，"
                            "须明确建模类型关系和各分支业务语义。不得通过转换兜底掩盖契约违例。"
                        ),
                        evidence={
                            "annotation": ast.unparse(annotation),
                            "discriminator": ast.unparse(item.args[1]),
                            "relation": relation,
                            "type_provenance": provenance,
                            "classification": "PROVEN" if definitive else "REVIEW",
                        },
                    )
                )
        if (
            isinstance(item, ast.IfExp)
            and wraps_by_type_probe(item)
            and isinstance(item.test, ast.Call)
            and bool(item.test.args)
            and dotted_name(item.test.args[0]) in parameter_annotations
        ):
            findings.append(
                facts.make_finding(
                    item,
                    "QG061",
                    "根据 isinstance() 把内部值临时包装为列表或字典。",
                    symbol=qualname,
                    severity="critical",
                    suggestion="生产方固定容器类型，消费方直接按契约使用；禁止用 isinstance 三元包装替代正式 schema。",
                )
            )
    return findings


def _verified_registration_guard(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    probe: ast.Call,
    parameter: str,
    annotation: ast.expr,
    discriminator: ast.expr,
) -> bool:
    """Recognize fail-fast registration of an explicitly declared ABC contract.

    The failing branch raises a TypeError. The successful path must actually
    register a class or invoke the ABC's declared abstract operation. These
    bounded source facts do not imply that an arbitrary isinstance guard is
    legitimate, and never waive incompatible builtin consumer checks.
    """
    if facts.tree is None or not isinstance(discriminator, ast.Name):
        return False
    guard_index = next(
        (
            index
            for index, statement in enumerate(function.body)
            if isinstance(statement, ast.If)
            and any(child is probe for child in ast.walk(statement.test))
        ),
        None,
    )
    if guard_index is None:
        return False
    guard = function.body[guard_index]
    if not isinstance(guard, ast.If) or guard.orelse or len(guard.body) != 1:
        return False
    failure = guard.body[0]
    if not (
        isinstance(failure, ast.Raise)
        and isinstance(failure.exc, ast.Call)
        and isinstance(failure.exc.func, ast.Name)
        and failure.exc.func.id == "TypeError"
        and not _scope_rebinds(function, "TypeError")
    ):
        return False
    if not _is_negated_probe(guard.test, probe):
        return False
    # The ABC is a source declaration, not an inferred class based on a name.
    if (
        isinstance(annotation, ast.Subscript)
        and dotted_name(annotation.value) == "type"
    ):
        base = annotation.slice
        if not (isinstance(base, ast.Name) and discriminator.id == "type"):
            return False
        if not _builtin_type_unshadowed(facts, function, "type"):
            return False
        if not _guard_checks_subclass(guard.test, parameter, base.id):
            return False
        interface = _local_abc(facts, base.id)
        return interface is not None and _registration_stores_class(
            function.body[guard_index + 1 :], parameter
        )
    if not (isinstance(annotation, ast.Name) and discriminator.id == annotation.id):
        return False
    interface = _local_abc(facts, annotation.id)
    if interface is None:
        return False
    if not any(
        isinstance(stmt, ast.ImportFrom)
        and stmt.module == "abc"
        and any(
            alias.name == "abstractmethod" and alias.asname is None
            for alias in stmt.names
        )
        for stmt in facts.tree.body
    ):
        return False
    abstract_operations = {
        stmt.name
        for stmt in interface.body
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            dotted_name(decorator) == "abstractmethod"
            for decorator in stmt.decorator_list
        )
    }
    return _calls_declared_registration_operation(
        function.body[guard_index + 1 :], parameter, abstract_operations
    )


def _local_abc(facts: ModuleFacts, name: str) -> ast.ClassDef | None:
    """Resolve an ABC declared after a genuine, unshadowed stdlib import."""
    if facts.tree is None:
        return None
    abc_bound = False
    for statement in facts.tree.body:
        if isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                bound_name = alias.asname or alias.name
                if bound_name == "ABC":
                    abc_bound = statement.module == "abc" and alias.name == "ABC"
        if isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.name == "ABC":
                abc_bound = False
            if isinstance(statement, ast.ClassDef) and statement.name == name:
                if abc_bound and any(
                    isinstance(base, ast.Name) and base.id == "ABC"
                    for base in statement.bases
                ):
                    return statement
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            if any(
                isinstance(target, ast.Name) and target.id == "ABC"
                for target in targets
            ):
                abc_bound = False
    return None


def _is_negated_probe(condition: ast.expr, probe: ast.Call) -> bool:
    """The probed input must fail closed on the checked type test."""
    predicates = (
        condition.values
        if isinstance(condition, ast.BoolOp) and isinstance(condition.op, ast.Or)
        else [condition]
    )
    return any(
        isinstance(predicate, ast.UnaryOp)
        and isinstance(predicate.op, ast.Not)
        and predicate.operand is probe
        for predicate in predicates
    )


def _guard_checks_subclass(condition: ast.expr, parameter: str, base: str) -> bool:
    """Recognize explicit rejection of an invalid registered ABC subclass."""
    predicates = (
        condition.values
        if isinstance(condition, ast.BoolOp) and isinstance(condition.op, ast.Or)
        else [condition]
    )
    return any(
        isinstance(predicate, ast.UnaryOp)
        and isinstance(predicate.op, ast.Not)
        and isinstance(predicate.operand, ast.Call)
        and call_name(predicate.operand) == "issubclass"
        and len(predicate.operand.args) == 2
        and dotted_name(predicate.operand.args[0]) == parameter
        and dotted_name(predicate.operand.args[1]) == base
        for predicate in predicates
    )


def _registration_stores_class(body: list[ast.stmt], parameter: str) -> bool:
    """Prove successful class registration retains the input for later dispatch."""
    for statement in body:
        if (
            isinstance(statement, ast.Assign)
            and dotted_name(statement.value) == parameter
        ):
            if any(
                isinstance(target, ast.Attribute)
                and dotted_name(target.value) == "self"
                for target in statement.targets
            ):
                return True
        if not isinstance(statement, ast.Expr) or not isinstance(
            statement.value, ast.Call
        ):
            continue
        call = statement.value
        if (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "append"
            and dotted_name(call.func.value).startswith("self.")
            and len(call.args) == 1
            and dotted_name(call.args[0]) == parameter
        ):
            return True
    return False


def _calls_declared_registration_operation(
    body: list[ast.stmt], parameter: str, operations: set[str]
) -> bool:
    """Check successful registration invokes a declared abstract operation."""
    return any(
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Attribute)
        and dotted_name(statement.value.func.value) == parameter
        and statement.value.func.attr in operations
        for statement in body
    )


# Builtins whose distinct concrete representations cannot share a compatible
# instance under the ordinary Python built-in type contract.
_CLOSED_BUILTINS = frozenset(
    {"str", "bytes", "int", "float", "bool", "dict", "list", "tuple", "set"}
)


def _builtin_isinstance_unshadowed(
    facts: ModuleFacts, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> bool:
    """Do not mistake a user-defined identifier for the builtin type predicate."""
    if any(parameter.arg == "isinstance" for parameter in all_parameters(function)):
        return False
    if _scope_rebinds(function, "isinstance"):
        return False
    if "isinstance" in facts.imports or facts.tree is None:
        return False
    return not any(
        (
            isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and statement.name == "isinstance"
        )
        or (
            isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "isinstance"
                for target in (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
            )
        )
        for statement in facts.tree.body
    )


def _builtin_type_unshadowed(
    facts: ModuleFacts, function: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> bool:
    """Assert lexical ownership of a canonical built-in type name."""
    if facts.tree is None or name in facts.imports:
        return False
    if name in {argument.arg for argument in all_parameters(function)}:
        return False
    if _scope_rebinds(function, name):
        return False
    for stmt in facts.tree.body:
        if (
            isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and stmt.name == name
        ):
            return False
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if any(
                isinstance(target, ast.Name) and target.id == name for target in targets
            ):
                return False
    return True


def _scope_rebinds(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """Detect a local overwrite of an annotated formal or source import."""
    for node in direct_body_nodes(function):
        if isinstance(node, ast.ExceptHandler) and node.name == name:
            return True
        if (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name.split(".", 1)[0]) == name:
                    return True
    return False


def _canonical_ast_type(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    expression: ast.expr,
) -> type[ast.AST] | None:
    """Bind an authored AST class to a proven stdlib import, not a name pattern."""
    if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
        binding, member = expression.value.id, expression.attr
        if facts.imports.get(binding) != "ast":
            return None
    elif isinstance(expression, ast.Name):
        binding = expression.id
        imported = facts.imports[binding] if binding in facts.imports else ""
        if not imported.startswith("ast.") or imported.count(".") != 1:
            return None
        member = imported.partition(".")[2]
    else:
        return None
    if binding in {
        parameter.arg for parameter in all_parameters(function)
    } or _scope_rebinds(function, binding):
        return None
    if facts.tree is None:
        return None
    # Rebinding the module makes an apparent ast.* name untrustworthy.
    for node in facts.tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == binding
        ):
            return None
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == binding
                for target in targets
            ):
                return None
    return _CANONICAL_AST_CLASSES.get(member)


def _type_probe_relation(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    annotation: ast.expr,
    discriminator: ast.expr,
) -> tuple[str, str]:
    """Classify only source-bound, closed-type facts; unknown never means conflict."""
    if isinstance(discriminator, ast.Tuple):
        if not discriminator.elts:
            return "unknown", "empty_discriminator"
        branches = [
            _type_probe_relation(facts, function, annotation, item)
            for item in discriminator.elts
        ]
        if all(relation == "subtype" for relation, _ in branches):
            return "subtype", "tuple_of_declared_subtypes"
        if all(relation == "disjoint_builtin" for relation, _ in branches):
            return "disjoint_builtin", "tuple_of_disjoint_builtins"
        return "unknown", "mixed_or_unresolved_tuple"

    declared_ast = _canonical_ast_type(facts, function, annotation)
    checked_ast = _canonical_ast_type(facts, function, discriminator)
    if declared_ast is not None and checked_ast is not None:
        if issubclass(checked_ast, declared_ast):
            return "subtype", "stdlib_ast_class_hierarchy"
        if issubclass(declared_ast, checked_ast):
            return "redundant_or_narrowing", "stdlib_ast_class_hierarchy"
        return "unknown", "unrelated_ast_categories"

    # Reuse parsed source declarations as type ownership facts. Only a direct
    # local, unambiguous inheritance chain proves a subtype. Incomparable user
    # classes stay UNKNOWN because Python allows multiple inheritance.
    if (
        facts.tree is not None
        and isinstance(annotation, ast.Name)
        and isinstance(discriminator, ast.Name)
    ):
        declarations = {
            statement.name: statement
            for statement in facts.tree.body
            if isinstance(statement, ast.ClassDef)
        }
        declared_class = annotation.id
        checked_class = discriminator.id
        if declared_class in declarations and checked_class in declarations:
            if _local_class_descends(declarations, checked_class, declared_class):
                return "subtype", "source_class_inheritance"
            if _local_class_descends(declarations, declared_class, checked_class):
                return "redundant_or_narrowing", "source_class_inheritance"
    declared = dotted_name(annotation)
    checked = dotted_name(discriminator)
    if (
        declared in _CLOSED_BUILTINS
        and checked in _CLOSED_BUILTINS
        and _builtin_type_unshadowed(facts, function, declared)
        and _builtin_type_unshadowed(facts, function, checked)
    ):
        if declared == "int" and checked == "bool":
            return "subtype", "builtin_type_contract"
        if declared == checked or (declared == "bool" and checked == "int"):
            return "redundant_or_narrowing", "builtin_type_contract"
        return "disjoint_builtin", "builtin_closed_type_contract"
    # Explicit same nominal type does not prove misuse, but a repeated runtime
    # check cannot be called a high-confidence bug from syntax alone.
    if declared and declared == checked:
        return "redundant_or_narrowing", "same_declared_type"
    return "unknown", "unresolved_type_binding"


def _local_class_descends(
    declarations: dict[str, ast.ClassDef],
    descendant: str,
    ancestor: str,
) -> bool:
    """Prove only declared, source-bound same-module inheritance paths."""
    visited: set[str] = set()
    pending = [descendant]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if current == ancestor:
            return True
        node = declarations[current] if current in declarations else None
        if node is not None:
            pending.extend(
                base.id
                for base in node.bases
                if isinstance(base, ast.Name) and base.id in declarations
            )
    return False


def root_annotation_name(annotation: ast.expr | None) -> str:
    """
    提取参数注解的顶层类型名称。

    Args:
        annotation: 参数类型注解。

    Returns:
        顶层类型名称；PEP 604 联合类型返回 Union，无法识别时返回空字符串。
    """
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return "Union"
    if isinstance(annotation, ast.Subscript):
        return dotted_name(annotation.value).rsplit(".", 1)[-1]
    return dotted_name(annotation).rsplit(".", 1)[-1]


def check_length_contract_rules(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """
    检查静默截断和长度修复。

    Args:
        facts: 当前模块静态事实。
        function: 当前函数定义。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        长度契约相关发现列表。
    """
    findings: list[Finding] = []
    guards = equal_length_guards(function)
    for item in nodes:
        if isinstance(item, ast.Call) and call_name(item) == "zip":
            strict = next(
                (keyword.value for keyword in item.keywords if keyword.arg == "strict"),
                None,
            )
            argument_names = [dotted_name(argument) for argument in item.args]
            guarded = zip_arguments_guarded(argument_names, item.lineno, guards)
            if (
                len(item.args) >= MIN_PARALLEL_CONTRACT_ITEMS
                and not (isinstance(strict, ast.Constant) and strict.value is True)
                and not guarded
            ):
                findings.append(
                    facts.make_finding(
                        item,
                        "QG062",
                        "zip() 未指定 strict=True，长度不一致时可能静默截断。",
                        symbol=qualname,
                        severity="info",
                        confidence="medium",
                        suggestion="等长属于契约时使用 strict=True；明确需要最短序列语义时保留并写明。",
                        evidence={"arguments": argument_names},
                    )
                )
        if is_length_repair(item):
            findings.append(
                facts.make_finding(
                    item,
                    "QG063",
                    "使用 min(len(...)) 或交叉长度切片静默修复列表长度。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="让生产方保证长度关系；非法结构应显式失败。",
                )
            )
    return findings


def equal_length_guards(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[frozenset[str], int]]:
    """
    收集在后续语句前已显式保证两个序列等长的守卫。

    Args:
        function: 当前函数定义。

    Returns:
        无序变量对及守卫结束行组成的列表。
    """
    guards: list[tuple[frozenset[str], int]] = []
    for statement in function.body:
        pair: frozenset[str] | None = None
        if isinstance(statement, ast.Assert):
            pair = equal_length_pair(statement.test, require_equal=True)
        elif isinstance(statement, ast.If) and branch_stops(statement.body):
            pair = equal_length_pair(statement.test, require_equal=False)
        if pair is not None:
            guards.append((pair, statement.end_lineno or statement.lineno))
    return guards


def equal_length_pair(
    expression: ast.expr,
    require_equal: bool,
) -> frozenset[str] | None:
    """
    从 len(a) 与 len(b) 比较中提取变量对。

    Args:
        expression: 比较表达式。
        require_equal: True 时接受相等比较，否则接受不等比较。

    Returns:
        可识别时返回两个变量组成的无序集合。
    """
    if not isinstance(expression, ast.Compare) or len(expression.ops) != 1:
        return None
    accepted = (ast.Eq,) if require_equal else (ast.NotEq,)
    if not isinstance(expression.ops[0], accepted):
        return None
    operands = [expression.left, expression.comparators[0]]
    names: list[str] = []
    for operand in operands:
        if not (
            isinstance(operand, ast.Call)
            and call_name(operand) == "len"
            and operand.args
        ):
            return None
        name = dotted_name(operand.args[0])
        if not name:
            return None
        names.append(name)
    return frozenset(names) if len(set(names)) == PAIR_ARGUMENT_COUNT else None


def branch_stops(body: list[ast.stmt]) -> bool:
    """
    判断守卫分支是否以 return 或 raise 终止。

    Args:
        body: 分支语句列表。

    Returns:
        分支包含无条件终止语句时返回 True。
    """
    return any(isinstance(statement, (ast.Return, ast.Raise)) for statement in body)


def zip_arguments_guarded(
    arguments: list[str],
    line: int,
    guards: list[tuple[frozenset[str], int]],
) -> bool:
    """
    判断 zip 的全部简单参数对是否已由前置长度守卫覆盖。

    Args:
        arguments: zip 实参名称。
        line: zip 调用行号。
        guards: 已收集的长度守卫。

    Returns:
        两参数 zip 已由更早守卫保证等长时返回 True。
    """
    if len(arguments) != PAIR_ARGUMENT_COUNT or not all(arguments):
        return False
    pair = frozenset(arguments)
    return any(
        guard_pair == pair and guard_line < line for guard_pair, guard_line in guards
    )


def check_field_contract_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """
    检查字段别名和对象/字典双协议访问。

    Args:
        facts: 当前模块静态事实。
        node: 当前函数定义。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        字段契约相关发现列表。
    """
    annotated = {arg.arg for arg in all_parameters(node) if arg.annotation is not None}
    accesses: dict[str, set[str]] = defaultdict(set)
    findings: list[Finding] = []
    for item in nodes:
        if is_alias_field_compatibility(item):
            findings.append(
                facts.make_finding(
                    item,
                    "QG064",
                    "同一位置兼容多个字段名，形成永久字段别名层。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="统一生产方字段并迁移旧数据。",
                )
            )
        if isinstance(item, ast.Subscript):
            base = dotted_name(item.value)
            if base:
                accesses[base].add("mapping")
        elif isinstance(item, ast.Attribute):
            if item.attr in MAPPING_PROTOCOL_METHODS:
                continue
            base = dotted_name(item.value)
            if base:
                accesses[base].add("attribute")
    for base, modes in accesses.items():
        if modes == {"mapping", "attribute"} and base in annotated:
            findings.append(
                facts.make_finding(
                    node,
                    "QG065",
                    f"`{base}` 在同一函数中同时按对象属性和字典结构访问。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="固定一种内部协议，不要同时兼容对象与字典。",
                )
            )
    return findings


def check_runtime_contract_rules(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """
    检查默认值替代、assert 和无效不可变调用。

    Args:
        facts: 当前模块静态事实。
        function: 当前函数定义。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        运行时契约相关发现列表。
    """
    parameters = {argument.arg for argument in all_parameters(function)}
    findings: list[Finding] = []
    for item in nodes:
        if isinstance(item, ast.Assert):
            referenced = {
                child.id
                for child in ast.walk(item.test)
                if isinstance(child, ast.Name) and child.id in parameters
            }
            findings.append(
                facts.make_finding(
                    item,
                    "QG069",
                    (
                        "使用 assert 校验函数输入或接口契约。"
                        if referenced
                        else "使用 assert 表达内部不变量。"
                    ),
                    symbol=qualname,
                    severity="warning" if referenced else "info",
                    confidence="high" if referenced else "medium",
                    suggestion="外部输入和运行时契约使用显式异常；assert 仅用于可被优化移除的内部不变量。",
                    evidence={"parameters": sorted(referenced)},
                )
            )
        if isinstance(item, ast.Expr) and isinstance(item.value, ast.Call):
            immutable_name = ignored_immutable_result(item.value, facts)
            if immutable_name:
                findings.append(
                    facts.make_finding(
                        item,
                        "QG070",
                        f"调用不可变对象方法 `{immutable_name}` 后丢弃返回值。",
                        symbol=qualname,
                        severity="warning",
                        suggestion="接收返回值或删除无效调用。",
                    )
                )
    return findings


def wraps_by_type_probe(node: ast.IfExp) -> bool:
    """
    判断条件表达式是否按类型探测临时包装容器。

    Args:
        node: 条件表达式。

    Returns:
        命中 `x if isinstance(x, list) else [x]` 等模式时返回 True。
    """
    if not isinstance(node.test, ast.Call) or call_name(node.test) != "isinstance":
        return False
    return isinstance(node.body, (ast.Name, ast.Attribute)) and isinstance(
        node.orelse, (ast.List, ast.Dict, ast.Tuple)
    )


def is_length_repair(node: ast.AST) -> bool:
    """
    判断节点是否表现为静默截断长度的修复模式。

    Args:
        node: 待分析节点。

    Returns:
        命中 min(len(...)) 或使用其他对象长度切片时返回 True。
    """
    if isinstance(node, ast.Call) and call_name(node) == "min":
        return (
            sum(
                isinstance(argument, ast.Call) and call_name(argument) == "len"
                for argument in node.args
            )
            >= MIN_PARALLEL_CONTRACT_ITEMS
        )
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        upper = node.slice.upper
        return (
            isinstance(upper, ast.Call)
            and call_name(upper) == "len"
            and bool(upper.args)
            and dotted_name(upper.args[0]) != dotted_name(node.value)
        )
    return False


def is_alias_field_compatibility(node: ast.AST) -> bool:
    """
    判断表达式是否在同一接收者上回退读取多个字段名。

    Args:
        node: 待分析节点。

    Returns:
        命中 receiver.get("new") or receiver.get("old") 时返回 True。
    """
    if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
        return False
    calls = [item for item in node.values if isinstance(item, ast.Call)]
    if len(calls) < MIN_FIELD_ALIAS_CALLS:
        return False
    receivers: set[str] = set()
    keys: set[str] = set()
    for call in calls:
        if (
            not isinstance(call.func, ast.Attribute)
            or call.func.attr != "get"
            or not call.args
        ):
            return False
        receivers.add(dotted_name(call.func.value))
        key = call.args[0]
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return False
        keys.add(key.value)
    return len(receivers) == 1 and len(keys) > 1


def is_default_literal(node: ast.expr) -> bool:
    """
    判断表达式是否为常见兜底字面量。

    Args:
        node: 待判断表达式。

    Returns:
        空容器、空字符串、零或 False 时返回 True。
    """
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    if not isinstance(node, ast.Constant):
        return False
    value = node.value
    return (
        value is None
        or value is False
        or value == ""
        or (type(value) is int and value == 0)
    )


def ignored_immutable_result(node: ast.Call, facts: ModuleFacts) -> str:
    """Require an exact builtin receiver before proving discarded immutable result.

    A freely named receiver or method cannot establish immutability. Only a
    source literal has a known exact type without additional compiler facts.
    Restrict actuals to constants to exclude observable argument evaluation.

    Args:
        node: Authored AST node in the immediate lexical ownership domain.
        facts: Previously established facts used as the sole evidence input.

    Returns:
        Findings for discarded immutable operations whose results are provably unused."""
    if not isinstance(node.func, ast.Attribute):
        return ""
    receiver = node.func.value
    if not (isinstance(receiver, ast.Constant) and type(receiver.value) is str):
        return ""
    if any(not isinstance(arg, ast.Constant) for arg in node.args) or any(
        keyword.arg is None or not isinstance(keyword.value, ast.Constant)
        for keyword in node.keywords
    ):
        return ""
    # The receiver is an exact Python builtin str, not a user-defined class
    # that happens to implement a method with the same spelling.
    if node.func.attr not in PURE_STRING_METHODS:
        return ""
    return node.func.attr


def check_side_effect_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    allow_print: bool = False,
) -> list[Finding]:
    """
    检查输入参数修改、外部状态和异常吞噬。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。
        allow_print: 是否允许命令行入口使用 print()。

    Returns:
        副作用和异常处理发现列表。
    """
    nodes = direct_body_nodes(node)
    findings: list[Finding] = []
    findings.extend(check_parameter_mutation(facts, node, nodes, qualname))
    findings.extend(check_exception_flow(facts, nodes, qualname))
    if not allow_print:
        findings.extend(check_print_calls(facts, nodes, qualname))
    return findings


def _declares_readonly_input(
    facts: ModuleFacts,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    annotation: ast.expr,
) -> bool:
    """Resolve a read-only annotation against a real builtin or stdlib import."""
    base = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    name = dotted_name(base)
    if name in {"tuple", "frozenset"}:
        return _builtin_type_unshadowed(facts, function, name)
    leaf = name.rsplit(".", 1)[-1]
    if leaf not in {"Mapping", "Sequence", "MappingProxyType"}:
        return False
    allowed = (
        {"types.MappingProxyType"}
        if leaf == "MappingProxyType"
        else {f"typing.{leaf}", f"collections.abc.{leaf}"}
    )
    if "." not in name:
        return facts.imports.get(name) in allowed and not _scope_rebinds(function, name)
    prefix = name.rsplit(".", 1)[0]
    return any(
        f"{imported}.{leaf}" in allowed
        for local, imported in facts.imports.items()
        if local == prefix
    ) and not _scope_rebinds(function, prefix)


def check_parameter_mutation(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: list[ast.AST],
    qualname: str,
) -> list[Finding]:
    """
    检查输入参数和跨作用域状态修改。

    Args:
        facts: 当前模块静态事实。
        node: 当前函数定义。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        参数修改相关发现列表。
    """
    parameter_names = {argument.arg for argument in all_parameters(node)}
    # A direct class-body method's first positional formal may be a receiver.
    # Without descriptor-binding evidence, even decorated methods cannot
    # justify classifying this formal as an ordinary external input.
    if facts.tree is not None and any(
        isinstance(owner, ast.ClassDef) and node in owner.body
        for owner in ast.walk(facts.tree)
    ):
        positional = (*node.args.posonlyargs, *node.args.args)
        if positional:
            parameter_names.discard(positional[0].arg)
    mutated: set[str] = set()
    findings: list[Finding] = []
    for item in nodes:
        if isinstance(item, (ast.Global, ast.Nonlocal)):
            is_global = isinstance(item, ast.Global)
            findings.append(
                facts.make_finding(
                    item,
                    "QG051",
                    (
                        f"`{qualname}` 声明 global 并修改模块共享状态。"
                        if is_global
                        else f"`{qualname}` 声明 nonlocal 并修改闭包状态。"
                    ),
                    symbol=qualname,
                    severity="warning" if is_global else "info",
                    confidence="high" if is_global else "medium",
                    suggestion=(
                        "把模块共享状态所有权放入明确对象或返回值。"
                        if is_global
                        else "确认闭包状态仅服务于局部算法，且不会作为隐藏回调副作用逃逸。"
                    ),
                )
            )
        mutated.update(mutated_parameter_names(item, parameter_names))
    readonly_annotations = {
        parameter.arg: ast.unparse(parameter.annotation)
        for parameter in all_parameters(node)
        if parameter.annotation is not None
        and _declares_readonly_input(facts, node, parameter.annotation)
    }
    for name in sorted(mutated & readonly_annotations.keys()):
        findings.append(
            facts.make_finding(
                node,
                "QG050",
                f"`{qualname}` 修改声明为只读的参数 `{name}`。",
                symbol=qualname,
                severity="warning",
                confidence="high",
                suggestion="遵循显式只读类型契约，避免向入参原位写入。",
                evidence={"parameter": name, "readonly_annotation": readonly_annotations[name]},
            )
        )
    return findings


def check_exception_flow(
    facts: ModuleFacts, nodes: list[ast.AST], qualname: str
) -> list[Finding]:
    """
    检查异常处理后继续执行和异常链丢失。

    Args:
        facts: 当前模块静态事实。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        异常流程发现列表。
    """
    findings: list[Finding] = []
    for item in nodes:
        if not isinstance(item, ast.ExceptHandler):
            continue
        if handler_logs_without_raising(item):
            findings.append(
                facts.make_finding(
                    item,
                    "QG048",
                    "异常被记录后继续运行，失败状态可能被伪装成成功路径。",
                    symbol=qualname,
                    severity="error",
                    suggestion="重新抛出异常或返回明确失败结构。",
                )
            )
        if translates_exception_without_cause(item):
            findings.append(
                facts.make_finding(
                    item,
                    "QG049",
                    "捕获异常后重新包装，但没有使用 `raise ... from error`。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="保留原始异常作为 cause，或不做无价值异常翻译。",
                )
            )
    return findings


def check_print_calls(
    facts: ModuleFacts, nodes: list[ast.AST], qualname: str
) -> list[Finding]:
    """
    检查生产函数中的 print() 调用。

    Args:
        facts: 当前模块静态事实。
        nodes: 当前函数直接拥有的 AST 节点。
        qualname: 函数限定名称。

    Returns:
        print() 调用发现列表。
    """
    return [
        facts.make_finding(
            item,
            "QG052",
            "生产代码中使用 print()，输出无法进入统一日志和 Trace。",
            symbol=qualname,
            severity="warning",
            suggestion="使用项目日志系统并选择正确级别。",
        )
        for item in nodes
        if isinstance(item, ast.Call) and call_name(item) == "print"
    ]


def mutated_parameter_names(node: ast.AST, parameters: set[str]) -> set[str]:
    """
    提取单个节点中被原地修改的参数名称。

    Args:
        node: 待检查节点。
        parameters: 当前函数参数名称集合。

    Returns:
        命中原地修改的参数名称集合。
    """
    result: set[str] = set()
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, (ast.Attribute, ast.Subscript)):
                continue
            base = mutation_base_name(target)
            if base in parameters:
                result.add(base)
    if isinstance(node, ast.AugAssign):
        # A bare parameter name is rebound after augmented assignment. Whether
        # __iadd__ also mutates the caller's object requires a resolved type
        # and operator implementation; syntax alone cannot prove that effect.
        # An attribute/subscript target, by contrast, writes through the
        # original reference and is a valid source-level state-write witness.
        if isinstance(node.target, (ast.Attribute, ast.Subscript)):
            base = mutation_base_name(node.target)
            if base in parameters:
                result.add(base)
    return result


def mutation_base_name(node: ast.expr) -> str:
    """
    提取赋值目标所属的根变量名。

    Args:
        node: 赋值目标表达式。

    Returns:
        根名称；无法确定时返回空字符串。
    """
    current: ast.expr = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


def handler_logs_without_raising(handler: ast.ExceptHandler) -> bool:
    """
    判断异常处理块是否只记录后继续执行。

    Args:
        handler: 异常处理节点。

    Returns:
        处理块包含日志且没有 raise/return 时返回 True。
    """
    has_log = any(
        isinstance(item, ast.Call)
        and (
            call_name(item).startswith(("logger.", "logging."))
            or call_name(item) == "print"
        )
        for statement in handler.body
        for item in ast.walk(statement)
    )
    terminates = any(
        isinstance(item, (ast.Raise, ast.Return))
        for statement in handler.body
        for item in ast.walk(statement)
    )
    return has_log and not terminates


def translates_exception_without_cause(handler: ast.ExceptHandler) -> bool:
    """
    判断异常处理块是否丢失异常链地重新抛出新异常。

    Args:
        handler: 异常处理节点。

    Returns:
        存在 `raise NewError(...)` 且没有 cause 时返回 True。
    """
    for statement in handler.body:
        for item in ast.walk(statement):
            if (
                isinstance(item, ast.Raise)
                and item.exc is not None
                and item.cause is None
            ):
                return True
    return False
