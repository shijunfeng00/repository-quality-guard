"""Source-pinned, read-only graph cache and location-based evidence projection."""

from __future__ import annotations


import ast
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import NamedTuple


def _digest(snapshot, skill_root: Path) -> str:
    """Bind the graph to every included source, provider and repo config, not mtime."""
    digest = hashlib.sha256()
    for key in sorted(snapshot.units):
        item = snapshot.units[key]
        digest.update(key.encode("utf-8"))
        digest.update(hashlib.sha256(item.source.encode("utf-8")).digest())
    for key in sorted(snapshot.language_units):
        item = snapshot.language_units[key]
        digest.update(key.encode("utf-8"))
        digest.update(hashlib.sha256(item.source.encode("utf-8")).digest())
    for key in ("config.json", "AGENTS.md", ".gitignore"):
        path = snapshot.root / key
        if path.is_file():
            digest.update(key.encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    providers = sorted((skill_root / "runtime" / "src").glob("*.py"))
    providers.append(skill_root / "scripts" / "evidence_graph.py")
    for path in providers:
        digest.update(path.relative_to(skill_root).as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _serialize(topology) -> dict:
    """Project normalized symbol, call and parameter facts into cacheable JSON."""
    return {
        "symbols": [
            {
                "id": s.symbol_id,
                "name": s.name,
                "qualname": s.qualname,
                "path": s.path.as_posix(),
                "line": s.line,
                "end": s.end_line,
                "kind": s.kind,
                "owner": s.owner_id,
                "bases": list(s.bases),
                "language": s.language,
            }
            for s in topology.symbols
        ],
        "edges": [
            {
                "source": e.source_id,
                "target": e.target_id,
                "kind": e.kind.value,
                "path": e.path.as_posix(),
                "line": e.line,
                "confidence": e.confidence,
            }
            for e in topology.edges
        ],
        "parameter_forwardings": [
            {
                "source": f.source_symbol_id,
                "src_index": f.source_parameter_index,
                "target": f.target_symbol_id,
                "dst_index": f.target_parameter_index,
                "path": f.path.as_posix(),
                "line": f.line,
                "capability": f.capability,
            }
            for f in topology.parameter_forwardings
        ],
    }


def load_graph(snapshot, skill_root: Path, cache_dir: Path | None):
    """Load or rebuild normalized graph facts with content-addressed freshness.

    Args:
        snapshot: Authoritative source snapshot for the target repository.
        skill_root: Skill root used to fingerprint provider implementations.
        cache_dir: Optional external directory for immutable graph snapshots.

    Returns:
        Graph JSON facts, cache HIT/MISS label, and content digest.
    """
    key = _digest(snapshot, skill_root)
    if cache_dir is None:
        cache_dir = (
            Path.home() / ".cache" / "repository-quality-guard" / "evidence-graph"
        )
    repo_id = hashlib.sha256(str(snapshot.root.resolve()).encode("utf-8")).hexdigest()[
        :24
    ]
    cache_file = cache_dir / f"{repo_id}.json"
    if cache_file.is_file():
        try:
            entry = json.loads(cache_file.read_text(encoding="utf-8"))
        except (ValueError, OSError) as err:
            raise ValueError(f"缓存损坏，需显式清理: {cache_file}: {err}") from err
        if entry.get("format") == "rqg-evidence-v1" and entry.get("digest") == key:
            payload = entry.get("graph")
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("symbols"), list)
                or not isinstance(payload.get("edges"), list)
            ):
                raise ValueError(f"缓存结构损坏，需显式清理: {cache_file}")
            return payload, "HIT", key
    from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology

    payload = _serialize(normalized_topology(RepositoryRelationGraph(snapshot)))
    cache_dir.mkdir(parents=True, exist_ok=True)
    entry = {"format": "rqg-evidence-v1", "digest": key, "graph": payload}
    encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    # Same-directory atomic replacement prevents a partially written reusable graph.
    descriptor, tmp = tempfile.mkstemp(prefix=".rqg-evidence-", dir=cache_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
            writer.write(encoded)
        os.replace(tmp, cache_file)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return payload, "MISS/REBUILT", key


def _location_matches(symbols, file: str, line: int, name: str | None, kind: str):
    """Select the narrowest authored symbol enclosing a source location."""
    candidates = [
        s for s in symbols if s["path"] == file and s["line"] <= line <= s["end"]
    ]
    if kind == "class":
        matches = [
            s
            for s in candidates
            if s["kind"] == "class" and (name is None or s["name"] == name)
        ]
    elif kind in ("function", "variable"):
        matches = [s for s in candidates if s["kind"] in ("function", "method")]
    elif name:
        matches = [
            s
            for s in candidates
            if s["name"] == name and s["kind"] in ("class", "function", "method")
        ]
        if not matches:
            matches = [s for s in candidates if s["kind"] in ("method", "function")]
    else:
        matches = [
            s for s in candidates if s["kind"] in ("method", "function", "class")
        ]
    if matches:
        smallest = min(s["end"] - s["line"] for s in matches)
        matches = [s for s in matches if s["end"] - s["line"] == smallest]
    return matches


def select_symbol(
    graph: dict,
    query: str | None,
    file: str | None,
    line: int | None,
    name: str | None,
    kind: str,
):
    """Find an unambiguous authored symbol at the requested source location.

    Args:
        graph: Source-bound repository graph.
        query: Exact symbol identity or name, if supplied.
        file: Repository-relative file when selecting by location.
        line: One-based line containing the desired owner.
        name: Optional name narrowing the location.
        kind: Requested symbol category.

    Returns:
        Selected symbol dictionary, or an explicit ambiguity/error result.
    """
    symbols = graph["symbols"]
    if query:
        matches = [s for s in symbols if s["id"] == query]
        if not matches:
            matches = [
                s for s in symbols if s["name"] == query or s["qualname"] == query
            ]
    else:
        matches = _location_matches(symbols, file, line, name, kind)
    if len(matches) != 1:
        ids = ", ".join(x["id"] for x in matches[:12])
        raise ValueError(
            f"目标必须唯一，匹配 {len(matches)} 个: {ids or (query or file)}"
        )
    return matches[0]


def context(snapshot, path: str, line: int, radius: int = 2) -> list[str]:
    """Render bounded authored source lines around a resolved repository position.

    Args:
        snapshot: Repository snapshot holding exact source contents.
        path: Repository-relative source file path.
        line: One-based source location.
        radius: Number of adjacent lines to display on either side.

    Returns:
        Formatted source lines including line numbers and observation marker.
    """
    unit = snapshot.units.get(path)
    if unit is None:
        return ["    [该语言源码片段暂不可用]"]
    lines = unit.source.splitlines()
    return [
        f"{' >' if n == line else '  '} {n:5}: {lines[n - 1]}"
        for n in range(max(1, line - radius), min(len(lines), line + radius) + 1)
    ]


def find_node(snapshot, symbol):
    """Locate the exact authored Python definition for a graph symbol.

    Args:
        snapshot: Repository snapshot including Python syntax trees.
        symbol: Selected graph symbol with file, line and name.

    Returns:
        Matching authored AST node, or None when unavailable.
    """
    unit = snapshot.units.get(symbol["path"])
    if unit is None or unit.tree is None:
        return None
    nodes = (
        n
        for n in ast.walk(unit.tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and n.lineno == symbol["line"]
        and n.name == symbol["name"]
    )
    return next(nodes, None)


def _lexical_body_walk(node):
    """Avoid mixing eager execution with nested function or class code."""

    class Walk(ast.NodeVisitor):
        """Collect only statements belonging to the immediately enclosing owner.

        Nested function and class definitions are independent lexical scopes;
        their assignments cannot be treated as candidate writes by this owner.
        """

        def __init__(self):
            """Initialize the lexical statement collection.

            Returns:
                None; a new mutable collection is allocated for this traversal.
            """
            self.nodes = []

        def visit_FunctionDef(self, inner):
            """Skip nested scope owners while keeping their bodies unvisited.

            Args:
                inner: Nested callable definition outside the current scope.

            Returns:
                None; nested bodies are excluded from candidate assignment facts.
            """
            return

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def generic_visit(self, item):
            """Collect each current-scope node then continue the AST traversal.

            Args:
                item: Source AST node in the current lexical owner.

            Returns:
                None; only the visitor's collection is updated.
            """
            self.nodes.append(item)
            super().generic_visit(item)

    visit = Walk()
    for body in getattr(node, "body", []):
        visit.visit(body)
    return visit.nodes


def lexical_candidates(snapshot, anchor: dict, limit: int = 12):
    """List lexical call candidates without assigning a false receiver owner.

    Args:
        snapshot: Actual parsed source snapshot.
        anchor: Selected function or method symbol.
        limit: Maximum source candidates returned.

    Returns:
        Bounded source paths, positions and expressions for unresolved calls.
    """
    if anchor["kind"] not in ("method", "function"):
        return []
    matches = []
    for path, unit in sorted(snapshot.units.items()):
        if unit.tree is None:
            continue
        for node in ast.walk(unit.tree):
            if not isinstance(node, ast.Call):
                continue
            if (
                not isinstance(node.func, ast.Attribute)
                or node.func.attr != anchor["name"]
            ):
                continue
            # All sites are only lexical candidates. Keep tests in a lower
            # priority group, never promote same-name to receiver identity.
            production = not path.startswith(("dev-tests/", "tests/", "test/"))
            same_module = path == anchor["path"]
            matches.append(
                (
                    0 if same_module else 1 if production else 2,
                    path,
                    node.lineno,
                    ast.unparse(node)[:180],
                )
            )
    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return [(path, line, expr) for _, path, line, expr in matches[:limit]]


def _formals(node):
    """Return explicitly declared formal parameter names in source order."""
    return [
        arg.arg for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs
    ]


def _bindings(snapshot, symbol, name: str, before: int, budget: int):
    """Enumerate candidate assignments without claiming path dominance."""
    node = find_node(snapshot, symbol)
    if node is None:
        return []
    candidates = []
    for statement in _lexical_body_walk(node):
        if not isinstance(
            statement, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)
        ):
            continue
        if not getattr(statement, "lineno", 0) <= before:
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        if not any(
            isinstance(target, (ast.Name, ast.Attribute))
            and ast.unparse(target) == name
            for target in targets
        ):
            continue
        candidates.append(statement)
    return sorted(candidates, key=lambda item: item.lineno)[-budget:]


def _call_site(snapshot, edge, callee):
    """Resolve one unique source-level Call for an already-bound graph edge."""
    unit = snapshot.units.get(edge["path"])
    if unit is None or unit.tree is None:
        return None
    constructor_name = (
        callee["owner"].rsplit(".", 1)[-1] if callee["name"] == "__init__" else None
    )
    candidates = [
        n
        for n in ast.walk(unit.tree)
        if isinstance(n, ast.Call)
        and n.lineno == edge["line"]
        and isinstance(n.func, (ast.Name, ast.Attribute))
        and (n.func.id if isinstance(n.func, ast.Name) else n.func.attr)
        in {callee["name"], constructor_name}
    ]
    return candidates[0] if len(candidates) == 1 else None


class ParameterTrace(NamedTuple):
    """Preserve bounded source evidence during recursive parameter trace traversal.

    This lightweight value carries the same immutable graph and source snapshot
    through upstream call edges, without constructing a parallel repository IR.
    Candidate writes never constitute proven reaching definitions.
    """

    snapshot: object
    graph: dict
    depth: int
    budget: int
    seen: set


def _parameter_assignment_sources(
    env: ParameterTrace, caller: dict, edge: dict, actual: ast.Name
):
    """Expand candidate assignments and recursively bound upstream formals."""
    result = []
    snapshot, graph, depth, budget, seen = env
    caller_node = find_node(snapshot, caller)
    for assignment in _bindings(
        snapshot, caller, actual.id, edge["line"], min(budget, 6)
    ):
        rhs = getattr(assignment, "value", None)
        result.append(
            f"  - 上游候选赋值 `{edge['path']}:{assignment.lineno}`: `{ast.unparse(assignment)[:240]}`"
        )
        result.extend(context(snapshot, edge["path"], assignment.lineno, 2))
        if rhs is None:
            continue
        deps = sorted(
            {
                n.id
                for n in ast.walk(rhs)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
        )
        for dep in deps[:2]:
            if caller_node and dep in _formals(caller_node):
                result.extend(
                    _param_sources(
                        snapshot,
                        graph,
                        caller,
                        dep,
                        depth=depth - 1,
                        budget=budget,
                        seen=seen,
                    )
                )
    if caller_node and actual.id in _formals(caller_node):
        result.extend(
            _param_sources(
                snapshot,
                graph,
                caller,
                actual.id,
                depth=depth - 1,
                budget=budget,
                seen=seen,
            )
        )
    return result


def _param_sources(
    snapshot, graph, symbol, formal: str, depth: int, budget: int, seen=None
):
    """Read positional/keyword expressions on verified edges; content remains OPEN."""
    seen = set() if seen is None else seen
    marker = (symbol["id"], formal)
    if marker in seen or depth <= 0:
        return []
    seen.add(marker)
    function = find_node(snapshot, symbol)
    if function is None or not hasattr(function, "args"):
        return ["- 无法解析此函数的形参，继续人工查找"]
    formals = _formals(function)
    if formal not in formals:
        return []
    position = formals.index(formal)
    result = []
    owners = {s["id"]: s for s in graph["symbols"]}
    edges = [
        e
        for e in graph["edges"]
        if e["kind"] == "direct_call" and e["target"] == symbol["id"]
    ]
    env = ParameterTrace(snapshot, graph, depth, budget, seen)
    for edge in edges[:budget]:
        caller = owners.get(edge["source"])
        if caller is None:
            continue
        call = _call_site(snapshot, edge, symbol)
        if call is None:
            result.append(
                f"- `[UNKNOWN]` `{edge['path']}:{edge['line']}` 已有调用边，但无法唯一绑定 AST 实参"
            )
            continue
        shift = (
            1
            if (isinstance(call.func, ast.Attribute) or symbol["name"] == "__init__")
            and formals
            and formals[0] in ("self", "cls")
            else 0
        )
        actual_pos = position - shift
        actual = (
            call.args[actual_pos]
            if 0 <= actual_pos < len(call.args)
            else next((kw.value for kw in call.keywords if kw.arg == formal), None)
        )
        if actual is None:
            result.append(
                f"- `[UNKNOWN]` `{edge['path']}:{edge['line']}` 未解析到显式实参（可能是默认值或 **展开）"
            )
            continue
        expression = ast.unparse(actual)
        result.append(
            f"- **实参绑定** `[SOURCE_BOUND_MAY_CALL]` `{caller['id']}` → `{symbol['id']}.{formal}` = `{expression}` @ `{edge['path']}:{edge['line']}`"
        )
        result.extend(context(snapshot, edge["path"], edge["line"], 2))
        if isinstance(actual, ast.Name):
            result.extend(_parameter_assignment_sources(env, caller, edge, actual))
        elif (
            isinstance(actual, ast.Attribute)
            and isinstance(actual.value, ast.Name)
            and actual.value.id == "self"
        ):
            result.extend(
                _constructor_field_sources(
                    snapshot, graph, caller, f"self.{actual.attr}", min(budget, 4)
                )
            )
    if len(edges) > budget:
        result.append("- **TRUNCATED**：部分调用方未展示")
    return result


def _field_method_candidates(
    snapshot, anchor: dict, owner: dict, method, field_name: str
):
    """Expose authored writes and source-container mutations, never path reachability."""
    result = []
    for call in (n for n in _lexical_body_walk(method) if isinstance(n, ast.Call)):
        if (
            not isinstance(call.func, ast.Attribute)
            or call.func.attr != "__setattr__"
            or len(call.args) < 3
            or not isinstance(call.args[1], ast.Constant)
            or call.args[1].value != field_name
        ):
            continue
        result.append(
            f"- **字段写入候选** `{anchor['path']}:{call.lineno}` 归属 `{owner['id']}.{method.name}`: `{ast.unparse(call.args[2])[:180]}` (源码证据，未证明所有路径都执行)"
        )
        result.extend(context(snapshot, anchor["path"], call.lineno, 3))
        names = {
            n.id
            for n in ast.walk(call.args[2])
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        for name in sorted(names):
            result.extend(_field_container_mutations(snapshot, anchor, method, name))
    return result


def _field_container_mutations(snapshot, anchor: dict, method, name: str):
    """Project source-container calls for a particular authored variable."""
    result = []
    for statement in _lexical_body_walk(method):
        if (
            isinstance(statement, ast.Call)
            and isinstance(statement.func, ast.Attribute)
            and isinstance(statement.func.value, ast.Name)
            and statement.func.value.id == name
        ):
            result.append(
                f"  - 字段来源容器的变更 `{anchor['path']}:{statement.lineno}`: `{ast.unparse(statement)[:160]}`"
            )
            result.extend(context(snapshot, anchor["path"], statement.lineno, 2))
    return result


def _constructor_field_sources(snapshot, graph, anchor, variable: str, budget: int):
    """Trace an instance field via source constructor assignments and bound inputs.

    Args:
        snapshot: Repository source snapshot.
        graph: Cached normalized call and ownership graph.
        anchor: Method using the field.
        variable: Requested self-owned field expression.
        budget: Upper bound for callsite/source expansion.

    Returns:
        Constructor write candidates and verifiable upstream callsite evidence.
    """
    if not variable.startswith("self."):
        return []
    symbols = {item["id"]: item for item in graph["symbols"]}
    owner = symbols.get(anchor["owner"])
    constructor = symbols.get(f"{anchor['owner']}.__init__")
    if owner is None or constructor is None:
        return []
    init = find_node(snapshot, constructor)
    if init is None:
        return []
    field_name = variable.removeprefix("self.")
    matches = []
    for assignment in _lexical_body_walk(init):
        if not isinstance(assignment, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            assignment.targets
            if isinstance(assignment, ast.Assign)
            else [assignment.target]
        )
        if not any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == field_name
            for target in targets
        ):
            continue
        matches.append(assignment)
    result = []
    for assignment in matches[:budget]:
        rhs = assignment.value
        result.append(
            f"- **构造字段来源** `{constructor['id']}` @ "
            f"`{constructor['path']}:{assignment.lineno}`: "
            f"`{ast.unparse(assignment)[:200]}` [SOURCE_WRITE_CANDIDATE]"
        )
        result.extend(context(snapshot, constructor["path"], assignment.lineno, 2))
        if isinstance(rhs, ast.Name):
            result.extend(
                _param_sources(
                    snapshot, graph, constructor, rhs.id, depth=2, budget=budget
                )
            )
    if len(matches) > budget:
        result.append("- **TRUNCATED**：构造字段写入候选超出预算")
    return result


def _field_write_candidates(
    snapshot, graph: dict, anchor: dict, variable: str, budget: int
):
    """Project field constructor writers as bounded candidate evidence."""
    if not (variable.startswith("self.") or variable.startswith("_")):
        return []
    field_name = variable.split(".")[-1]
    unit = snapshot.units.get(anchor["path"])
    owner = next(
        (
            s
            for s in graph["symbols"]
            if s["id"] == anchor["owner"] and s["kind"] == "class"
        ),
        None,
    )
    class_node = find_node(snapshot, owner) if owner else None
    if class_node is None or unit is None:
        return []
    result = []
    for method in class_node.body:
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.extend(
                _field_method_candidates(snapshot, anchor, owner, method, field_name)
            )
    return result


def lineage(snapshot, graph: dict, anchor: dict, variable: str, line: int, budget: int):
    """Show candidate assignments and actual-to-formal evidence without MUST claims.

    Args:
        snapshot: Immutable repository source snapshot.
        graph: Normalized cached graph with parameter edges.
        anchor: Exact owning function symbol.
        variable: Variable or instance field under investigation.
        line: Observation location in the selected source.
        budget: Maximum source candidates per traversal stage.

    Returns:
        Markdown lines identifying source-bound candidates and unknown edges.
    """
    node = find_node(snapshot, anchor)
    if node is None or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ["无法取得函数 AST，变量来源 UNKNOWN"]
    result = [f"## `{variable}` 源码来源与静态赋值（目标 {anchor['path']}:{line}）", ""]
    args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
    params = [a.arg for a in args]
    if node.args.vararg:
        params.append(node.args.vararg.arg)
    if node.args.kwarg:
        params.append(node.args.kwarg.arg)
    if variable in params:
        index = params.index(variable)
        annotation = (
            ast.unparse(args[index].annotation)
            if index < len(args) and args[index].annotation
            else "未声明"
        )
        result.append(
            f"- 参数：`{variable}`，形参序号 `{index}`，注解 `{annotation}`；注解不是实际取值保证"
        )
        forwards = [
            x
            for x in graph["parameter_forwardings"]
            if x["target"] == anchor["id"] and x["dst_index"] == index
        ]
        if forwards:
            for fact in forwards[:budget]:
                caller = next(
                    (s for s in graph["symbols"] if s["id"] == fact["source"]), None
                )
                caller_node = find_node(snapshot, caller) if caller else None
                cp = []
                if isinstance(caller_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cp = [
                        a.arg
                        for a in caller_node.args.posonlyargs
                        + caller_node.args.args
                        + caller_node.args.kwonlyargs
                    ]
                forwarded = (
                    cp[fact["src_index"]] if fact["src_index"] < len(cp) else "UNKNOWN"
                )
                result.append(
                    f"- 已绑定可能传导：`{fact['source']}.{forwarded}` → `{anchor['id']}.{variable}` @ `{fact['path']}:{fact['line']}` (MAY_FORWARD；内容/执行未证明)"
                )
                result.extend(context(snapshot, fact["path"], fact["line"], 2))
        else:
            result.append("- 当前快照没有已证明的跨函数形参传导；不等于没有上游输入")
    result.extend(
        _param_sources(snapshot, graph, anchor, variable, depth=3, budget=budget)
    )
    written = _bindings(snapshot, anchor, variable, line, budget)
    for assignment in written:
        result.append(
            f"- 函数内候选赋值 `{anchor['path']}:{assignment.lineno}`: `{ast.unparse(assignment)[:240]}`"
        )
        result.extend(context(snapshot, anchor["path"], assignment.lineno, 2))
    if not written and variable not in params:
        result.append(
            "- 此函数内无已定位的写入：可能是属性/全局/闭包/别名或其他未解析来源（UNKNOWN）"
        )
    if variable.startswith("self.") or variable.startswith("_"):
        search = variable.split(".")[-1]
        refs = []
        unit = snapshot.units.get(anchor["path"])
        for i, row in enumerate(unit.source.splitlines(), 1):
            if search in row and i != line and i != anchor["line"]:
                refs.append((i, row))
        if refs:
            refs.sort(
                key=lambda x: (
                    0 if "setattr" in x[1] or "setdefault" in x[1] else 1,
                    x[0],
                )
            )
            result.append(
                f"- 同文件属性/字段引用候选 `{search}`（仅词法搜索，非确定的生产者）："
            )
            for i, row in refs[:budget]:
                result.append(f"  - `{anchor['path']}:{i}`: `{row.strip()[:180]}`")
                result.extend(context(snapshot, anchor["path"], i, 2))
    result.extend(_constructor_field_sources(snapshot, graph, anchor, variable, budget))
    result.extend(_field_write_candidates(snapshot, graph, anchor, variable, budget))
    result.append(
        "- **证据边界：赋值语句只是候选来源；未建立 SSA/CFG 支配/别名/实际 Case 证明，不能把它当作最终到达定义。**"
    )
    return result


class TraceOptions(NamedTuple):
    """Hold the requested static trace presentation and graph traversal limits.

    These options control CLI output only; they are not evidence that a call
    executed or that a particular branch is reachable. The graph remains the
    single source snapshot used by rule analysis and repository lookup.
    """

    direction: str
    depth: int
    budget: int
    radius: int
    variable: str | None
    line: int | None
    cache_info: str


def render(snapshot, graph, anchor, options: TraceOptions):
    """Render auditable source-level paths and assignment candidates.

    Args:
        snapshot: Unmodified source snapshot used to locate authored code.
        graph: Repository relation facts resolved against that snapshot.
        anchor: Selected function, method, or class symbol.
        options: Static trace direction, depth, bounds, source context,
            variable target, observation line, and cache provenance.

    Returns:
        Markdown report that distinguishes resolved links from UNKNOWN.
    """
    direction, depth, budget, radius, variable, line, cache_info = options
    result = [
        "# 仓库证据 Trace（静态，非运行时 Traceback）",
        "",
        f"来源：`{snapshot.root.resolve()}` · `{snapshot.label}` · `{cache_info}`",
        f"目标：`{anchor['id']}` @ `{anchor['path']}:{anchor['line']}` · `{anchor['kind']}`",
        "",
        "**OPEN_WORLD**：DIRECT_CALL 是源码绑定；MAY_DISPATCH 是类型约束的可能目标，MAY_CALLBACK 是有调度器证据的可能执行，运行时均 OPEN；词法同名仍 UNKNOWN。三者都不证明该路径执行过或缺省业务合理。",
        "",
        "## 定义及所在代码",
        "",
    ]
    result.extend(context(snapshot, anchor["path"], line or anchor["line"], radius))
    if anchor["kind"] == "class":
        result.extend(
            [
                "",
                "## 类型关系（声明的继承关系）",
                f"- bases: {', '.join(anchor['bases']) or '(无)'} (源码声明，非运行时 MRO)",
            ]
        )
        class_edges = [
            e
            for e in graph["edges"]
            if e["kind"] in ("inheritance", "override")
            and (e["source"] == anchor["id"] or e["target"] == anchor["id"])
        ]
        for edge in class_edges[:budget]:
            result.append(
                f"- `{edge['source']}` → `{edge['target']}` ({edge['kind']}, `{edge['path']}:{edge['line']}`)"
            )
    selected = set()
    for side in ("callers", "callees") if direction == "both" else (direction,):
        if anchor["kind"] not in ("function", "method"):
            continue
        result.extend(["", f"## {side}: 静态调用链（深度 {depth}）", ""])
        queue = [(anchor["id"], (anchor["id"],), 0)]
        seen = {anchor["id"]}
        while queue and len(selected) < budget:
            current, chain, level = queue.pop(0)
            if level >= depth:
                continue
            edges = [
                e
                for e in graph["edges"]
                if e["kind"] in ("direct_call", "may_dispatch", "may_callback")
                and (
                    e["target"] == current
                    if side == "callers"
                    else e["source"] == current
                )
            ]
            edges.sort(key=lambda e: (e["path"], e["line"], e["source"], e["target"]))
            for edge in edges:
                key = (side, edge["source"], edge["target"], edge["path"], edge["line"])
                if key in selected:
                    continue
                if len(selected) >= budget:
                    break
                selected.add(key)
                other = edge["source"] if side == "callers" else edge["target"]
                newchain = (other,) + chain if side == "callers" else chain + (other,)
                relation = {
                    "direct_call": "DIRECT_CALL, resolved",
                    "may_dispatch": "MAY_DISPATCH, declared type; runtime target OPEN",
                    "may_callback": "MAY_CALLBACK, executor scheduling; execution OPEN",
                }[edge["kind"]]
                result.append("- `" + "` → `".join(newchain) + f"` [{relation}]")
                result.append(
                    f"  - CALL SITE `{edge['path']}:{edge['line']}` confidence={edge['confidence']}"
                )
                result.extend(context(snapshot, edge["path"], edge["line"], radius))
                if other not in seen:
                    seen.add(other)
                    queue.append((other, newchain, level + 1))
        if not any(e[0] == side for e in selected):
            result.append(
                "- 当前 Provider 未解析出该方向 DIRECT_CALL/MAY_DISPATCH；不等于不存在真实调用"
            )
        if len(selected) >= budget:
            result.append("- **TRUNCATED**：已达调用边预算")
    if anchor["kind"] in ("method", "function"):
        registered = [
            e for e in graph["edges"]
            if e["kind"] == "callback_registration"
            and e["target"] == anchor["id"]
        ]
        if registered and direction in ("both", "callers"):
            result.extend([
                "", "## 传入回调/注册参数的源码位点（不是已执行调用）", "",
            ])
            for edge in sorted(registered, key=lambda e: (e["path"], e["line"]))[:budget]:
                result.append(
                    f"- `[CALLBACK_ARGUMENT]` `{edge['source']}` → `{anchor['id']}` "
                    f"@ `{edge['path']}:{edge['line']}`（静态参数引用，不证明实际调度）"
                )
                result.extend(context(snapshot, edge["path"], edge["line"], radius))
    if anchor["kind"] in ("method", "function"):
        candidates = (
            lexical_candidates(snapshot, anchor, limit=min(budget, 12))
            if direction in ("callers", "both")
            else []
        )
        if candidates:
            bound_sites = {
                (edge["path"], edge["line"])
                for edge in graph["edges"]
                if edge["kind"] in ("direct_call", "may_dispatch", "may_callback")
                and edge["target"] == anchor["id"]
            }
            unresolved = [
                (path, n, expression)
                for path, n, expression in candidates
                if (path, n) not in bound_sites
            ]
            if unresolved:
                result.extend(
                    ["", "## 未解析的同名方法调用（仅词法候选，不可冒充调用边）", ""]
                )
                for path, n, expression in unresolved:
                    result.append(f"- `[UNKNOWN]` `{path}:{n}` `{expression}`")
                    result.extend(context(snapshot, path, n, radius))
        if variable is not None:
            result.extend(
                [
                    "",
                    *lineage(
                        snapshot, graph, anchor, variable, line or anchor["end"], budget
                    ),
                ]
            )
    result.extend(
        [
            "",
            "结论边界：需人工检查完整 Producer、所有合法入口、Guard 支配、状态变更与真实可达 Cases；未知不得判定 CODE_BUG 或 RULE_BUG。",
        ]
    )
    return "\n".join(result).rstrip() + "\n"
