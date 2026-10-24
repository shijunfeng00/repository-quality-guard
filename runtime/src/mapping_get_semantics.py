"""Owner-specific source-bound mapping-get analysis extracted from AST collection.

This module owns the existing mapping contract classifier; the AST collector
retains traversal and finding emission responsibilities without a parallel graph.
"""

from __future__ import annotations

import ast
from typing import Protocol
from .ast_utils import annotation_is_mapping, dotted_name


class MappingGetSyntax(Protocol):
    """Expose only the module AST needed to classify an authored call.

    This small structural contract prevents the analyzer from importing its
    caller implementation, avoiding an unnecessary cross-module dependency.
    """

    tree: ast.Module | None


class MappingGetOwner(Protocol):
    """Provide lexical evidence from the active AST collection owner.

    Consumers read this live state but never mutate traversal, scopes or AST.
    This preserves a single ownership and scanning path for each module.
    """

    facts: MappingGetSyntax
    local_classes: dict[str, ast.ClassDef]
    class_stack: list[str]
    function_node_stack: list[ast.FunctionDef | ast.AsyncFunctionDef]

    @property
    def annotations(self) -> dict[str, str]:
        """Return current lexical parameter type annotations.

        Returns:
            Source-declared type annotations for the current scope.
        """
        ...


class MappingGetSemantics:
    """Source-bound .get owner and empty-result proof for the active visitor.

    The analyzer shares its visitor's immutable module AST; no second repository
    parser or alternate fact graph is constructed.
    """

    def __init__(self, owner: MappingGetOwner) -> None:
        """Bind lexical facts to this source-bound analyzer.

        Args:
            owner: Visitor exposing the authoritative module and lexical scopes.

        Returns:
            None; the analyzer only retains this visitor's stable state.
        """
        self.owner = owner
        self._parents: dict[ast.AST, ast.AST] | None = None

    def _mapping_class_or_method(self, class_name: str) -> str:
        """Inspect local class hierarchy before attributing get to Mapping.

        External unresolved ancestors never provide a negative proof.
        """
        visited: set[str] = set()

        def resolve(name: str) -> str:
            """Resolve a local class or base without assuming its runtime MRO.

            Args:
                name: Name of the authored local class or ancestor.

            Returns:
                Mapping, method, or unknown source-bound owner classification.
            """
            if name in visited:
                return "unknown"
            visited.add(name)
            if name in {"dict", "Dict", "Mapping", "MutableMapping", "TypedDict"}:
                return "mapping"
            definition = self.owner.local_classes.get(name)
            if definition is None:
                return "unknown"
            parents = [
                dotted_name(base.value if isinstance(base, ast.Subscript) else base)
                for base in definition.bases
            ]
            inherited = [resolve(parent.split(".")[-1]) for parent in parents]
            if "mapping" in inherited and "method" in inherited:
                return "unknown"  # Unproved multiple-inheritance method resolution.
            if "mapping" in inherited:
                return "mapping"
            if (
                any(
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "get"
                    for item in definition.body
                )
                or "method" in inherited
            ):
                return "method"
            return "unknown"

        return resolve(class_name)

    def _constructor_field_owner(
        self, method: ast.FunctionDef | ast.AsyncFunctionDef, field: str
    ) -> str | None:
        """Classify the first definite constructor write, or report no such write."""
        for statement in ast.walk(method):
            if not isinstance(statement, (ast.AnnAssign, ast.Assign)):
                continue
            targets = (
                (statement.target,)
                if isinstance(statement, ast.AnnAssign)
                else statement.targets
            )
            if not any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == field
                for target in targets
            ):
                continue
            value = statement.value
            if (
                (
                    isinstance(statement, ast.AnnAssign)
                    and (
                        annotation_is_mapping(statement.annotation)
                        or self._mapping_class_or_method(
                            dotted_name(statement.annotation).split(".")[-1]
                        )
                        == "mapping"
                    )
                )
                or isinstance(value, (ast.Dict, ast.DictComp))
                or (
                    isinstance(value, ast.Call)
                    and dotted_name(value.func) in {"dict", "defaultdict"}
                )
            ):
                return "mapping"
            if isinstance(value, ast.Call):
                return self._mapping_class_or_method(
                    dotted_name(value.func).split(".")[-1]
                )
            if isinstance(value, ast.Name):
                assignments = [
                    item.value
                    for item in ast.walk(method)
                    if isinstance(item, (ast.Assign, ast.AnnAssign))
                    and any(
                        isinstance(target, ast.Name) and target.id == value.id
                        for target in (
                            (item.target,)
                            if isinstance(item, ast.AnnAssign)
                            else item.targets
                        )
                    )
                ]
                if len(assignments) == 1 and isinstance(
                    assignments[0], (ast.Dict, ast.DictComp)
                ):
                    return "mapping"
            return "unknown"
        return None

    def _instance_field_owner(self, class_name: str, field: str) -> str:
        """Resolve explicitly constructed self fields without guessing their name."""
        visited: set[str] = set()
        while class_name in self.owner.local_classes and class_name not in visited:
            visited.add(class_name)
            owner = self.owner.local_classes[class_name]
            for method in owner.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if method.name not in {"__init__", "__post_init__"}:
                    continue
                found = self._constructor_field_owner(method, field)
                if found is not None:
                    return found
            parents = [
                dotted_name(base.value if isinstance(base, ast.Subscript) else base)
                for base in owner.bases
            ]
            class_name = parents[0].split(".")[-1] if len(parents) == 1 else ""
        return "unknown"

    def rebound_formal_receiver(self, receiver_name: str) -> bool:
        """Determine whether an authored formal receiver has local rebindings.

        Args:
            receiver_name: Source name of a candidate typed local or parameter.

        Returns:
            True when a local store/delete invalidates the original annotation;
            False if no invalidating write has been observed in the active scope.
        """
        if not receiver_name.isidentifier() or not self.owner.function_node_stack:
            return False
        if receiver_name not in self.owner.annotations:
            return False
        function = self.owner.function_node_stack[-1]
        return any(
            isinstance(item, ast.Name)
            and item.id == receiver_name
            and isinstance(item.ctx, (ast.Store, ast.Del))
            for item in ast.walk(function)
        )

    def local_get_owner(self, receiver_name: str) -> str:
        """Classify a local get receiver using authored owners and constructors.

        Args:
            receiver_name: Source-level receiver of the observed get call.

        Returns:
            Mapping, local method, or unresolved owner classification.
        """
        root, _, field = receiver_name.partition(".")
        if root in {"self", "cls"} and self.owner.class_stack:
            class_name = self.owner.class_stack[-1]
            return (
                self._instance_field_owner(class_name, field)
                if field
                else self._mapping_class_or_method(class_name)
            )
        if field:
            return "unknown"
        class_name = (
            ""
            if self.rebound_formal_receiver(root)
            else self.owner.annotations.get(root, "").split(".")[-1]
        )
        if not class_name and self.owner.function_node_stack:
            declarations = [
                item.value
                for item in ast.walk(self.owner.function_node_stack[-1])
                if isinstance(item, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == root
                    for target in (
                        (item.target,)
                        if isinstance(item, ast.AnnAssign)
                        else item.targets
                    )
                )
            ]
            if len(declarations) == 1 and isinstance(declarations[0], ast.Call):
                class_name = dotted_name(declarations[0].func).split(".")[-1]
        return self._mapping_class_or_method(class_name) if class_name else "unknown"

    def _self_field_type(self, field: str) -> str:
        """Read one authored self.field annotation from constructor source."""
        if not self.owner.class_stack:
            return ""
        owner = self.owner.local_classes.get(self.owner.class_stack[-1])
        if owner is None:
            return ""
        for method in owner.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if method.name not in {"__init__", "__post_init__"}:
                continue
            for statement in ast.walk(method):
                if not isinstance(statement, ast.AnnAssign):
                    continue
                target = statement.target
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr == field
                ):
                    return dotted_name(statement.annotation).split(".")[-1]
        return ""

    def source_bound_receiver(self, receiver_name: str, node: ast.Call) -> str:
        """Resolve a uniquely assigned local alias to an annotated source.

        Only straight-line single assignments with no competing local writes
        provide type ownership evidence; unresolved paths keep their identity.

        Args:
            receiver_name: Lexical receiver of the observed mapping call.
            node: Exact call source position that must follow the assignments.

        Returns:
            Authored annotated receiver identity when its alias chain is proven;
            otherwise the original receiver without guessed type ownership.
        """
        if not receiver_name.isidentifier() or not self.owner.function_node_stack:
            return receiver_name
        function = self.owner.function_node_stack[-1]
        writes = [
            part.id
            for part in ast.walk(function)
            if isinstance(part, ast.Name) and isinstance(part.ctx, (ast.Store, ast.Del))
        ]
        current = receiver_name
        seen: set[str] = set()
        while current not in seen:
            seen.add(current)
            if current in self.owner.annotations:
                return current if writes.count(current) == 0 else receiver_name
            if writes.count(current) != 1:
                break
            assigned = [
                stmt
                for stmt in function.body
                if isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == current
                and stmt.lineno < node.lineno
                and isinstance(stmt.value, (ast.Name, ast.Attribute))
            ]
            if len(assigned) != 1:
                break
            source = dotted_name(assigned[0].value)
            if source.startswith("self."):
                return source if writes.count("self") == 0 else receiver_name
            if not source.isidentifier():
                break
            current = source
        return receiver_name

    def schema_key_requirement(self, receiver_name: str, selector: str) -> bool | None:
        """Read a local TypedDict field's statically declared requirement.

        Args:
            receiver_name: Receiver whose schema must be locally resolved.
            selector: Literal field name observed by the actual mapping read.

        Returns:
            True for a mandatory field, False for an explicitly optional
            field, and None when neither can be proved from the declaration.
        """
        if not selector:
            return None
        root, _, field = receiver_name.partition(".")
        name = (
            self._self_field_type(field)
            if root == "self" and field
            else (
                ""
                if self.rebound_formal_receiver(root)
                else self.owner.annotations.get(root, "").split(".")[-1]
            )
        )
        definition = self.owner.local_classes.get(name)
        if definition is None or not any(
            self.owner.facts.imports.get(dotted_name(base).split(".")[0], "")
            + (
                "." + dotted_name(base).split(".", 1)[1]
                if "." in dotted_name(base)
                else ""
            )
            in {"typing.TypedDict", "typing_extensions.TypedDict"}
            for base in definition.bases
        ):
            return None
        total = True
        for keyword in definition.keywords:
            if keyword.arg == "total":
                if not isinstance(keyword.value, ast.Constant) or not isinstance(
                    keyword.value.value, bool
                ):
                    return None
                total = keyword.value.value
        for member in definition.body:
            if not isinstance(member, ast.AnnAssign):
                continue
            if not isinstance(member.target, ast.Name) or member.target.id != selector:
                continue
            annotation = member.annotation
            if isinstance(annotation, ast.Subscript):
                marker = dotted_name(annotation.value)
                name, _, tail = marker.partition(".")
                qualified = self.owner.facts.imports.get(name, "")
                if tail:
                    qualified += "." + tail
                if qualified in {"typing.NotRequired", "typing_extensions.NotRequired"}:
                    return False
                if qualified in {"typing.Required", "typing_extensions.Required"}:
                    return True
                if marker.split(".")[-1] in {"NotRequired", "Required"}:
                    return None
            return total
        return None

    def established_key_guard(
        self, receiver_name: str, selector: str, node: ast.Call
    ) -> bool:
        """Prove a missing-key guard aborts immediately before a direct return.

        Args:
            receiver_name: Identity used by the guard and subsequent read.
            selector: Exact literal key checked before the read.
            node: Source-bound mapping call directly returned after the guard.

        Returns:
            True only when an adjacent top-level missing-key branch raises
            before that exact read, without an intervening mutation path.
        """
        if not self.owner.function_node_stack or not selector:
            return False
        function = self.owner.function_node_stack[-1]
        # This bounded proof accepts no intervening statements or receiver writes.
        if receiver_name.isidentifier() and any(
            isinstance(item, ast.Name)
            and item.id == receiver_name
            and isinstance(item.ctx, (ast.Store, ast.Del))
            for item in ast.walk(function)
        ):
            return False
        for guard, following in zip(function.body, function.body[1:]):
            if (
                isinstance(guard, ast.If)
                and not guard.orelse
                and len(guard.body) == 1
                and isinstance(guard.body[0], ast.Raise)
                and isinstance(following, ast.Return)
                and following.value is node
                and isinstance(guard.test, ast.Compare)
                and len(guard.test.ops) == 1
                and isinstance(guard.test.ops[0], ast.NotIn)
                and isinstance(guard.test.left, ast.Constant)
                and guard.test.left.value == selector
                and len(guard.test.comparators) == 1
                and dotted_name(guard.test.comparators[0]) == receiver_name
            ):
                return True
        return False

    def required_schema_key(self, receiver_name: str, selector: str) -> bool:
        """Determine whether a named TypedDict field is contractually required.

        Args:
            receiver_name: Field access receiver traced from the caller.
            selector: Statical literal field name on that receiver.

        Returns:
            True only when authored schema marks the field required.
        """
        return self.schema_key_requirement(receiver_name, selector) is True

    def _sparse_constructor_empty_case(self, receiver_name: str) -> bool:
        """Prove a local field is populated from a sparse, edge-driven builder.

        Only an empty literal producer and setdefault inside an authored for-loop
        count; an index initialized with all keys or a foreign provider is unknown.
        """
        if not receiver_name.startswith("self.") or not self.owner.class_stack:
            return False
        field = receiver_name.split(".", 1)[1]
        owner = self.owner.local_classes.get(self.owner.class_stack[-1])
        if owner is None:
            return False
        for method in owner.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if method.name not in {"__init__", "__post_init__"}:
                continue
            for statement in ast.walk(method):
                if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                    continue
                target = statement.targets[0]
                if not (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr == field
                ):
                    continue
                source = statement.value
                if not isinstance(source, ast.Name):
                    continue
                initializers = [
                    node.value
                    for node in ast.walk(method)
                    if isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == source.id
                ]
                if len(initializers) != 1 or not isinstance(initializers[0], ast.Dict):
                    continue
                if initializers[0].keys:
                    continue
                return any(
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "setdefault"
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id == source.id
                    for loop in ast.walk(method)
                    if isinstance(loop, (ast.For, ast.AsyncFor))
                    for inner in ast.walk(loop)
                )
        return False

    def _is_direct_return(self, node: ast.Call) -> bool:
        """Check exact AST parent without retaining trees across repository scans."""
        if self._parents is None:
            self._parents = (
                {
                    child: parent
                    for parent in ast.walk(self.owner.facts.tree)
                    for child in ast.iter_child_nodes(parent)
                }
                if self.owner.facts.tree is not None
                else {}
            )
        # This call is taken from the active visitor AST; a missing parent
        # would violate the analyzer's own source-identity invariant.
        parent = self._parents[node]
        return isinstance(parent, ast.Return) and parent.value is node

    def _parent_index(self) -> dict[ast.AST, ast.AST]:
        """Return the parent relation of the already parsed source module."""
        if self._parents is None:
            tree = self.owner.facts.tree
            self._parents = (
                {
                    child: parent
                    for parent in ast.walk(tree)
                    for child in ast.iter_child_nodes(parent)
                }
                if tree is not None
                else {}
            )
        return self._parents

    @staticmethod
    def _none_comparison(test: ast.AST, binding: str) -> bool:
        """Prove an explicit None guard on the identified local binding."""
        return any(
            isinstance(expr, ast.Compare)
            and isinstance(expr.left, ast.Name)
            and expr.left.id == binding
            and len(expr.ops) == 1
            and isinstance(expr.ops[0], (ast.Is, ast.IsNot))
            and len(expr.comparators) == 1
            and isinstance(expr.comparators[0], ast.Constant)
            and expr.comparators[0].value is None
            for expr in ast.walk(test)
        )

    @staticmethod
    def _has_meaningful_branch(branch: list[ast.stmt]) -> bool:
        """Reject a no-op guard masquerading as missing-value handling."""
        return any(
            not isinstance(item, (ast.Pass, ast.Expr))
            or (isinstance(item, ast.Expr) and isinstance(item.value, ast.Call))
            for item in branch
        )

    def _guarded_result(self, node: ast.Call) -> bool:
        """Check a local call result is guarded before downstream consumption."""
        if not self.owner.function_node_stack:
            return False
        parents = self._parent_index()
        current: ast.AST = node
        while current in parents and not isinstance(parents[current], ast.Assign):
            current = parents[current]
        assignment = parents.get(current)
        if (
            not isinstance(assignment, ast.Assign)
            or len(assignment.targets) != 1
            or not isinstance(assignment.targets[0], ast.Name)
        ):
            return False
        binding = assignment.targets[0].id
        function = self.owner.function_node_stack[-1]
        # Source order is mandatory; a later check cannot repair an earlier use.
        for index, statement in enumerate(function.body):
            if statement is not assignment:
                continue
            for successor in function.body[index + 1 :]:
                if isinstance(successor, ast.Assign) and all(
                    isinstance(target, ast.Name) and target.id != binding
                    for target in successor.targets
                ):
                    continue
                return (
                    isinstance(successor, ast.If)
                    and self._none_comparison(successor.test, binding)
                    and self._has_meaningful_branch(successor.body)
                    and self._has_meaningful_branch(successor.orelse)
                )
        return False

    def _bounded_module_index(self, receiver: str) -> bool:
        """Prove a finite module index, not a required external input schema."""
        if not receiver.isidentifier() or self.owner.facts.tree is None:
            return False
        for declaration in self.owner.facts.tree.body:
            if isinstance(declaration, ast.AnnAssign):
                target, source = declaration.target, declaration.value
            elif isinstance(declaration, ast.Assign) and len(declaration.targets) == 1:
                target, source = declaration.targets[0], declaration.value
            else:
                continue
            if not isinstance(target, ast.Name) or target.id != receiver:
                continue
            if isinstance(source, ast.Dict):
                return bool(source.keys) and all(
                    isinstance(key, ast.Constant) for key in source.keys
                )
            return (
                isinstance(source, ast.DictComp)
                and len(source.generators) == 1
                and isinstance(
                    source.generators[0].iter, (ast.List, ast.Tuple, ast.Set)
                )
                and not source.generators[0].ifs
            )
        return False

    def _caller_guard(self, call: ast.Call) -> bool:
        """Prove one resolved local return is tested before any consumption."""
        parents = self._parent_index()
        child: ast.AST = call
        while child in parents and not isinstance(parents[child], ast.Assign):
            child = parents[child]
        assigned = parents.get(child)
        if (
            not isinstance(assigned, ast.Assign)
            or len(assigned.targets) != 1
            or not isinstance(assigned.targets[0], ast.Name)
            or assigned.value is not call
        ):
            return False
        name = assigned.targets[0].id
        parent: ast.AST = assigned
        while parent in parents and not isinstance(parents[parent], ast.FunctionDef):
            parent = parents[parent]
        function = parents.get(parent)
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        if assigned not in function.body:
            return False
        for statement in function.body[function.body.index(assigned) + 1 :]:
            if isinstance(statement, ast.Assign) and not any(
                isinstance(node, ast.Name) and node.id == name
                for node in ast.walk(statement.value)
            ):
                continue
            return (
                isinstance(statement, ast.If)
                and self._none_comparison(statement.test, name)
                and any(
                    isinstance(node, (ast.Return, ast.Raise))
                    for node in ast.walk(statement)
                )
            )
        return False

    def _private_optional_consumer_proof(self, node: ast.Call) -> bool:
        """Require every visible direct caller to handle absent results explicitly."""
        if not self.owner.function_node_stack or self.owner.facts.tree is None:
            return False
        function = self.owner.function_node_stack[-1]
        if not function.name.startswith("_") or function.returns is None:
            return False
        annotation = ast.unparse(function.returns)
        if "None" not in annotation or not (
            "|" in annotation or "Optional" in annotation
        ):
            return False
        if not self._is_direct_return(node):
            return False
        # A second unguarded consumer invalidates the entire negative proof.
        direct_calls = [
            call
            for call in ast.walk(self.owner.facts.tree)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == function.name
        ]
        return bool(direct_calls) and all(
            self._caller_guard(call) for call in direct_calls
        )

    def proven_missing_result(self, node: ast.Call, receiver: str) -> bool:
        """Admit only a source-proven absent-key business branch.

        Args:
            node: Actual Mapping.get call from the active parsed module.
            receiver: Source-bound mapping receiver, never a semantic name guess.

        Returns:
            Whether a bounded lookup or sparse index is demonstrably handled.
        """
        if not self.owner.function_node_stack or len(node.args) != 1 or node.keywords:
            return False
        selector = node.args[0].value if isinstance(node.args[0], ast.Constant) else ""
        if isinstance(selector, str) and self.required_schema_key(receiver, selector):
            return False
        if (
            isinstance(node.func, ast.Attribute)
            and self._bounded_module_index(receiver)
            and not isinstance(node.args[0], ast.Constant)
            and self._private_optional_consumer_proof(node)
        ):
            return True
        if not receiver.startswith("self.") or not self.owner.class_stack:
            return False
        field = receiver.partition(".")[2]
        if self._instance_field_owner(self.owner.class_stack[-1], field) != "mapping":
            return False
        owner = self.owner.local_classes[self.owner.class_stack[-1]]
        # Index is constructed incrementally rather than being a fixed required schema.
        sparse_source = any(
            isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
            and method.name in {"__init__", "__post_init__"}
            and any(
                isinstance(item, (ast.For, ast.AsyncFor))
                and any(
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and isinstance(target.value.value, ast.Name)
                    and target.value.value.id == "self"
                    and target.value.attr == field
                    for inner in ast.walk(item)
                    for target in (
                        inner.targets
                        if isinstance(inner, ast.Assign)
                        else (inner.target,)
                        if isinstance(inner, ast.AnnAssign)
                        else ()
                    )
                )
                for item in ast.walk(method)
            )
            for method in owner.body
        )
        return sparse_source and self._guarded_result(node)

    def explicit_empty_return(self, node: ast.Call) -> str:
        """Recognize a direct, declared empty return supported by source facts.

        Args:
            node: Mapped AST call inside the active owner scope.

        Returns:
            Optional None, sparse empty, or empty marker; otherwise empty.
        """
        if (
            len(node.args) == 1
            and not node.keywords
            and isinstance(node.func, ast.Attribute)
            and self.proven_missing_result(node, dotted_name(node.func.value))
        ):
            return "proven_missing"
        if (
            not self.owner.function_node_stack
            or len(node.args) != 2
            or node.keywords
            or self.owner.facts.tree is None
            or not self._is_direct_return(node)
        ):
            return ""
        function = self.owner.function_node_stack[-1]
        annotation = function.returns
        if annotation is None:
            return ""
        default = node.args[1]
        if isinstance(default, ast.Constant) and default.value is None:
            if isinstance(annotation, ast.Subscript) and dotted_name(
                annotation.value
            ).split(".")[-1] in {"Optional", "Union"}:
                return "optional_none"
            if isinstance(annotation, ast.BinOp) and isinstance(
                annotation.op, ast.BitOr
            ):
                if any(
                    isinstance(part, ast.Constant) and part.value is None
                    for part in (annotation.left, annotation.right)
                ):
                    return "optional_none"
        if isinstance(default, ast.List) and not default.elts:
            if (
                isinstance(annotation, ast.Subscript)
                and dotted_name(annotation.value).split(".")[-1] in {"list", "List"}
                and isinstance(node.func, ast.Attribute)
                and self._sparse_constructor_empty_case(dotted_name(node.func.value))
            ):
                return "sparse_empty_list"
        if isinstance(default, ast.Constant) and default.value == "":
            if (
                dotted_name(annotation) == "str"
                and isinstance(node.func, ast.Attribute)
                and self._sparse_constructor_empty_case(dotted_name(node.func.value))
            ):
                return "sparse_empty_string"
        return ""
