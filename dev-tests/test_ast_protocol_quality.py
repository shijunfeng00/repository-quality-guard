"""Ensure compiler AST protocols are not mistaken for application type guessing."""

from pathlib import Path

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


def _findings(tmp_path: Path, source: str):
    """Scan an actual parsed source under the production rule engine."""
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")
    return (
        RepositoryScanner(
            tmp_path,
            GuardConfig(require_docstrings=False, require_docstring_sections=False),
        )
        .scan()
        .findings
    )


def test_stdlib_ast_tagged_union_is_not_qg060(tmp_path: Path) -> None:
    """An AST node class is a declared polymorphic compiler input."""
    rows = _findings(
        tmp_path,
        "import ast\n"
        "def decode(stmt: ast.stmt):\n"
        "    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):\n"
        "        return stmt.value\n"
        "    return None\n",
    )
    assert not [item for item in rows if item.code == "QG060"]


def test_stdlib_ast_alias_is_not_qg060(tmp_path: Path) -> None:
    """An unshadowed stdlib import alias has the same typed semantics."""
    rows = _findings(
        tmp_path,
        "import ast as syntax\n"
        "def decode(stmt: syntax.stmt):\n"
        "    return isinstance(stmt, syntax.Assign)\n",
    )
    assert not [item for item in rows if item.code == "QG060"]


def test_internal_object_discriminator_still_qg060(tmp_path: Path) -> None:
    """A concrete business type is not a tagged syntax-node protocol."""
    rows = _findings(
        tmp_path,
        "class Contract: pass\nclass Alternative: pass\n"
        "def consume(value: Contract):\n"
        "    return isinstance(value, Alternative)\n",
    )
    assert "QG060" in [item.code for item in rows]


def test_shadowed_ast_is_not_trusted(tmp_path: Path) -> None:
    """An import later overwritten by an internal object cannot exempt probes."""
    rows = _findings(
        tmp_path,
        "import ast\n"
        "class Fake:\n    AST = object\n    Assign = str\n"
        "ast = Fake()\n"
        "def consume(value: ast.AST):\n"
        "    return isinstance(value, ast.Assign)\n",
    )
    assert "QG060" in [item.code for item in rows]


def test_nested_nodevisitor_callback_not_ephemeral(tmp_path: Path) -> None:
    """The inherited visitor dispatch, not the hook's spelling alone, is reuse."""
    rows = _findings(
        tmp_path,
        "import ast\n"
        "def inspect(root):\n"
        "    class Collector(ast.NodeVisitor):\n"
        "        def visit_Name(self, node):\n"
        "            return node.id\n"
        "    instance = Collector()\n"
        "    return instance.visit(root)\n",
    )
    assert not [
        item
        for item in rows
        if item.code == "QG001" and item.symbol.endswith("Collector.visit_Name")
    ]


def test_unrelated_visit_named_method_has_no_protocol_edge(tmp_path: Path) -> None:
    """A similarly named method without NodeVisitor inheritance gets no hook."""
    _findings(
        tmp_path,
        "def inspect(root):\n"
        "    class Collector:\n"
        "        def visit_Name(self, node):\n"
        "            return node.id\n"
        "    instance = Collector()\n"
        "    return instance\n",
    )
    # A name-shaped callback is not a framework contract without its base.
    # Confirm the actual normalized graph carries no fake protocol edge.
    from runtime.src.analysis_snapshot import directory_analysis_snapshot
    from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
    from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
    from runtime.src.topology_facts import UsageKind

    graph = RepositoryRelationGraph(
        directory_analysis_snapshot(tmp_path, GuardConfig())
    )
    topology = normalized_topology(
        graph, StaticPythonDependencyResolver.from_environment()
    )
    assert not [
        edge
        for edge in topology.edges
        if edge.source_id.endswith("Collector.visit_Name")
        and edge.kind == UsageKind.PROTOCOL_HOOK
    ]


def test_declared_frozen_postinit_field_is_not_qg183(tmp_path: Path) -> None:
    """A frozen dataclass may initialize a formally declared derived field."""
    rows = _findings(
        tmp_path,
        "from dataclasses import dataclass, field\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class Snapshot:\n"
        "    source: int\n"
        "    derived: int = field(init=False)\n"
        "    def __post_init__(self):\n"
        "        object.__setattr__(self, 'derived', self.source + 1)\n",
    )
    assert not [item for item in rows if item.code == "QG183"]


def test_undeclared_frozen_postinit_attribute_is_qg183(tmp_path: Path) -> None:
    """The same reflective write to an undeclared field remains a blocker."""
    rows = _findings(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class Snapshot:\n"
        "    source: int\n"
        "    def __post_init__(self):\n"
        "        object.__setattr__(self, 'invented', self.source + 1)\n",
    )
    assert "QG183" in [item.code for item in rows]
