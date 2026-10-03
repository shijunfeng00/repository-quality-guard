from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
from runtime.src.multilang_topology import normalized_multilang_topology
from runtime.src.topology_facts import UsageKind, Visibility


class TestScriptTopologyAdapter(unittest.TestCase):
    def test_script_facts_use_normalized_schema_without_guessing_visibility(
        self,
    ) -> None:
        snapshot = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="DIRECTORY",
            units={},
            language_units={
                "src/app.ts": LanguageUnit("src/app.ts", "typescript", ""),
            },
        )
        facts = [
            {
                "path": "src/app.ts",
                "language": "typescript",
                "definitions": [
                    {
                        "name": "helper",
                        "qualname": "helper",
                        "kind": "function",
                        "scope_key": "source-file",
                        "line": 1,
                        "end_line": 3,
                        "parameter_count": 1,
                        "calls": [],
                    },
                    {
                        "name": "run",
                        "qualname": "run",
                        "kind": "function",
                        "scope_key": "source-file",
                        "line": 5,
                        "end_line": 8,
                        "parameter_count": 0,
                        "calls": ["helper"],
                    },
                ],
            }
        ]
        topology = normalized_multilang_topology(snapshot, script_facts=facts)
        helper = next(item for item in topology.symbols if item.name == "helper")
        run = next(item for item in topology.symbols if item.name == "run")
        self.assertEqual(helper.visibility, Visibility.UNKNOWN)
        self.assertTrue(
            any(
                edge.source_id == run.symbol_id
                and edge.target_id == helper.symbol_id
                and edge.kind is UsageKind.DIRECT_CALL
                for edge in topology.edges
            )
        )

    def test_css_html_do_not_emit_callable_topology(self) -> None:
        snapshot = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="DIRECTORY",
            units={},
            language_units={
                "style.css": LanguageUnit("style.css", "css", ".x { color: red; }"),
                "index.html": LanguageUnit("index.html", "html", "<main></main>"),
            },
        )
        topology = normalized_multilang_topology(snapshot, script_facts=[])
        self.assertEqual(topology.symbols, ())
        self.assertEqual(topology.owners, ())
        self.assertEqual(topology.edges, ())


@unittest.skipUnless(shutil.which("clang++") or shutil.which("clang"), "clang required")
class TestCppTopologyAdapter(unittest.TestCase):
    def _topology(self, source: str):
        snapshot = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="DIRECTORY",
            units={},
            language_units={
                "src/sample.cpp": LanguageUnit("src/sample.cpp", "cpp", source)
            },
        )
        return normalized_multilang_topology(
            snapshot,
            script_facts=[],
            cpp_paths=["src/sample.cpp"],
        )

    def test_clang_maps_owner_calls_fields_overrides_and_callable_references(
        self,
    ) -> None:
        topology = self._topology(
            """namespace app {
class Base {
public:
    virtual void tick(int value) = 0;
};
class Worker : public Base {
private:
    int count_ = 0;
    void helper() {
        count_++;
    }
public:
    void tick(int value) override {
        helper();
        if (value) {
            count_ += value;
        }
    }
    static void invoke(void (*callback)()) {
        callback();
    }
};
void free_helper() {}
void run() {
    Worker worker;
    worker.tick(1);
    auto callback = &free_helper;
    Worker::invoke(callback);
}
}
"""
        )
        by_name = {}
        for symbol in topology.symbols:
            by_name.setdefault(symbol.name, []).append(symbol)
        helper = by_name["helper"][0]
        tick = next(
            item
            for item in by_name["tick"]
            if item.owner_kind.value == "class" and "Worker" in item.owner_id
        )
        base_tick = next(item for item in by_name["tick"] if "Base" in item.owner_id)
        run = by_name["run"][0]
        free_helper = by_name["free_helper"][0]
        field = by_name["count_"][0]
        worker = next(
            item
            for item in topology.symbols
            if item.name == "Worker" and item.kind == "class"
        )
        base = next(
            item
            for item in topology.symbols
            if item.name == "Base" and item.kind == "class"
        )

        self.assertEqual(helper.visibility, Visibility.PRIVATE)
        self.assertEqual(tick.visibility, Visibility.PUBLIC)
        triples = {
            (edge.source_id, edge.target_id, edge.kind) for edge in topology.edges
        }
        self.assertIn(
            (tick.symbol_id, helper.symbol_id, UsageKind.DIRECT_CALL), triples
        )
        self.assertIn(
            (helper.symbol_id, field.symbol_id, UsageKind.FIELD_ACCESS), triples
        )
        self.assertIn(
            (tick.symbol_id, field.symbol_id, UsageKind.FIELD_ACCESS), triples
        )
        self.assertIn(
            (tick.symbol_id, base_tick.symbol_id, UsageKind.OVERRIDE), triples
        )
        self.assertIn(
            (worker.symbol_id, base.symbol_id, UsageKind.INHERITANCE), triples
        )
        self.assertIn(
            (run.symbol_id, free_helper.symbol_id, UsageKind.CALLABLE_REFERENCE),
            triples,
        )

    def test_clang_maps_lambda_reference_direct_call_and_callback_usage(self) -> None:
        topology = self._topology(
            """namespace app {
void consume(void (*callback)()) { callback(); }
void run() {
    auto local = []() {};
    local();
    consume(local);
    consume([]() {});
}
}
"""
        )
        run = next(item for item in topology.symbols if item.name == "run")
        lambdas = [
            item for item in topology.symbols if item.name.startswith("<lambda@")
        ]
        self.assertEqual(len(lambdas), 2)
        self.assertTrue(all(item.nested for item in lambdas))
        local = min(lambdas, key=lambda item: item.line)
        inline = max(lambdas, key=lambda item: item.line)
        triples = {
            (edge.source_id, edge.target_id, edge.kind) for edge in topology.edges
        }
        self.assertIn(
            (run.symbol_id, local.symbol_id, UsageKind.CALLABLE_REFERENCE), triples
        )
        self.assertIn((run.symbol_id, local.symbol_id, UsageKind.DIRECT_CALL), triples)
        self.assertIn(
            (run.symbol_id, local.symbol_id, UsageKind.CALLBACK_REGISTRATION), triples
        )
        self.assertIn(
            (run.symbol_id, inline.symbol_id, UsageKind.CALLBACK_REGISTRATION), triples
        )

    def test_clang_counts_parameters_on_authored_lambda(self) -> None:
        topology = self._topology(
            """void run() {
    auto transform = [](int value) { return value + 1; };
    (void)transform(1);
}
"""
        )
        nested = next(
            item for item in topology.symbols if item.name.startswith("<lambda@")
        )
        self.assertEqual(nested.parameter_count, 1)

    def test_clang_maps_named_concept_as_protocol_evidence(self) -> None:
        topology = self._topology(
            """namespace app {
template <typename T>
concept Incrementable = requires(T value) {
    ++value;
};
template <Incrementable T>
void bump(T& value) {
    ++value;
}
}
"""
        )
        concept = next(
            item for item in topology.symbols if item.name == "Incrementable"
        )
        bump = next(item for item in topology.symbols if item.name == "bump")
        self.assertEqual(concept.kind, "protocol")
        self.assertTrue(
            any(
                edge.source_id == bump.symbol_id
                and edge.target_id == concept.symbol_id
                and edge.kind is UsageKind.PROTOCOL_HOOK
                for edge in topology.edges
            )
        )

    def test_clang_discovers_unique_local_include_root_without_guessing_semantics(
        self,
    ) -> None:
        snapshot = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="DIRECTORY",
            units={},
            language_units={
                "src/sample.cpp": LanguageUnit(
                    "src/sample.cpp",
                    "cpp",
                    '#include "api.hpp"\nint run() { return project::api(); }\n',
                ),
                "vendor/api.hpp": LanguageUnit(
                    "vendor/api.hpp",
                    "cpp",
                    "#pragma once\nnamespace project { inline int api() { return 1; } }\n",
                ),
            },
        )
        topology = normalized_multilang_topology(
            snapshot,
            script_facts=[],
            cpp_paths=["src/sample.cpp"],
        )
        run = next(item for item in topology.symbols if item.name == "run")
        self.assertEqual(run.path, Path("src/sample.cpp"))

    def test_clang_does_not_add_unrelated_include_roots_globally(self) -> None:
        snapshot = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="DIRECTORY",
            units={},
            language_units={
                "src/sample.cpp": LanguageUnit(
                    "src/sample.cpp",
                    "cpp",
                    "#include <iostream>\nint run() { return 0; }\n",
                ),
                "vendor/include/wchar.h": LanguageUnit(
                    "vendor/include/wchar.h",
                    "cpp",
                    "#error unrelated vendored header must not shadow libc\n",
                ),
            },
        )
        topology = normalized_multilang_topology(
            snapshot,
            script_facts=[],
            cpp_paths=["src/sample.cpp"],
        )
        run = next(item for item in topology.symbols if item.name == "run")
        self.assertEqual(run.path, Path("src/sample.cpp"))

    def test_clang_keeps_successful_translation_units_when_peer_is_unavailable(
        self,
    ) -> None:
        snapshot = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="DIRECTORY",
            units={},
            language_units={
                "src/good.cpp": LanguageUnit(
                    "src/good.cpp",
                    "cpp",
                    "namespace demo {\nint good(int x) { return x + 1; }\n}\n",
                ),
                "src/bad.cpp": LanguageUnit(
                    "src/bad.cpp",
                    "cpp",
                    "namespace demo {\nint should_not_leak() { return 1; }\nint broken = ;\n}\n",
                ),
            },
        )
        topology = normalized_multilang_topology(
            snapshot,
            script_facts=[],
            cpp_paths=["src/good.cpp", "src/bad.cpp"],
        )
        names = {item.name for item in topology.symbols}
        self.assertIn("good", names)
        self.assertNotIn("should_not_leak", names)

    def test_missing_clang_keeps_cpp_semantics_na_instead_of_guessing(self) -> None:
        snapshot = RepositoryAnalysisSnapshot(
            root=Path("/repo"),
            label="DIRECTORY",
            units={},
            language_units={
                "src/sample.cpp": LanguageUnit(
                    "src/sample.cpp", "cpp", "static void helper() {}"
                )
            },
        )
        with patch("runtime.src.multilang_topology.shutil.which", return_value=None):
            topology = normalized_multilang_topology(snapshot, script_facts=[])
        self.assertEqual(topology.symbols, ())
        self.assertEqual(topology.edges, ())


if __name__ == "__main__":
    unittest.main()
