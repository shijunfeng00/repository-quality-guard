from __future__ import annotations

import ast
import re
from collections import defaultdict

from .ast_utils import dotted_name
from .config import GuardConfig
from .facts import ModuleFacts
from .model import Finding
from .policy_common import (
    BLOCKING_CALL_PREFIXES,
    BOOL_INT_VALUES,
    BOOL_TEXT_VALUES,
    NETWORK_CALL_PREFIXES,
    FunctionSignature,
    ParsedModule,
    all_parameters,
    annotation_names,
    call_name,
    direct_body_nodes,
    is_bool_literal,
    is_bool_name,
    is_boundary_module,
    literal_value,
    make_finding,
    parameter_defaults,
)


def check_signature_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    config: GuardConfig,
) -> list[Finding]:
    """
    检查布尔输入、变长参数和关键字参数袋。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。
        config: 当前仓库质量配置。

    Returns:
        签名相关发现列表。
    """
    findings = check_parameter_defaults(facts, node, qualname)
    findings.extend(check_star_signature(facts, node, qualname))
    findings.extend(check_kwargs_contract(facts, node, qualname))
    findings.extend(check_deleted_parameters(facts, node, qualname))
    if not is_boundary_module(facts.module, config.boundary_module_markers):
        findings.extend(check_boolean_coercion(facts, node, qualname))
    return findings


def check_parameter_defaults(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
) -> list[Finding]:
    """
    检查布尔参数表示、可变默认值和可空标注。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。

    Returns:
        参数默认值相关发现列表。
    """
    findings: list[Finding] = []
    defaults = parameter_defaults(node)
    for argument in all_parameters(node):
        if argument.arg in {"self", "cls"}:
            continue
        names = annotation_names(argument.annotation)
        default = defaults[argument.arg]
        value = literal_value(default)
        bool_like = is_bool_name(argument.arg) or "bool" in names
        if bool_like and (
            (type(value) is str and value in BOOL_TEXT_VALUES)
            or (type(value) is int and value in BOOL_INT_VALUES)
        ):
            findings.append(
                make_finding(
                    facts,
                    argument,
                    "QG031",
                    f"布尔参数 `{argument.arg}` 使用非 bool 默认值 {value!r}。",
                    symbol=qualname,
                    severity="error",
                    suggestion="内部只使用 False/True；0/1 和文本转换放在输入边界。",
                    evidence={"parameter": argument.arg, "default": value},
                )
            )
        if "bool" in names and names & {"str", "int"}:
            findings.append(
                make_finding(
                    facts,
                    argument,
                    "QG032",
                    f"布尔参数 `{argument.arg}` 同时允许 {sorted(names & {'str', 'int'})}。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="内部签名固定为 bool；多种外部表示只解析一次。",
                    evidence={"parameter": argument.arg, "annotations": sorted(names)},
                )
            )
        findings.extend(check_single_default(facts, argument, default, qualname))
    return findings


def check_single_default(
    facts: ModuleFacts,
    argument: ast.arg,
    default: ast.expr | None,
    qualname: str,
) -> list[Finding]:
    """
    检查单个参数的可变默认值和 None 标注。

    Args:
        facts: 当前模块静态事实。
        argument: 参数节点。
        default: 参数默认值。
        qualname: 函数限定名称。

    Returns:
        当前参数默认值发现列表。
    """
    findings: list[Finding] = []
    if default is not None and isinstance(default, (ast.List, ast.Dict, ast.Set)):
        findings.append(
            make_finding(
                facts,
                default,
                "QG039",
                f"参数 `{argument.arg}` 使用可变默认值。",
                symbol=qualname,
                severity="error",
                suggestion="使用 None 后显式创建，或使用不可变默认值。",
                evidence={"parameter": argument.arg},
            )
        )
    if (
        isinstance(default, ast.Constant)
        and default.value is None
        and argument.annotation is not None
        and not annotation_allows_none(argument.annotation)
    ):
        findings.append(
            make_finding(
                facts,
                argument,
                "QG040",
                f"参数 `{argument.arg}` 默认 None，但类型标注未声明可空。",
                symbol=qualname,
                severity="warning",
                suggestion="必填参数取消默认值；真正可空时显式标注 None。",
                evidence={"parameter": argument.arg},
            )
        )
    return findings


def annotation_allows_none(annotation: ast.expr) -> bool:
    """
    判断类型标注是否显式包含 None。

    Args:
        annotation: 待分析的类型标注。

    Returns:
        标注包含 None、Optional 或 NoneType 时返回 True。
    """
    return bool(annotation_names(annotation) & {"None", "NoneType", "Optional"}) or any(
        isinstance(item, ast.Constant) and item.value is None
        for item in ast.walk(annotation)
    )


_KWARGS_CONTRACT_PATTERN = re.compile(
    r"\s*#\s*RQG-KWARGS:\s*keys="
    r"([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)"
    r"\s*;\s*mode=forward-only\s*"
)


def check_star_signature(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
) -> list[Finding]:
    """检查 bare ``*`` 与 ``*args`` 特殊函数签名。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。

    Returns:
        QG034 发现列表；普通固定参数签名返回空列表。
    """
    argument = node.args.vararg
    if argument is not None:
        return [
            make_finding(
                facts,
                argument,
                "QG034",
                f"`{qualname}` 使用 *{argument.arg} 接受未逐项声明的可变位置参数。",
                symbol=qualname,
                severity="warning",
                confidence="high",
                suggestion=(
                    "生产接口优先使用固定显式参数；不要用 *args 吸收调用方参数漂移。"
                ),
                evidence={"shape": "var_positional", "parameter": argument.arg},
            )
        ]
    if not node.args.kwonlyargs:
        return []
    keyword_names = [argument.arg for argument in node.args.kwonlyargs]
    return [
        make_finding(
            facts,
            node,
            "QG034",
            f"`{qualname}` 使用 bare `*` 引入 keyword-only 特殊调用契约：{keyword_names}。",
            symbol=qualname,
            severity="warning",
            confidence="high",
            suggestion=(
                "项目默认优先普通固定显式参数；参数总量过宽由 QG015 独立检查。"
            ),
            evidence={"shape": "bare_star", "keyword_only": keyword_names},
        )
    ]


def check_kwargs_contract(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
) -> list[Finding]:
    """检查 ``**kwargs`` 是否具有有限、透明、机器可验证的透传契约。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。

    Returns:
        开放或非透明 ``**kwargs`` 产生 QG042；有限纯透传契约返回空列表。
    """
    argument = node.args.kwarg
    if argument is None:
        return []
    annotation_keys = _typed_dict_contract_keys(facts, argument.annotation)
    comment_keys = _structured_kwargs_contract_keys(facts, node)
    contract_source = "typed-dict" if annotation_keys is not None else ""
    keys = annotation_keys
    if keys is None and comment_keys is not None:
        keys = comment_keys
        contract_source = "structured-comment"
    forward_only = _kwargs_is_transparent_forwarding(node, argument.arg)
    if keys is not None and forward_only:
        return []
    if keys is None:
        message = f"`{qualname}` 使用开放 **{argument.arg}，允许 key 集无法由静态契约完整枚举。"
        reason = "open-key-space"
    else:
        message = (
            f"`{qualname}` 为 **{argument.arg} 声明了有限 key {list(keys)}，"
            "但当前函数并非纯透明透传。"
        )
        reason = "bounded-but-not-forward-only"
    return [
        make_finding(
            facts,
            argument,
            "QG042",
            message,
            symbol=qualname,
            severity="warning",
            confidence="high",
            suggestion=(
                "优先改为固定显式参数；确需跨多层透传大量稳定参数时，使用 "
                "Unpack[TypedDict] 或结构化 RQG-KWARGS 有限 key 契约，并保持 kwargs 原样透传。"
            ),
            evidence={
                "shape": "var_keyword",
                "parameter": argument.arg,
                "contract_source": contract_source or "none",
                "allowed_keys": list(keys or ()),
                "forward_only": forward_only,
                "reason": reason,
            },
        )
    ]


def _typed_dict_contract_keys(
    facts: ModuleFacts,
    annotation: ast.expr | None,
) -> tuple[str, ...] | None:
    """解析同模块 class-style ``Unpack[TypedDict]`` 的有限 key 集。"""
    if not isinstance(annotation, ast.Subscript) or facts.tree is None:
        return None
    outer = dotted_name(annotation.value)
    target_name = dotted_name(annotation.slice)
    if (
        not outer
        or outer.rsplit(".", 1)[-1] != "Unpack"
        or not target_name
        or "." in target_name
    ):
        return None
    for statement in facts.tree.body:
        if not isinstance(statement, ast.ClassDef) or statement.name != target_name:
            continue
        bases = [dotted_name(base) for base in statement.bases]
        if not any(name and name.rsplit(".", 1)[-1] == "TypedDict" for name in bases):
            return None
        return tuple(
            item.target.id
            for item in statement.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        )
    return None


def _structured_kwargs_contract_keys(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ...] | None:
    """读取紧邻定义的结构化 ``RQG-KWARGS`` 有限 key 契约。"""
    if not facts.source:
        return None
    anchor = min(
        [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
    )
    if anchor <= 1:
        return None
    lines = facts.source.splitlines()
    raw = lines[anchor - 2]
    match = _KWARGS_CONTRACT_PATTERN.fullmatch(raw)
    if match is None:
        return None
    keys = tuple(item.strip() for item in match.group(1).split(","))
    if not keys or len(set(keys)) != len(keys):
        return None
    return keys


def _kwargs_is_transparent_forwarding(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> bool:
    """判断 kwargs 是否只作为 ``callee(**kwargs)`` 原样传给明确调用。"""
    nodes = direct_body_nodes(node)
    forwarded_loads = {
        id(keyword.value)
        for item in nodes
        if isinstance(item, ast.Call)
        for keyword in item.keywords
        if keyword.arg is None
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == name
    }
    if not forwarded_loads:
        return False
    if any(
        isinstance(item, ast.Name)
        and item.id == name
        and isinstance(item.ctx, (ast.Store, ast.Del))
        for item in nodes
    ):
        return False
    loads = [
        item
        for item in nodes
        if isinstance(item, ast.Name)
        and item.id == name
        and isinstance(item.ctx, ast.Load)
    ]
    return bool(loads) and all(id(item) in forwarded_loads for item in loads)


def check_deleted_parameters(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
) -> list[Finding]:
    """
    检查通过 del 参数掩盖未使用接口参数的做法。

    Args:
        facts: 当前模块静态事实。
        node: 待检查函数。
        qualname: 函数限定名称。

    Returns:
        被显式删除的参数发现列表。
    """
    parameters = {argument.arg for argument in all_parameters(node)}
    findings: list[Finding] = []
    for statement in node.body:
        if not isinstance(statement, ast.Delete):
            continue
        deleted = sorted(
            target.id
            for target in statement.targets
            if isinstance(target, ast.Name) and target.id in parameters
        )
        if not deleted:
            continue
        findings.append(
            make_finding(
                facts,
                statement,
                "QG145",
                f"`{qualname}` 通过 del 丢弃接口参数：{deleted}。",
                symbol=qualname,
                severity="warning",
                suggestion="删除无效参数并同步调用方；若由外部协议强制要求，明确标注 override 并解释。",
                evidence={"parameters": deleted},
            )
        )
    return findings


def is_protocol_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """
    判断函数是否明显属于 Python 或第三方协议重写入口。

    Args:
        node: 待检查函数。

    Returns:
        特殊方法或带 override/abstractmethod 装饰器时返回 True。
    """
    if node.name.startswith("__") and node.name.endswith("__"):
        return True
    decorators = {dotted_name(item).rsplit(".", 1)[-1] for item in node.decorator_list}
    return bool(decorators & {"override", "abstractmethod"})


def check_boolean_coercion(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
) -> list[Finding]:
    """
    检查内部函数对布尔值多种表示的解析和错误转换。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。

    Returns:
        布尔归一化相关发现列表。
    """
    parameters = {argument.arg for argument in all_parameters(node)}
    bool_parameters = {
        argument.arg
        for argument in all_parameters(node)
        if is_bool_name(argument.arg) or "bool" in annotation_names(argument.annotation)
    }
    findings: list[Finding] = []
    for item in direct_body_nodes(node):
        if isinstance(item, ast.Call) and call_name(item) == "bool" and item.args:
            target = dotted_name(item.args[0])
            if target in parameters:
                findings.append(
                    make_finding(
                        facts,
                        item,
                        "QG033",
                        f"内部函数对参数 `{target}` 调用 bool()；字符串 'False' 仍会得到 True。",
                        symbol=qualname,
                        severity="warning",
                        suggestion="边界层显式解析外部布尔表示，内部只接收 bool。",
                    )
                )
        if (
            isinstance(item, ast.Compare)
            and dotted_name(item.left) in bool_parameters
            and compare_contains_bool_representations(item)
        ):
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG033",
                    "内部函数同时识别 0/1 或 True/False 字符串等多种布尔表示。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="把多表示解析集中到外部输入边界，内部调用链统一使用 bool。",
                )
            )
    return findings


def compare_contains_bool_representations(node: ast.Compare) -> bool:
    """
    判断比较表达式是否枚举多种布尔表示。

    Args:
        node: 比较表达式。

    Returns:
        比较值包含文本布尔值或 0/1 时返回 True。
    """
    values: list[object] = []
    for comparator in node.comparators:
        candidates = (
            comparator.elts
            if isinstance(comparator, (ast.Set, ast.List, ast.Tuple))
            else [comparator]
        )
        values.extend(
            item.value for item in candidates if isinstance(item, ast.Constant)
        )
    text_values = {
        value.lower()
        for value in values
        if type(value) is str and value in BOOL_TEXT_VALUES
    }
    int_values = {
        value for value in values if type(value) is int and value in BOOL_INT_VALUES
    }
    return bool(text_values) or int_values == BOOL_INT_VALUES


def check_call_safety_rules(
    facts: ModuleFacts,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
) -> list[Finding]:
    """
    检查子进程、网络、动态执行和异步阻塞调用。

    Args:
        facts: 当前模块静态事实。
        node: 待检查的函数定义。
        qualname: 函数限定名称。

    Returns:
        调用安全性发现列表。
    """
    findings: list[Finding] = []
    for item in direct_body_nodes(node):
        if not isinstance(item, ast.Call):
            continue
        name = call_name(item)
        keyword_names = {keyword.arg for keyword in item.keywords}
        if name == "subprocess.run" and "check" not in keyword_names:
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG053",
                    "subprocess.run() 未显式设置 check=True。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="检查返回码或使用 check=True，禁止把命令失败伪装成成功。",
                )
            )
        if name.startswith(NETWORK_CALL_PREFIXES) and "timeout" not in keyword_names:
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG054",
                    f"网络调用 `{name}` 未显式设置 timeout。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="网络边界必须设置明确超时并传播失败。",
                )
            )
        if isinstance(node, ast.AsyncFunctionDef) and name.startswith(
            BLOCKING_CALL_PREFIXES
        ):
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG055",
                    f"异步函数中直接调用同步阻塞 API `{name}`。",
                    symbol=qualname,
                    severity="warning",
                    suggestion="使用异步客户端，或把阻塞操作显式移入线程/进程执行器。",
                )
            )
        if name in {"eval", "exec", "compile"}:
            findings.append(
                make_finding(
                    facts,
                    item,
                    "QG056",
                    f"使用动态代码执行 `{name}()`。",
                    symbol=qualname,
                    severity="error",
                    suggestion="使用显式解析器、分发表或受限 DSL，不要执行动态 Python。",
                )
            )
    return findings


def is_boolean_call_literal(node: ast.expr) -> bool:
    """
    判断调用实参是否为布尔值或历史兼容表示。

    Args:
        node: 调用实表达式。

    Returns:
        命中 False/True、0/1 或文本布尔值时返回 True。
    """
    if is_bool_literal(node):
        return True
    value = literal_value(node)
    return (type(value) is int and value in BOOL_INT_VALUES) or (
        type(value) is str and value in BOOL_TEXT_VALUES
    )


def check_positional_boolean_calls(
    parsed: list[ParsedModule],
    signatures: list[FunctionSignature],
) -> list[Finding]:
    """
    检查向已知布尔参数传递位置 bool 字面量的调用。

    Args:
        parsed: 已解析模块列表。
        signatures: 仓库函数签名列表。

    Returns:
        难以阅读的位置布尔实参发现列表。
    """
    by_name: dict[str, list[FunctionSignature]] = defaultdict(list)
    for signature in signatures:
        by_name[signature.name].append(signature)
    findings: list[Finding] = []
    for module in parsed:
        for item in ast.walk(module.tree):
            if not isinstance(item, ast.Call):
                continue
            target = dotted_name(item.func).rsplit(".", 1)[-1]
            candidates = by_name[target]
            if len(candidates) != 1:
                continue
            signature = candidates[0]
            bad_positions = [
                index
                for index, argument in enumerate(item.args)
                if index in signature.boolean_positions
                and is_boolean_call_literal(argument)
            ]
            if not bad_positions:
                continue
            findings.append(
                make_finding(
                    module.facts,
                    item,
                    "QG041",
                    f"调用 `{target}` 时以位置参数传递布尔值或兼容表示，语义不可读。",
                    symbol=module.facts.module,
                    severity="warning",
                    suggestion="内部仅传递 False/True，并使用显式关键字参数。",
                    evidence={"positions": bad_positions},
                )
            )
    return findings
