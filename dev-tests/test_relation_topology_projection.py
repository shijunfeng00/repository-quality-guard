from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.config import GuardConfig
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import UsageKind, Visibility


_BASE = """\
class Base:
    def hook(self):
        return None


def callback(value):
    return value
"""

_WORKER = """\
from base import Base, callback

__all__ = ["Worker"]


class Worker(Base):
    def __init__(self):
        self.state = 1

    def hook(self):
        return self.state

    def _helper(self):
        return self.state

    def run(self):
        return self._helper()


def consume(fn):
    return fn


def main():
    return consume(callback)


def outer(value):
    def transform(item):
        return item + value

    return consume(transform)
"""


class TestRelationTopologyProjection(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "base.py").write_text(_BASE, encoding="utf-8")
        (self.root / "worker.py").write_text(_WORKER, encoding="utf-8")
        self.snapshot = directory_analysis_snapshot(self.root, GuardConfig())

    def test_projection_preserves_relation_graph_semantics(self) -> None:
        graph = RepositoryRelationGraph(self.snapshot)
        before_edges = frozenset(graph.edges)
        before_stats = graph.stats()

        topology = normalized_topology(graph)

        self.assertEqual(frozenset(graph.edges), before_edges)
        self.assertEqual(graph.stats(), before_stats)
        self.assertGreater(len(topology.edges), 0)

    def test_projection_adds_reuse_and_owner_facts(self) -> None:
        topology = normalized_topology(RepositoryRelationGraph(self.snapshot))
        symbols = {symbol.symbol_id: symbol for symbol in topology.symbols}
        edges = {(edge.source_id, edge.target_id, edge.kind) for edge in topology.edges}

        self.assertTrue(symbols["worker.Worker"].exported)
        self.assertEqual(
            symbols["worker.Worker._helper"].visibility, Visibility.INTERNAL
        )
        self.assertIn(
            ("worker.Worker", "base.Base", UsageKind.INHERITANCE),
            edges,
        )
        self.assertIn(
            ("worker.Worker.hook", "base.Base.hook", UsageKind.OVERRIDE),
            edges,
        )
        self.assertIn(
            ("worker.main", "base.callback", UsageKind.CALLBACK_REGISTRATION),
            edges,
        )
        field_id = "field:worker.Worker.state"
        self.assertIn(field_id, symbols)
        self.assertIn(
            ("worker.Worker.__init__", field_id, UsageKind.FIELD_ACCESS),
            edges,
        )
        self.assertIn(
            ("worker.Worker._helper", field_id, UsageKind.FIELD_ACCESS),
            edges,
        )
        self.assertEqual(symbols["worker.outer.transform"].kind, "function")
        self.assertIn(
            (
                "worker.outer",
                "worker.outer.transform",
                UsageKind.CALLBACK_REGISTRATION,
            ),
            edges,
        )
        self.assertIn(
            (
                "worker.outer.transform",
                "binding:worker.outer:value",
                UsageKind.CLOSURE_CAPTURE,
            ),
            edges,
        )
        components = topology.state_components("worker.Worker")
        self.assertEqual(len(components), 1)
        self.assertIn("field:worker.Worker.state", components[0])
        self.assertIn("worker.Worker.__init__", components[0])
        self.assertIn("worker.Worker._helper", components[0])
        self.assertIn("worker.Worker.hook", components[0])

    def test_local_class_owner_preserves_lexical_scope_for_state_facts(self) -> None:
        (self.root / "local_class.py").write_text(
            "def build():\n"
            "    class SpyBinder:\n"
            "        calls = 0\n"
            "        def require(self):\n"
            "            self.calls += 1\n"
            "            return self.calls\n"
            "    return SpyBinder().require()\n",
            encoding="utf-8",
        )
        snapshot = directory_analysis_snapshot(self.root, GuardConfig())
        topology = normalized_topology(RepositoryRelationGraph(snapshot))
        symbols = {symbol.symbol_id: symbol for symbol in topology.symbols}
        edges = {(edge.source_id, edge.target_id, edge.kind) for edge in topology.edges}

        class_id = "local_class.build.SpyBinder"
        method_id = f"{class_id}.require"
        field_id = f"field:{class_id}.calls"
        self.assertIn(class_id, symbols)
        self.assertIn(method_id, symbols)
        self.assertIn(field_id, symbols)
        self.assertIn((method_id, field_id, UsageKind.FIELD_ACCESS), edges)

    def test_bare_name_resolution_stays_inside_python_lexical_scope(self) -> None:
        (self.root / "other.py").write_text(
            "def unrelated():\n    is_valid = True\n    return is_valid\n",
            encoding="utf-8",
        )
        (self.root / "lexical.py").write_text(
            "def outer(value):\n"
            "    def is_valid(item):\n"
            "        return bool(item)\n"
            "    def inner(item):\n"
            "        return is_valid(item)\n"
            "    return inner(value)\n",
            encoding="utf-8",
        )
        (self.root / "class_scope.py").write_text(
            "class Worker:\n"
            "    def helper(self):\n"
            "        return 1\n"
            "    def run(self):\n"
            "        return helper()\n",
            encoding="utf-8",
        )
        snapshot = directory_analysis_snapshot(self.root, GuardConfig())
        topology = normalized_topology(RepositoryRelationGraph(snapshot))

        incoming = topology.incoming(
            "lexical.outer.is_valid",
            (UsageKind.DIRECT_CALL, UsageKind.CALLABLE_REFERENCE),
        )
        self.assertEqual(
            [(edge.source_id, edge.kind) for edge in incoming],
            [("lexical.outer.inner", UsageKind.DIRECT_CALL)],
        )
        class_incoming = topology.incoming(
            "class_scope.Worker.helper",
            (UsageKind.DIRECT_CALL, UsageKind.CALLABLE_REFERENCE),
        )
        self.assertEqual(class_incoming, ())


if __name__ == "__main__":
    unittest.main()


class TestStaticDependencyProtocolProjection(unittest.TestCase):
    def test_external_stub_protocol_hooks_are_resolved_without_importing(self) -> None:
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            deps = root / "deps"
            project = root / "project"
            (deps / "fakefw").mkdir(parents=True)
            project.mkdir()
            (deps / "fakefw" / "__init__.py").write_text(
                "from .layers import Layer\nraise RuntimeError('must not execute')\n",
                encoding="utf-8",
            )
            (deps / "fakefw" / "layers.pyi").write_text(
                "class Layer:\n"
                "    def build(self, shape): ...\n"
                "    def call(self, inputs): ...\n",
                encoding="utf-8",
            )
            (project / "model.py").write_text(
                "from fakefw import Layer\n\n"
                "class Model(Layer):\n"
                "    def build(self, shape):\n"
                "        self.shape = shape\n\n"
                "    def call(self, inputs):\n"
                "        return inputs\n\n"
                "    def helper(self):\n"
                "        return 1\n",
                encoding="utf-8",
            )
            snapshot = directory_analysis_snapshot(project, GuardConfig())
            graph = RepositoryRelationGraph(snapshot)
            resolver = StaticPythonDependencyResolver((deps,))

            topology = normalized_topology(graph, resolver)
            edges = {
                (edge.source_id, edge.target_id, edge.kind) for edge in topology.edges
            }

            self.assertIn(
                (
                    "model.Model",
                    "external:fakefw.Layer",
                    UsageKind.INHERITANCE,
                ),
                edges,
            )
            self.assertIn(
                (
                    "model.Model.build",
                    "external:fakefw.Layer.build",
                    UsageKind.PROTOCOL_HOOK,
                ),
                edges,
            )
            self.assertIn(
                (
                    "model.Model.call",
                    "external:fakefw.Layer.call",
                    UsageKind.PROTOCOL_HOOK,
                ),
                edges,
            )
            self.assertNotIn(
                (
                    "model.Model.helper",
                    "external:fakefw.Layer.helper",
                    UsageKind.PROTOCOL_HOOK,
                ),
                edges,
            )


class TestKerasStyleStaticProtocolProjection(unittest.TestCase):
    def test_tensorflow_alias_and_stub_hooks_resolve_without_runtime_import(
        self,
    ) -> None:
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            deps = root / "deps"
            project = root / "project"
            (deps / "tensorflow" / "keras").mkdir(parents=True)
            project.mkdir()
            (deps / "tensorflow" / "__init__.py").write_text(
                "raise RuntimeError('must not import tensorflow')\n",
                encoding="utf-8",
            )
            (deps / "tensorflow" / "keras" / "layers.pyi").write_text(
                "class Layer:\n"
                "    def build(self, input_shape): ...\n"
                "    def call(self, inputs): ...\n",
                encoding="utf-8",
            )
            (project / "model.py").write_text(
                "import tensorflow as tf\n\n"
                "class SoftThreshold(tf.keras.layers.Layer):\n"
                "    def build(self, input_shape):\n"
                "        self.shape = input_shape\n\n"
                "    def call(self, inputs):\n"
                "        return inputs\n",
                encoding="utf-8",
            )
            snapshot = directory_analysis_snapshot(project, GuardConfig())
            resolver = StaticPythonDependencyResolver((deps,))
            topology = normalized_topology(RepositoryRelationGraph(snapshot), resolver)
            edges = {
                (edge.source_id, edge.target_id, edge.kind) for edge in topology.edges
            }

            self.assertIn(
                (
                    "model.SoftThreshold",
                    "external:tensorflow.keras.layers.Layer",
                    UsageKind.INHERITANCE,
                ),
                edges,
            )
            self.assertIn(
                (
                    "model.SoftThreshold.build",
                    "external:tensorflow.keras.layers.Layer.build",
                    UsageKind.PROTOCOL_HOOK,
                ),
                edges,
            )
            self.assertIn(
                (
                    "model.SoftThreshold.call",
                    "external:tensorflow.keras.layers.Layer.call",
                    UsageKind.PROTOCOL_HOOK,
                ),
                edges,
            )
