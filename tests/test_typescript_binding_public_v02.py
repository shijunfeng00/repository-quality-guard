"""TypeScript public normalized topology: independent alias and receiver targets."""
from __future__ import annotations
import json
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
from runtime.src.multilang_topology import normalized_multilang_topology
from runtime.src.topology_facts import UsageKind


class TypeScriptPublicBindingCase(TestCase):
    """Distinguish sourced TypeScript aliases, receiver bindings, and uncertain shadows."""

    def get_enterpoint(self):
        """Return public production topology entrypoint, not a test-owned adapter."""
        return normalized_multilang_topology

    def get_input_data(self):
        """Use original authored TypeScript sources; parser runs inside the target."""
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
            Path('/repo'), 'TEST', {},
            {path: LanguageUnit(path, 'typescript', code) for path, code in sources.items()},
        )
        yield ((snapshot,), {})

    def get_output_data(self):
        """Read independent, frozen edge expectations without executing the target."""
        doc = json.loads((Path(__file__).parent / 'cases' / 'typescript-binding-observable-v1.json').read_text(encoding='utf-8'))
        for row in doc['cases']:
            yield row['expected']

    def equals(self, expected, actual):
        """Compare public symbol and edge evidence without inventing runtime bindings."""
        symbols = {item.symbol_id: item for item in actual.symbols}
        triples = {(symbols[edge.source_id].qualname, symbols[edge.target_id].qualname, edge.kind) for edge in actual.edges}
        observed = {
            'alias_reexport': ('trigger', 'emit', UsageKind.MAY_DISPATCH) in triples,
            'first_receiver': ('First.execute', 'First.run', UsageKind.MAY_DISPATCH) in triples,
            'second_receiver': ('Second.execute', 'Second.run', UsageKind.MAY_DISPATCH) in triples,
            'shadow_unbound': not any(source == 'shadow' for source, _, _ in triples),
            'no_false_static_direct': not any(kind == UsageKind.DIRECT_CALL for _, _, kind in triples),
        }
        return observed == expected
