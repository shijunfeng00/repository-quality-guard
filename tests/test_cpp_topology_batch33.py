"""Frozen C++ symbol and edge contracts from the real topology provider."""
from __future__ import annotations
import json
from operator import attrgetter
import shutil
import tempfile
import atexit
from pathlib import Path
from rqgtest import TestCase
from runtime.src.analysis_snapshot import LanguageUnit, RepositoryAnalysisSnapshot
from runtime.src.multilang_topology import normalized_multilang_topology

class CppTopologyBatch33Case(TestCase):
    """Compare real Clang-derived topology to authored symbol and edge oracles."""

    def get_enterpoint(self):
        """Run the actual normalized cross-language topology producer."""
        return normalized_multilang_topology

    def get_input_data(self):
        """Construct the original compiler-backed source snapshot for this Case."""
        rows = json.loads((Path(__file__).parent / 'cases' / 'b33-cpp-topology-v1.json').read_text(encoding='utf-8'))['cases']
        for row in rows:
            root = Path(tempfile.mkdtemp(prefix='rqg-b33-cpp-'))
            atexit.register(shutil.rmtree, root, ignore_errors=True)
            for relative, source in row['sources'].items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
            compile_spec = row['compile']
            if compile_spec:
                compiler = shutil.which(compile_spec['compiler'])
                if compiler is None and compile_spec['compiler'] == 'g++':
                    compiler = 'g++'
                if compiler is None:
                    raise RuntimeError('Native compiler is required: ' + compile_spec['compiler'])
                source = root / 'src/sample.cpp'
                build = root / 'build'
                build.mkdir()
                (build / 'compile_commands.json').write_text(json.dumps([{'directory': str(build), 'file': str(source), 'arguments': [compiler, *compile_spec['flags'], '-c', str(source)]}]), encoding='utf-8')
            snapshot = RepositoryAnalysisSnapshot(root, 'DIRECTORY', {}, {relative: LanguageUnit(relative, 'cpp', source) for relative, source in row['sources'].items()})
            yield ((snapshot,), {'script_facts': [], 'cpp_paths': row['cpp_paths']})

    def get_output_data(self):
        """Return the previously authored check values, never computed outputs."""
        rows = json.loads((Path(__file__).parent / 'cases' / 'b33-cpp-topology-v1.json').read_text(encoding='utf-8'))['cases']
        for row in rows:
            yield row['checks']

    def equals(self, ground_truth, output):
        """Compare complete source-grounded C++ symbols and typed relations."""
        actual = []
        for check in ground_truth:
            kind = check['kind']
            match kind:
                case 'symbol' | 'edge' | 'lambda_edges':
                    queries = [check['selector']] if kind == 'symbol' else [{'name': check['source']}] if kind == 'lambda_edges' else [check['source'], check['target']]
                    matches = [[symbol for symbol in output.symbols if all((str(symbol.owner_id).find(want) >= 0 if key == 'owner_contains' else symbol.name.startswith(want) if key == 'name_prefix' else symbol.name == want if key == 'name' else symbol.qualname == want if key == 'qualname' else False for key, want in query.items()))] for query in queries]
                    if any((len(item) != 1 for item in matches)):
                        return False
                    symbols = [item[0] for item in matches]
                    if kind == 'lambda_edges':
                        lambdas = sorted((symbol for symbol in output.symbols if symbol.name.startswith('<lambda@')), key=attrgetter('line'))
                        if len(lambdas) <= check['index']:
                            return False
                        symbols.append(lambdas[check['index']])
                    if kind == 'symbol':
                        attr = check['attr']
                        properties = {None: True, 'kind': symbols[0].kind, 'parameter_count': symbols[0].parameter_count, 'path': str(symbols[0].path), 'visibility': symbols[0].visibility.value}
                        if attr not in properties:
                            raise ValueError('Unsupported C++ symbol attribute: ' + str(attr))
                        value = properties[attr]
                    else:
                        value = any((edge.source_id == symbols[0].symbol_id and edge.target_id == symbols[1].symbol_id and (edge.kind.value == check['usage']) for edge in output.edges))
                case 'prefix_count':
                    value = sum((s.name.startswith(check['prefix']) for s in output.symbols))
                case 'all_prefix_nested':
                    value = all((s.nested for s in output.symbols if s.name.startswith(check['prefix'])))
                case 'qualname_absent':
                    value = not any((s.qualname == check['qualname'] for s in output.symbols))
                case 'name_absent':
                    value = not any((s.name == check['name'] for s in output.symbols))
                case 'edge_kind_count':
                    value = sum((e.kind.value == check['usage'] for e in output.edges))
                case 'symbol_count':
                    value = len(output.symbols)
                case 'edge_count':
                    value = len(output.edges)
                case _:
                    raise ValueError('Unsupported authored C++ check: ' + kind)
            actual.append(value)
        return actual == [check['expected'] for check in ground_truth]
