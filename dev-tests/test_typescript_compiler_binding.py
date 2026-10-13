"""Native TypeScript symbol-binding behavior, not same-spelling heuristics."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
from runtime.src.multilang import node_facts
from runtime.src.multilang_topology import normalized_multilang_topology
from runtime.src.topology_facts import UsageKind


class CompilerBindingTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("RQG_NODE_MODULES"), "locked TypeScript missing"
    )
    def test_alias_reexport_and_class_method_identity(self) -> None:
        sources = {
            "lib.ts": "export function emit(){return 1}\n",
            "bridge.ts": 'export {emit as relay} from "./lib";\n',
            "run.ts": (
                'import {relay as execute} from "./bridge";\n'
                "class First { run(){return 1} execute(){return this.run()} }\n"
                "class Second { run(){return 2} execute(){return this.run()} }\n"
                "function trigger(){return execute()}\n"
                "function shadow(execute:()=>number){return execute()}\n"
            ),
        }
        snapshot = RepositoryAnalysisSnapshot(
            Path("/repo"),
            "TEST",
            {},
            {
                path: LanguageUnit(path, "typescript", code)
                for path, code in sources.items()
            },
        )
        topo = normalized_multilang_topology(
            snapshot, script_facts=node_facts(snapshot)
        )
        symbols = {s.symbol_id: s for s in topo.symbols}
        triples = {
            (symbols[e.source_id].qualname, symbols[e.target_id].qualname, e.kind)
            for e in topo.edges
        }
        self.assertIn(("trigger", "emit", UsageKind.MAY_DISPATCH), triples)
        self.assertIn(("First.execute", "First.run", UsageKind.MAY_DISPATCH), triples)
        self.assertIn(("Second.execute", "Second.run", UsageKind.MAY_DISPATCH), triples)
        self.assertFalse(any(source == "shadow" for source, _, _ in triples))
        self.assertFalse(any(kind == UsageKind.DIRECT_CALL for _, _, kind in triples))


if __name__ == "__main__":
    unittest.main()
