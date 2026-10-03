"""Clang-backed C++ provider for normalized repository topology facts."""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .multilang import clang_error_detail, clang_include_args, clang_line_range
from .topology_facts import (
    OwnerFact,
    OwnerKind,
    RepositoryTopology,
    SymbolFact,
    UsageEdge,
    UsageKind,
    Visibility,
)

_CPP_FUNCTION_RE = re.compile(
    r"(?P<kind>FunctionDecl|CXXMethodDecl|CXXConstructorDecl|CXXDestructorDecl)\s+"
    r"(?P<id>0x[0-9A-Fa-f]+)\s+<(?P<range>[^>]+)>\s+.*?"
    r"(?P<name>[~A-Za-z_][A-Za-z0-9_:~]*)\s+'(?P<sig>[^']+)'"
)
_CPP_NAMESPACE_RE = re.compile(
    r"NamespaceDecl\s+(?P<id>0x[0-9A-Fa-f]+)\s+<(?P<range>[^>]+)>\s+"
    r".*?\s(?P<name>[A-Za-z_][A-Za-z0-9_]*)$"
)
_CPP_RECORD_RE = re.compile(
    r"CXXRecordDecl\s+(?P<id>0x[0-9A-Fa-f]+)\s+<(?P<range>[^>]+)>\s+"
    r".*?\b(?P<tag>class|struct)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+definition$"
)
_CPP_FIELD_RE = re.compile(
    r"FieldDecl\s+(?P<id>0x[0-9A-Fa-f]+)\s+<(?P<range>[^>]+)>\s+"
    r".*?\s(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+'"
)
_CPP_ACCESS_RE = re.compile(
    r"AccessSpecDecl\s+0x[0-9A-Fa-f]+\s+<[^>]+>\s+.*?\b(public|protected|private)$"
)
_CPP_DECL_REF_RE = re.compile(
    r"DeclRefExpr\s+0x[0-9A-Fa-f]+\s+<[^>]+>\s+.*?\b"
    r"(?P<decl_kind>Function|CXXMethod|Var)\s+(?P<target>0x[0-9A-Fa-f]+)\s+'"
)
_CPP_LAMBDA_RE = re.compile(r"LambdaExpr\s+(?P<id>0x[0-9A-Fa-f]+)\s+<(?P<range>[^>]+)>")
_CPP_LAMBDA_LOCATION_RE = re.compile(
    r"\(lambda at (?P<path>.+):(?P<line>\d+):(?P<column>\d+)\)"
)
_CPP_VAR_RE = re.compile(
    r"VarDecl\s+(?P<id>0x[0-9A-Fa-f]+)\s+<(?P<range>[^>]+)>\s+.*?"
    r"\s(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+'"
)
_CPP_MEMBER_REF_RE = re.compile(
    r"MemberExpr\s+0x[0-9A-Fa-f]+\s+<[^>]+>\s+.*?"
    r"(?:->|\.)(?P<name>[~A-Za-z_][A-Za-z0-9_:~]*)\s+"
    r"(?P<target>0x[0-9A-Fa-f]+)$"
)
_CPP_OVERRIDE_RE = re.compile(r"Overrides:\s*\[\s*(?P<target>0x[0-9A-Fa-f]+)\b")
_CPP_CONCEPT_RE = re.compile(
    r"ConceptDecl\s+(?P<id>0x[0-9A-Fa-f]+)\s+<(?P<range>[^>]+)>\s+"
    r".*?\s(?P<name>[A-Za-z_][A-Za-z0-9_]*)$"
)
_CPP_CONCEPT_REF_RE = re.compile(
    r"ConceptSpecializationExpr\s+0x[0-9A-Fa-f]+\s+<[^>]+>\s+.*?\b"
    r"Concept\s+(?P<target>0x[0-9A-Fa-f]+)\s+'"
)

_CPP_CALLABLE_KIND = {
    "FunctionDecl": "function",
    "CXXMethodDecl": "method",
    "CXXConstructorDecl": "constructor",
    "CXXDestructorDecl": "destructor",
}


def _cpp_visibility(value: str) -> Visibility:
    """Map a Clang access specifier onto normalized visibility."""
    if value == "public":
        return Visibility.PUBLIC
    if value == "protected":
        return Visibility.PROTECTED
    if value == "private":
        return Visibility.PRIVATE
    return Visibility.UNKNOWN


def _cpp_project_path(
    candidate: Path | None,
    authored_paths: dict[Path, str],
) -> str | None:
    """Return repository-relative path only for files owned by the input snapshot."""
    if candidate is None:
        return None
    resolved = candidate.resolve()
    if resolved not in authored_paths:
        return None
    return authored_paths[resolved]


def _cpp_scope_qualname(stack: list[dict[str, Any]]) -> str:
    """Return the authored namespace/class lexical scope from an AST stack."""
    names = [
        str(item["name"])
        for item in stack
        if item["kind"] in {"namespace", "class"} and "name" in item and item["name"]
    ]
    return "::".join(names)


def _cpp_nearest(stack: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    """Return the nearest active AST context of ``kind``."""
    return next((item for item in reversed(stack) if item["kind"] == kind), None)


@dataclass(slots=True)
class _CppTopologyFacts:
    """Mutable compiler-proven facts accumulated across C++ translation units."""

    symbols: dict[str, SymbolFact] = field(default_factory=dict)
    owners: dict[str, OwnerFact] = field(default_factory=dict)
    edges: set[UsageEdge] = field(default_factory=set)
    clang_targets: dict[str, str] = field(default_factory=dict)
    pending_edges: list[tuple[str, str, UsageKind, Path, int, str]] = field(
        default_factory=list
    )
    pending_bases: list[tuple[str, str, Path, int]] = field(default_factory=list)
    class_by_qualname: dict[str, str] = field(default_factory=dict)
    authored_paths: dict[Path, str] = field(default_factory=dict)
    lambda_counts: defaultdict[tuple[str, int], int] = field(
        default_factory=lambda: defaultdict(int)
    )


@dataclass(slots=True)
class _CppAstCursor:
    """Per-translation-unit Clang AST traversal context."""

    stack: list[dict[str, Any]] = field(default_factory=list)
    location_file: Path | None = None


@dataclass(slots=True, frozen=True)
class _CppCallableIdentity:
    """Resolved normalized identity for one authored C++ callable declaration."""

    symbol_id: str
    owner_id: str
    owner_kind: OwnerKind
    visibility: Visibility
    qualname: str
    kind: str
    existing: SymbolFact | None = None


def _record_cpp_module_owner(facts: _CppTopologyFacts, path: str) -> str:
    """Record and return the normalized module owner for one authored C++ file."""
    owner_id = f"cpp:module:{path}"
    if owner_id not in facts.owners:
        facts.owners[owner_id] = OwnerFact(
            owner_id=owner_id,
            language="cpp",
            kind=OwnerKind.MODULE,
            path=Path(path),
            line=1,
        )
    return owner_id


def _consume_cpp_namespace_declaration(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
    column: int,
) -> bool:
    """Consume one authored namespace declaration when the AST line carries one."""
    namespace = _CPP_NAMESPACE_RE.search(text)
    if namespace is None or column < 0:
        return False
    location_file, start, _end = clang_line_range(
        namespace.group("range"), cursor.location_file
    )
    cursor.location_file = location_file
    path = _cpp_project_path(location_file, facts.authored_paths)
    context: dict[str, Any] = {
        "kind": "namespace",
        "column": column,
        "name": "",
        "owner_id": "",
    }
    if path is not None and start is not None:
        parent_scope = _cpp_scope_qualname(cursor.stack)
        name = namespace.group("name")
        qualname = f"{parent_scope}::{name}" if parent_scope else name
        owner_id = f"cpp:namespace:{qualname}"
        facts.owners[owner_id] = OwnerFact(
            owner_id=owner_id,
            language="cpp",
            kind=OwnerKind.NAMESPACE,
            path=Path(path),
            line=start,
        )
        context.update(name=name, owner_id=owner_id)
    cursor.stack.append(context)
    return True


def _consume_cpp_record_declaration(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
    column: int,
) -> bool:
    """Consume one authored class/struct definition and its architectural owner."""
    record = _CPP_RECORD_RE.search(text)
    if record is None or column < 0:
        return False
    location_file, start, end = clang_line_range(
        record.group("range"), cursor.location_file
    )
    cursor.location_file = location_file
    path = _cpp_project_path(location_file, facts.authored_paths)
    context: dict[str, Any] = {
        "kind": "class",
        "column": column,
        "name": "",
        "owner_id": "",
        "access": "private" if record.group("tag") == "class" else "public",
    }
    if path is not None and start is not None and end is not None:
        parent_scope = _cpp_scope_qualname(cursor.stack)
        name = record.group("name")
        qualname = f"{parent_scope}::{name}" if parent_scope else name
        owner_id = f"cpp:class:{qualname}"
        parent_owner = _cpp_nearest(cursor.stack, "namespace")
        if parent_owner is not None and parent_owner["owner_id"]:
            parent_owner_id = str(parent_owner["owner_id"])
            parent_kind = OwnerKind.NAMESPACE
        else:
            parent_owner_id = _record_cpp_module_owner(facts, path)
            parent_kind = OwnerKind.MODULE
        facts.owners[owner_id] = OwnerFact(
            owner_id=owner_id,
            language="cpp",
            kind=OwnerKind.CLASS,
            path=Path(path),
            line=start,
        )
        facts.symbols[owner_id] = SymbolFact(
            symbol_id=owner_id,
            language="cpp",
            kind="class",
            owner_id=parent_owner_id,
            owner_kind=parent_kind,
            visibility=Visibility.PUBLIC,
            path=Path(path),
            line=start,
            end_line=end,
            lines=end - start + 1,
            name=name,
            qualname=qualname,
        )
        facts.clang_targets[record.group("id")] = owner_id
        facts.class_by_qualname[qualname] = owner_id
        context.update(name=name, owner_id=owner_id, qualname=qualname)
    cursor.stack.append(context)
    return True


def _consume_cpp_access_declaration(
    cursor: _CppAstCursor,
    text: str,
) -> bool:
    """Consume an explicit C++ access-specifier node for the active class context."""
    access = _CPP_ACCESS_RE.search(text)
    if access is None:
        return False
    class_context = _cpp_nearest(cursor.stack, "class")
    if class_context is not None:
        class_context["access"] = access.group(1)
    return True


def _consume_cpp_concept_declaration(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
    column: int,
) -> bool:
    """Consume one authored named concept as normalized protocol evidence."""
    concept = _CPP_CONCEPT_RE.search(text)
    if concept is None or column < 0:
        return False
    location_file, start, end = clang_line_range(
        concept.group("range"), cursor.location_file
    )
    cursor.location_file = location_file
    path = _cpp_project_path(location_file, facts.authored_paths)
    context: dict[str, Any] = {
        "kind": "concept",
        "column": column,
        "name": "",
        "symbol_id": "",
    }
    if path is not None and start is not None and end is not None:
        scope = _cpp_scope_qualname(cursor.stack)
        name = concept.group("name")
        qualname = f"{scope}::{name}" if scope else name
        symbol_id = f"cpp:concept:{qualname}"
        owner = _cpp_nearest(cursor.stack, "namespace")
        if owner is not None and owner["owner_id"]:
            owner_id = str(owner["owner_id"])
            owner_kind = OwnerKind.NAMESPACE
        else:
            owner_id = _record_cpp_module_owner(facts, path)
            owner_kind = OwnerKind.MODULE
        facts.symbols[symbol_id] = SymbolFact(
            symbol_id=symbol_id,
            language="cpp",
            kind="protocol",
            owner_id=owner_id,
            owner_kind=owner_kind,
            visibility=Visibility.PUBLIC,
            path=Path(path),
            line=start,
            end_line=end,
            lines=end - start + 1,
            name=name,
            qualname=qualname,
        )
        facts.clang_targets[concept.group("id")] = symbol_id
        context.update(name=name, symbol_id=symbol_id)
    cursor.stack.append(context)
    return True


def _consume_cpp_template_declaration(
    cursor: _CppAstCursor,
    text: str,
    node: tuple[str, int] | None,
    column: int,
) -> bool:
    """Consume one function-template context from compiler AST evidence."""
    if node is None or node[0] != "FunctionTemplateDecl":
        return False
    range_match = re.search(r"<(?P<range>[^>]+)>", text)
    if range_match is not None:
        location_file, _start, _end = clang_line_range(
            range_match.group("range"), cursor.location_file
        )
        cursor.location_file = location_file
    cursor.stack.append(
        {
            "kind": "template",
            "column": column,
            "protocol_targets": set(),
        }
    )
    return True


def _resolve_cpp_callable_identity(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    declaration: re.Match[str],
    text: str,
    path: str,
) -> _CppCallableIdentity:
    """Resolve owner, visibility and stable identity for one C++ callable."""
    prev_match = re.search(r"\bprev\s+(0x[0-9A-Fa-f]+)\b", text)
    previous_clang_id = prev_match.group(1) if prev_match is not None else ""
    previous_symbol_id = (
        facts.clang_targets[previous_clang_id]
        if previous_clang_id in facts.clang_targets
        else ""
    )
    if previous_symbol_id:
        existing = facts.symbols[previous_symbol_id]
        return _CppCallableIdentity(
            symbol_id=existing.symbol_id,
            owner_id=existing.owner_id,
            owner_kind=existing.owner_kind,
            visibility=existing.visibility,
            qualname=existing.qualname,
            kind=existing.kind,
            existing=existing,
        )

    parent_match = re.search(r"\bparent\s+(0x[0-9A-Fa-f]+)\b", text)
    parent_clang_id = parent_match.group(1) if parent_match is not None else ""
    parent_class_id = (
        facts.clang_targets[parent_clang_id]
        if parent_clang_id in facts.clang_targets
        else ""
    )
    class_context = _cpp_nearest(cursor.stack, "class")
    class_owner_id = parent_class_id
    if class_context is not None and class_context["owner_id"]:
        class_owner_id = str(class_context["owner_id"])

    name = declaration.group("name").split("::")[-1]
    scope = _cpp_scope_qualname(cursor.stack)
    if class_owner_id and class_owner_id in facts.symbols:
        scope = facts.symbols[class_owner_id].qualname
    qualname = f"{scope}::{name}" if scope else name
    signature = declaration.group("sig")
    symbol_id = f"cpp:symbol:{qualname}:{signature}"
    kind = _CPP_CALLABLE_KIND[declaration.group("kind")]

    if class_owner_id:
        access = str(class_context["access"]) if class_context is not None else ""
        return _CppCallableIdentity(
            symbol_id=symbol_id,
            owner_id=class_owner_id,
            owner_kind=OwnerKind.CLASS,
            visibility=_cpp_visibility(access),
            qualname=qualname,
            kind=kind,
        )

    namespace = _cpp_nearest(cursor.stack, "namespace")
    if namespace is not None and namespace["owner_id"]:
        owner_id = str(namespace["owner_id"])
        owner_kind = OwnerKind.NAMESPACE
    else:
        owner_id = _record_cpp_module_owner(facts, path)
        owner_kind = OwnerKind.MODULE
    visibility = Visibility.INTERNAL if " static " in f" {text} " else Visibility.PUBLIC
    return _CppCallableIdentity(
        symbol_id=symbol_id,
        owner_id=owner_id,
        owner_kind=owner_kind,
        visibility=visibility,
        qualname=qualname,
        kind=kind,
    )


def _record_cpp_callable_symbol(
    facts: _CppTopologyFacts,
    declaration: re.Match[str],
    identity: _CppCallableIdentity,
    path: str,
    start: int,
    end: int,
) -> None:
    """Record the widest authored span for one stable callable identity."""
    candidate = SymbolFact(
        symbol_id=identity.symbol_id,
        language="cpp",
        kind=identity.kind,
        owner_id=identity.owner_id,
        owner_kind=identity.owner_kind,
        visibility=identity.visibility,
        path=Path(path),
        line=start,
        end_line=end,
        lines=end - start + 1,
        name=declaration.group("name").split("::")[-1],
        qualname=identity.qualname,
    )
    if identity.existing is None or candidate.lines > identity.existing.lines:
        facts.symbols[identity.symbol_id] = candidate
    facts.clang_targets[declaration.group("id")] = identity.symbol_id


def _consume_cpp_variable_declaration(
    cursor: _CppAstCursor,
    text: str,
    node: tuple[str, int] | None,
    column: int,
) -> bool:
    """Open a local-variable context so a child lambda can inherit its reference id."""
    if node is None or node[0] != "VarDecl":
        return False
    variable = _CPP_VAR_RE.search(text)
    if variable is None:
        return False
    cursor.stack.append(
        {
            "kind": "variable",
            "column": column,
            "clang_id": variable.group("id"),
        }
    )
    return True


def _callable_usage_kind(cursor: _CppAstCursor) -> UsageKind:
    """Classify callable consumption from active compiler-proven call contexts."""
    calls = [item for item in cursor.stack if item["kind"] == "call"]
    if any(bool(item["resolved"]) for item in calls):
        return UsageKind.CALLBACK_REGISTRATION
    if calls:
        calls[-1]["resolved"] = True
        return UsageKind.DIRECT_CALL
    return UsageKind.CALLABLE_REFERENCE


def _consume_cpp_lambda_expression(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
    column: int,
) -> bool:
    """Record one authored lambda as a nested callable and typed usage edge."""
    expression = _CPP_LAMBDA_RE.search(text)
    if expression is None or column < 0:
        return False
    location_file, start, end = clang_line_range(
        expression.group("range"), cursor.location_file
    )
    lambda_location = _CPP_LAMBDA_LOCATION_RE.search(text)
    if start is None and lambda_location is not None:
        start = int(lambda_location.group("line"))
        end = start if end is None else end
        if location_file is None:
            location_file = Path(lambda_location.group("path"))
    cursor.location_file = location_file
    path = _cpp_project_path(location_file, facts.authored_paths)
    outer = _cpp_nearest(cursor.stack, "function")
    if (
        path is None
        or start is None
        or end is None
        or outer is None
        or not outer["symbol_id"]
    ):
        cursor.stack.append(
            {
                "kind": "function",
                "column": column,
                "symbol_id": "",
                "line": start or 1,
                "parameter_count": 0,
            }
        )
        return True

    source_id = str(outer["symbol_id"])
    source = facts.symbols[source_id]
    count_key = (path, start)
    facts.lambda_counts[count_key] += 1
    ordinal = facts.lambda_counts[count_key]
    name = f"<lambda@{start}:{ordinal}>"
    qualname = f"{source.qualname}::{name}"
    symbol_id = f"cpp:lambda:{path}:{start}:{ordinal}"
    facts.symbols[symbol_id] = SymbolFact(
        symbol_id=symbol_id,
        language="cpp",
        kind="function",
        owner_id=source.owner_id,
        owner_kind=source.owner_kind,
        visibility=Visibility.INTERNAL,
        path=Path(path),
        line=start,
        end_line=end,
        lines=end - start + 1,
        name=name,
        qualname=qualname,
        nested=True,
    )
    facts.clang_targets[expression.group("id")] = symbol_id
    variable = _cpp_nearest(cursor.stack, "variable")
    if variable is not None:
        facts.clang_targets[str(variable["clang_id"])] = symbol_id
    facts.edges.add(
        UsageEdge(
            source_id=source_id,
            target_id=symbol_id,
            kind=_callable_usage_kind(cursor),
            path=Path(path),
            line=start,
        )
    )
    cursor.stack.append(
        {
            "kind": "function",
            "column": column,
            "symbol_id": symbol_id,
            "line": start,
            "parameter_count": 0,
        }
    )
    return True


def _consume_cpp_function_declaration(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
    column: int,
) -> bool:
    """Consume one authored C++ function/method declaration or definition."""
    declaration = _CPP_FUNCTION_RE.search(text)
    if declaration is None or column < 0:
        return False
    location_file, start, end = clang_line_range(
        declaration.group("range"), cursor.location_file
    )
    cursor.location_file = location_file
    context: dict[str, Any] = {
        "kind": "function",
        "column": column,
        "symbol_id": "",
        "line": start or 1,
        "parameter_count": 0,
    }
    path = _cpp_project_path(location_file, facts.authored_paths)
    authored = path is not None and start is not None and end is not None
    if not authored or " implicit " in f" {text} ":
        cursor.stack.append(context)
        return True

    identity = _resolve_cpp_callable_identity(facts, cursor, declaration, text, path)
    _record_cpp_callable_symbol(facts, declaration, identity, path, start, end)
    context.update(symbol_id=identity.symbol_id, line=start)

    template = _cpp_nearest(cursor.stack, "template")
    if template is not None:
        for target in sorted(template["protocol_targets"]):
            facts.pending_edges.append(
                (
                    identity.symbol_id,
                    target,
                    UsageKind.PROTOCOL_HOOK,
                    Path(path),
                    start,
                    "high",
                )
            )
    if " virtual" in text or " pure" in text:
        facts.edges.add(
            UsageEdge(
                source_id=identity.symbol_id,
                target_id=identity.owner_id,
                kind=UsageKind.PROTOCOL_HOOK,
                path=Path(path),
                line=start,
            )
        )
    cursor.stack.append(context)
    return True


def _consume_cpp_field_relation(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
) -> bool:
    """Consume one authored class-field declaration."""
    field_match = _CPP_FIELD_RE.search(text)
    if field_match is None:
        return False
    location_file, start, end = clang_line_range(
        field_match.group("range"), cursor.location_file
    )
    cursor.location_file = location_file
    path = _cpp_project_path(location_file, facts.authored_paths)
    class_context = _cpp_nearest(cursor.stack, "class")
    if (
        path is None
        or start is None
        or end is None
        or class_context is None
        or not class_context["owner_id"]
    ):
        return True
    owner_id = str(class_context["owner_id"])
    name = field_match.group("name")
    symbol_id = f"cpp:field:{owner_id}:{name}"
    facts.symbols[symbol_id] = SymbolFact(
        symbol_id=symbol_id,
        language="cpp",
        kind="field",
        owner_id=owner_id,
        owner_kind=OwnerKind.CLASS,
        visibility=_cpp_visibility(str(class_context["access"])),
        path=Path(path),
        line=start,
        end_line=end,
        lines=end - start + 1,
        name=name,
        qualname=f"{facts.symbols[owner_id].qualname}::{name}",
    )
    facts.clang_targets[field_match.group("id")] = symbol_id
    return True


def _consume_cpp_inheritance_relation(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
) -> bool:
    """Capture one compiler-emitted base-class relationship for the active class."""
    stripped = text.lstrip(" |`-")
    if not stripped.startswith(("public '", "protected '", "private '")):
        return False
    class_context = _cpp_nearest(cursor.stack, "class")
    if class_context is None or not class_context["owner_id"]:
        return True
    base_match = re.search(
        r"(?:public|protected|private)\s+'[^']+':'(?P<base>[^']+)'",
        text,
    )
    if base_match is None:
        return True
    class_symbol = facts.symbols[str(class_context["owner_id"])]
    facts.pending_bases.append(
        (
            class_symbol.symbol_id,
            base_match.group("base"),
            class_symbol.path,
            class_symbol.line,
        )
    )
    return True


def _consume_cpp_protocol_relation(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
) -> bool:
    """Capture override or named-concept protocol evidence from one AST line."""
    override = _CPP_OVERRIDE_RE.search(text)
    concept_ref = _CPP_CONCEPT_REF_RE.search(text)
    if override is None and concept_ref is None:
        return False
    function = _cpp_nearest(cursor.stack, "function")
    if function is not None and function["symbol_id"]:
        source_id = str(function["symbol_id"])
        target = (
            override.group("target")
            if override is not None
            else concept_ref.group("target")
        )
        kind = UsageKind.OVERRIDE if override is not None else UsageKind.PROTOCOL_HOOK
        source = facts.symbols[source_id]
        facts.pending_edges.append(
            (source_id, target, kind, source.path, source.line, "high")
        )
        return True
    if concept_ref is not None:
        template = _cpp_nearest(cursor.stack, "template")
        if template is not None:
            template["protocol_targets"].add(concept_ref.group("target"))
    return True


def _consume_cpp_parameter_relation(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    node: tuple[str, int] | None,
) -> bool:
    """Increment the normalized parameter count for one callable parameter node."""
    if node is None or node[0] != "ParmVarDecl":
        return False
    function = _cpp_nearest(cursor.stack, "function")
    if function is None or not function["symbol_id"]:
        return True
    source_id = str(function["symbol_id"])
    count = int(function["parameter_count"]) + 1
    function["parameter_count"] = count
    if count > facts.symbols[source_id].parameter_count:
        facts.symbols[source_id] = replace(
            facts.symbols[source_id], parameter_count=count
        )
    return True


def _consume_cpp_structural_relation(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
    node: tuple[str, int] | None,
) -> None:
    """Dispatch structural AST evidence to stable node-kind handlers."""
    if _consume_cpp_field_relation(facts, cursor, text):
        return
    if _consume_cpp_inheritance_relation(facts, cursor, text):
        return
    if _consume_cpp_protocol_relation(facts, cursor, text):
        return
    _consume_cpp_parameter_relation(facts, cursor, node)


def _consume_cpp_reference_relation(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
) -> None:
    """Capture field/callable usage for the active authored C++ callable."""
    function = _cpp_nearest(cursor.stack, "function")
    if function is None or not function["symbol_id"]:
        return
    source_id = str(function["symbol_id"])
    source_symbol = facts.symbols[source_id]
    member_ref = _CPP_MEMBER_REF_RE.search(text)
    decl_ref = _CPP_DECL_REF_RE.search(text)
    if member_ref is not None:
        target_clang = member_ref.group("target")
    elif decl_ref is not None:
        target_clang = decl_ref.group("target")
    else:
        return
    if target_clang not in facts.clang_targets:
        return
    target_id = facts.clang_targets[target_clang]
    if target_id not in facts.symbols:
        return
    target_symbol = facts.symbols[target_id]
    if target_symbol.kind == "field":
        usage_kind = UsageKind.FIELD_ACCESS
    elif target_symbol.kind in {*_CPP_CALLABLE_KIND.values(), "function"}:
        usage_kind = _callable_usage_kind(cursor)
    else:
        return
    facts.edges.add(
        UsageEdge(
            source_id=source_id,
            target_id=target_id,
            kind=usage_kind,
            path=source_symbol.path,
            line=source_symbol.line,
        )
    )


def _consume_cpp_call_context(
    cursor: _CppAstCursor,
    node: tuple[str, int] | None,
    column: int,
) -> None:
    """Open one call-expression context so child references can be classified."""
    if node is None or node[0] not in {
        "CallExpr",
        "CXXMemberCallExpr",
        "CXXOperatorCallExpr",
        "CUDAKernelCallExpr",
    }:
        return
    cursor.stack.append(
        {
            "kind": "call",
            "column": column,
            "resolved": False,
        }
    )


def _populate_cpp_sources(
    facts: _CppTopologyFacts,
    snapshot: RepositoryAnalysisSnapshot,
    root: Path,
) -> None:
    """Materialize authored C++ source units for isolated compiler analysis."""
    for path, unit in snapshot.language_units.items():
        if unit.language != "cpp":
            continue
        destination = (root / path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(unit.source, encoding="utf-8")
        facts.authored_paths[destination] = path


def _consume_cpp_ast_line(
    facts: _CppTopologyFacts,
    cursor: _CppAstCursor,
    text: str,
) -> None:
    """Route one Clang AST line through normalized semantic handlers."""
    node_match = re.search(
        r"(?P<kind>[A-Za-z][A-Za-z0-9_]*)\s+0x[0-9A-Fa-f]+\b",
        text,
    )
    node = (
        (node_match.group("kind"), node_match.start("kind"))
        if node_match is not None
        else None
    )
    column = node[1] if node is not None else -1
    if column >= 0:
        while cursor.stack and int(cursor.stack[-1]["column"]) >= column:
            cursor.stack.pop()
    if _consume_cpp_namespace_declaration(facts, cursor, text, column):
        return
    if _consume_cpp_record_declaration(facts, cursor, text, column):
        return
    if _consume_cpp_access_declaration(cursor, text):
        return
    if _consume_cpp_concept_declaration(facts, cursor, text, column):
        return
    if _consume_cpp_template_declaration(cursor, text, node, column):
        return
    if _consume_cpp_function_declaration(facts, cursor, text, column):
        return
    if _consume_cpp_variable_declaration(cursor, text, node, column):
        return
    if _consume_cpp_lambda_expression(facts, cursor, text, column):
        return
    _consume_cpp_structural_relation(facts, cursor, text, node)
    _consume_cpp_reference_relation(facts, cursor, text)
    _consume_cpp_call_context(cursor, node, column)


def _consume_cpp_translation_unit(
    facts: _CppTopologyFacts,
    root: Path,
    relative_path: str,
    clang: str,
    include_args: list[str],
) -> None:
    """Run Clang for one authored translation unit and consume its AST evidence."""
    source = (root / relative_path).resolve()
    if source not in facts.authored_paths or not source.is_file():
        return
    command = [
        clang,
        "-std=c++20",
        "-fsyntax-only",
        "-Xclang",
        "-ast-dump",
        *include_args,
        str(source),
    ]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_stream:
        process = subprocess.Popen(
            command,
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=error_stream,
        )
        if process.stdout is None:
            process.kill()
            raise RuntimeError(f"无法读取 C++ AST 输出：{relative_path}")
        cursor = _CppAstCursor()
        with process.stdout:
            for raw_line in process.stdout:
                _consume_cpp_ast_line(facts, cursor, raw_line.rstrip("\n"))
        return_code = process.wait()
        error_stream.seek(0)
        stderr = error_stream.read()
    if return_code != 0:
        detail = clang_error_detail(stderr)
        raise RuntimeError(f"C++ AST 解析失败 `{relative_path}`：{detail}")


def _consume_cpp_pending_relations(facts: _CppTopologyFacts) -> None:
    """Resolve deferred Clang ids and base names after all translation units exist."""
    for source_id, target_clang, kind, path, line, confidence in facts.pending_edges:
        if target_clang not in facts.clang_targets:
            continue
        facts.edges.add(
            UsageEdge(
                source_id=source_id,
                target_id=facts.clang_targets[target_clang],
                kind=kind,
                path=path,
                line=line,
                confidence=confidence,
            )
        )
    for source_id, base_qualname, path, line in facts.pending_bases:
        if base_qualname not in facts.class_by_qualname:
            continue
        facts.edges.add(
            UsageEdge(
                source_id=source_id,
                target_id=facts.class_by_qualname[base_qualname],
                kind=UsageKind.INHERITANCE,
                path=path,
                line=line,
            )
        )


def cpp_topology(
    snapshot: RepositoryAnalysisSnapshot,
    paths: list[str],
    clang: str,
) -> RepositoryTopology:
    """Project compiler-proven C++ owner/reuse facts into normalized topology.

    Clang node ids are transient resolution keys scoped to one compiler process. Stable
    graph identities use authored qualified names and signatures. Each translation unit
    is accumulated atomically; unavailable units contribute no partial facts.

    Args:
        snapshot: Shared repository source snapshot.
        paths: Repository-relative C++ translation units to inspect.
        clang: Resolved Clang compiler executable.

    Returns:
        Normalized C++ symbols, owners, and typed usage edges.
    """
    facts = _CppTopologyFacts()
    with tempfile.TemporaryDirectory(prefix="rqg-cpp-topology-") as directory:
        root = Path(directory).resolve()
        _populate_cpp_sources(facts, snapshot, root)
        requested_paths = sorted(dict.fromkeys(paths))
        include_args_by_path = clang_include_args(snapshot, root, requested_paths)
        for relative_path in requested_paths:
            unit_facts = _CppTopologyFacts(authored_paths=facts.authored_paths)
            try:
                _consume_cpp_translation_unit(
                    unit_facts,
                    root,
                    relative_path,
                    clang,
                    list(include_args_by_path[relative_path]),
                )
            except RuntimeError:
                continue
            _consume_cpp_pending_relations(unit_facts)
            facts.symbols.update(unit_facts.symbols)
            facts.owners.update(unit_facts.owners)
            facts.edges.update(unit_facts.edges)
    return RepositoryTopology(
        symbols=tuple(sorted(facts.symbols.values(), key=lambda item: item.symbol_id)),
        owners=tuple(sorted(facts.owners.values(), key=lambda item: item.owner_id)),
        edges=tuple(
            sorted(
                facts.edges,
                key=lambda item: (
                    item.path.as_posix(),
                    item.line,
                    item.source_id,
                    item.target_id,
                    item.kind,
                ),
            )
        ),
    )
