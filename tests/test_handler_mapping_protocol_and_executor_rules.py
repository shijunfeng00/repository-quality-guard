"""B30 source/graph contracts excluding invalid exec/eval and presentation side effects."""
from __future__ import annotations
from rqgtest import JSONDataLoader
import hashlib
from rqgtest import TestCase
from runtime.src.scanner import RepositoryScanner
from runtime.src.relation_graph import normalized_topology
from runtime.src.topology_facts import UsageKind

class B30HandlerSourceCase(TestCase):
    """Test source-authenticated QG211 positive and negative exception cases."""

    def get_enterpoint(self):
        """Use the real scanner rather than an exception runner wrapper."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Build original exception handler source modules for static analysis."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/handler-mapping-protocol-and-executor-rules.json')))['cases']:
            if row['query'] == 'handler_entry':
                _case_arg_1_row = row
                while True:
                    _case_arg_2_row = _case_arg_1_row
                    while True:
                        root = Path(tempfile.mkdtemp(prefix='rqg-b30-public-'))
                        atexit.register(shutil.rmtree, root, ignore_errors=True)
                        file = root / _case_arg_2_row['path']
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_text(_case_arg_2_row['source'], encoding='utf-8')
                        config = GuardConfig()
                        _case_value_2 = (root, config, directory_analysis_snapshot(root, config))
                        break
                        break
                    root, config, snapshot = _case_value_2
                    if _case_arg_1_row['query'] == 'handler_entry':
                        graph = normalized_topology(RepositoryRelationGraph(snapshot))
                        _case_value_1 = ((RepositoryScanner(root, config, snapshot, graph),), {})
                        break
                    _case_value_1 = ((RepositoryScanner(root, config, snapshot),), {})
                    break
                    break
                yield _case_value_1

    def get_output_data(self):
        """Preserve code line, source hash and confidence capability expectations."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/handler-mapping-protocol-and-executor-rules.json')))['cases']:
            if row['query'] == 'handler_entry':
                yield (row['source'], row['expected']['lines'], row['expected']['source_bound'], row['expected']['capabilities'])

    def equals(self, expected, actual):
        """Require precise QG211 source and capability witnesses without executing code."""
        source, lines, source_bound, capabilities = expected
        findings = [f for f in actual.findings if f.code == 'QG211']
        digest = hashlib.sha256(source.encode()).hexdigest()
        observed_bound = all((f.evidence['source_sha256'] == digest for f in findings))
        return [f.line for f in findings] == lines and observed_bound == source_bound and ([f.evidence['capability'] for f in findings] == capabilities)

class B30MappingStaticCase(TestCase):
    """Check QG003/QG004/QG026 static findings without exec/eval in the oracle."""

    def get_enterpoint(self):
        """Return real production scanner entrypoint."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Provide source mapping programs as static inputs, not executable outputs."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/handler-mapping-protocol-and-executor-rules.json')))['cases']:
            if row['query'] == 'mapping_behavior':
                _case_arg_3_row = row
                while True:
                    _case_arg_4_row = _case_arg_3_row
                    while True:
                        root = Path(tempfile.mkdtemp(prefix='rqg-b30-public-'))
                        atexit.register(shutil.rmtree, root, ignore_errors=True)
                        file = root / _case_arg_4_row['path']
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_text(_case_arg_4_row['source'], encoding='utf-8')
                        config = GuardConfig()
                        _case_value_4 = (root, config, directory_analysis_snapshot(root, config))
                        break
                        break
                    root, config, snapshot = _case_value_4
                    if _case_arg_3_row['query'] == 'handler_entry':
                        graph = normalized_topology(RepositoryRelationGraph(snapshot))
                        _case_value_3 = ((RepositoryScanner(root, config, snapshot, graph),), {})
                        break
                    _case_value_3 = ((RepositoryScanner(root, config, snapshot),), {})
                    break
                    break
                yield _case_value_3

    def get_output_data(self):
        """Retain independently authored expected mapping Finding sets."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/handler-mapping-protocol-and-executor-rules.json')))['cases']:
            if row['query'] == 'mapping_behavior':
                yield (row['expected']['findings'], row['forbidden'])

    def equals(self, expected, actual):
        """Require all selected mapping Finding codes and source lines."""
        truth, forbidden = expected
        selected = [[f.code, f.line] for f in actual.findings if f.code in {'QG003', 'QG004', 'QG026'}]
        if forbidden is not None:
            selected = [item for item in selected if item in forbidden]
        return selected == truth

class B30ProtocolHooksCase(TestCase):
    """Check callback protocol graph edges with original source owner."""

    def get_enterpoint(self):
        """Return real production normalized topology API."""
        return normalized_topology

    def get_input_data(self):
        """Construct concrete inherited/unrelated visitor source graphs."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/handler-mapping-protocol-and-executor-rules.json')))['cases']:
            if row['query'] == 'ast_protocol':
                _case_arg_5_row = row
                while True:
                    _case_arg_6_row = _case_arg_5_row
                    while True:
                        root = Path(tempfile.mkdtemp(prefix='rqg-b30-public-'))
                        atexit.register(shutil.rmtree, root, ignore_errors=True)
                        file = root / _case_arg_6_row['path']
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_text(_case_arg_6_row['source'], encoding='utf-8')
                        config = GuardConfig()
                        _case_value_6 = (root, config, directory_analysis_snapshot(root, config))
                        break
                        break
                    root, config, snapshot = _case_value_6
                    if _case_arg_5_row['query'] == 'ast_protocol':
                        _case_value_5 = ((RepositoryRelationGraph(snapshot), StaticPythonDependencyResolver.from_environment()), {})
                        break
                    _case_value_5 = ((RepositoryRelationGraph(snapshot),), {})
                    break
                    break
                yield _case_value_5

    def get_output_data(self):
        """Keep exact expected protocol callback source IDs."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/handler-mapping-protocol-and-executor-rules.json')))['cases']:
            if row['query'] == 'ast_protocol':
                yield row['expected']['protocol_hooks']

    def equals(self, expected, actual):
        """Reject nonprotocol visitor calls and missing true protocol hooks."""
        observed = [edge.source_id for edge in actual.edges if edge.source_id.endswith('Collector.visit_Name') and edge.kind == UsageKind.PROTOCOL_HOOK]
        return observed == expected

class B30ProtocolFalseEphemeralCase(TestCase):
    """Keep QG001 false-positive expectations separate from topology facts."""

    def get_enterpoint(self):
        """Return genuine scanner's public finding interface."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Prepare visitor source for scanner's independent Finding rule."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/handler-mapping-protocol-and-executor-rules.json')))['cases']:
            if row['query'] == 'ast_protocol':
                _case_arg_7_row = row
                while True:
                    _case_arg_8_row = _case_arg_7_row
                    while True:
                        root = Path(tempfile.mkdtemp(prefix='rqg-b30-public-'))
                        atexit.register(shutil.rmtree, root, ignore_errors=True)
                        file = root / _case_arg_8_row['path']
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_text(_case_arg_8_row['source'], encoding='utf-8')
                        config = GuardConfig()
                        _case_value_8 = (root, config, directory_analysis_snapshot(root, config))
                        break
                        break
                    root, config, snapshot = _case_value_8
                    if _case_arg_7_row['query'] == 'handler_entry':
                        graph = normalized_topology(RepositoryRelationGraph(snapshot))
                        _case_value_7 = ((RepositoryScanner(root, config, snapshot, graph),), {})
                        break
                    _case_value_7 = ((RepositoryScanner(root, config, snapshot),), {})
                    break
                    break
                yield _case_value_7

    def get_output_data(self):
        """Keep original negative QG001 Oracle by exact visitor symbol."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/handler-mapping-protocol-and-executor-rules.json')))['cases']:
            if row['query'] == 'ast_protocol':
                yield row['expected']['false_ephemeral']

    def equals(self, expected, actual):
        """Ensure protocol implementations never receive spurious QG001 findings."""
        observed = [f.symbol for f in actual.findings if f.code == 'QG001' and f.symbol.endswith('Collector.visit_Name')]
        return observed == expected

class B30ExecutorSchedulingCase(TestCase):
    """Test real callback scheduling facts rather than running trace/CLI in equals()."""

    def get_enterpoint(self):
        """Return public source topology analyzer."""
        return normalized_topology

    def get_input_data(self):
        """Build executor assignment, closure and rebinding source facts."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/handler-mapping-protocol-and-executor-rules.json')))['cases']:
            if row['query'] == 'executor_dispatch':
                _case_arg_9_row = row
                while True:
                    _case_arg_10_row = _case_arg_9_row
                    while True:
                        root = Path(tempfile.mkdtemp(prefix='rqg-b30-public-'))
                        atexit.register(shutil.rmtree, root, ignore_errors=True)
                        file = root / _case_arg_10_row['path']
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_text(_case_arg_10_row['source'], encoding='utf-8')
                        config = GuardConfig()
                        _case_value_10 = (root, config, directory_analysis_snapshot(root, config))
                        break
                        break
                    root, config, snapshot = _case_value_10
                    if _case_arg_9_row['query'] == 'ast_protocol':
                        _case_value_9 = ((RepositoryRelationGraph(snapshot), StaticPythonDependencyResolver.from_environment()), {})
                        break
                    _case_value_9 = ((RepositoryRelationGraph(snapshot),), {})
                    break
                    break
                yield _case_value_9

    def get_output_data(self):
        """Preserve authored callback scheduling and directness expectations."""
        import atexit
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from runtime.src.analysis_snapshot import directory_analysis_snapshot
        from runtime.src.config import GuardConfig
        from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
        from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
        from runtime.src.scanner import RepositoryScanner
        for row in next(iter(JSONDataLoader('tests/cases/handler-mapping-protocol-and-executor-rules.json')))['cases']:
            if row['query'] == 'executor_dispatch':
                yield (row['expected']['scheduled'], row['expected']['never_direct'])

    def equals(self, expected, actual):
        """Require open-world callback edges, not inferred immediate execution."""
        callbacks = [edge for edge in actual.edges if edge.kind == UsageKind.MAY_CALLBACK]
        observed = (bool(callbacks), all((edge.kind != UsageKind.DIRECT_CALL for edge in callbacks)))
        return observed == expected
