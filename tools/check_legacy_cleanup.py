#!/usr/bin/env python3
"""Deterministic and fail-closed RQG v0.22.6 legacy-layout cleanup gate.

This is a staging acceptance inspection; it does NOT grant RQG/UT47 release acceptance.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys


def main(root: Path) -> int:
    checks = {}
    for rel in ('dev-tests', 'tests-deprecated', 'runtime/contracts/python'):
        checks[f'legacy_directory_absent:{rel}'] = not (root / rel).exists()
    checks['deprecated_python_unit_absent'] = not (root / 'runtime/contracts/python/tests.py').exists()
    checks['modern_test_root_exists'] = (root / 'tests').is_dir()
    checks['native_adapter_retained_until_ir'] = all((root / p).is_file() for p in (
        'runtime/contracts/c/testunit.h', 'runtime/contracts/cpp/tests.hpp',
        'runtime/contracts/java/TestUnit.java'))

    # TS/Go remain first-class compile-time contracts until the multi-language IR
    # takes ownership.  The separate release smoke actually invokes both compilers.
    checks['typescript_interface_source_present'] = all((root / p).is_file() for p in (
        'runtime/contracts/typescript/testunit.ts',
        'tools/release_checks/fixtures/native-testunit/TypeScriptNativeCase.ts',
    ))
    checks['go_interface_source_present'] = all((root / p).is_file() for p in (
        'runtime/contracts/go/testunit.go',
        'runtime/contracts/go/testunit_test.go',
    ))
    checks['typescript_go_release_gate_wired'] = (
        'def _native_ts_go_interface_smoke(' in
        (root / 'tools/verify_release.py').read_text(encoding='utf-8')
        and '_native_ts_go_interface_smoke(bundle, workspace)' in
        (root / 'tools/verify_release.py').read_text(encoding='utf-8')
    )

    excluded = {'__pycache__', '.git', '.pytest_cache', '.ruff_cache'}
    files = [p for p in root.rglob('*') if p.is_file() and not (set(p.relative_to(root).parts) & excluded)
             and p.suffix in {'.py', '.md', '.json', '.toml', '.yml', '.yaml', '.sh'}]
    active = [p for p in files if p.suffix == '.py' and not p.relative_to(root).parts[0] == 'tests']
    actual_imports = []
    for p in active:
        try: tree = ast.parse(p.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeError): continue
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('runtime.contracts.python'):
                actual_imports.append(f'{p.relative_to(root)}:{n.lineno}')
            elif isinstance(n, ast.Import) and any(a.name.startswith('runtime.contracts.python') for a in n.names):
                actual_imports.append(f'{p.relative_to(root)}:{n.lineno}')
    checks['no_deprecated_python_contract_imports'] = not actual_imports

    # Don't confuse normal cross-repo analyzer taxonomy with RQG's own retired dirs.
    production = [p for p in files if p.relative_to(root).parts[0] in ('scripts','runtime','tools') and p != root / 'tools/check_legacy_cleanup.py']
    references = []
    for p in production:
        text = p.read_text(encoding='utf-8', errors='replace')
        for phrase in ('tests-deprecated/', 'runtime/contracts/python/tests.py', 'InstanceTestCase'):
            if phrase in text:
                references.append(f'{p.relative_to(root)}:{phrase}')
    checks['no_deprecated_runtime_references'] = not references

    # Retired QG identities must not survive as active rules or ordinary test
    # expectations.  QG050 is the sole exception: the frozen CORE-03 parity
    # baseline deliberately contains its *before*-state so the comparator can
    # prove that the current ledger removed it.  This is migration evidence,
    # not a QG050 detector or a positive QG050 test.
    scan_files = [p for p in files if p != root / 'tools/check_legacy_cleanup.py' and '__pycache__' not in p.parts and p.suffix != '.pyc']
    code_paths = {}
    for code in ('QG011', 'QG050', 'QG212'):
        code_paths[code] = sorted({p.relative_to(root).as_posix() for p in scan_files
                                   if re.search(rf'\b{code}\b', p.read_text(encoding='utf-8', errors='replace'))})
    checks['retired_qg011_absent_repository_wide'] = not code_paths['QG011']
    checks['retired_qg212_absent_repository_wide'] = not code_paths['QG212']
    qg050_allowed = {
        'tools/compare_finding_ledgers.py',
        'tests/cases/core03-finding-parity-v1.json',
    }
    checks['retired_qg050_references_confined_to_parity_migration'] = (
        set(code_paths['QG050']) == qg050_allowed
    )
    checks['retired_qg050_absent_from_production'] = not any(
        rel.startswith('runtime/') and rel in code_paths['QG050'] for rel in code_paths['QG050']
    )
    try:
        parity = json.loads((root/'tests/cases/core03-finding-parity-v1.json').read_text(encoding='utf-8'))
        qg050_before = 0
        qg050_current = 0
        qg050_expectation_mismatches = 0
        for case in parity['cases']:
            before = [f for f in case.get('baseline', {}).get('findings', []) if f.get('code') == 'QG050']
            current = [f for f in case.get('current', {}).get('findings', []) if f.get('code') == 'QG050']
            qg050_before += len(before)
            qg050_current += len(current)
            expected_removed = case.get('expected', {}).get('removed_retired_baseline_findings')
            if expected_removed is not None and expected_removed != len(before):
                qg050_expectation_mismatches += 1
        checks['retired_qg050_only_historical_baseline_rows'] = (
            qg050_before > 0 and qg050_current == 0 and qg050_expectation_mismatches == 0
            and all(case.get('case_id') != 'added-qg050-is-not-whitelisted' for case in parity['cases'])
        )
    except (KeyError, OSError, TypeError, ValueError):
        checks['retired_qg050_only_historical_baseline_rows'] = False
    checks['retired_qg050_old_positive_test_and_fixture_absent'] = (
        not (root/'tests/test_functional_batch28.py').exists()
        and not (root/'tests/cases/qg050-retired-functional-v1.json').exists()
    )

    verifier = root / 'tools/verify_release.py'
    text = verifier.read_text(encoding='utf-8') if verifier.exists() else ''
    checks['release_verifier_exists'] = bool(text)
    checks['no_legacy_release_fixture_path'] = bool(text) and 'dev-tests/' not in text and 'tests-deprecated/' not in text
    fixture_root = root / 'tools/release_checks/fixtures'
    expected_fixture_names = (
        'qg-evidence-lookup-cli-v1.json', 'b33-evidence-trace-v1.json',
        'b33-scanner-equivalence-v1.json', 'b32-graph-observable-v1.json',
        'native-oracle-b69.json', 'qg003-business-v1.json',
    )
    checks['release_oracles_present'] = all((fixture_root / name).is_file() for name in expected_fixture_names)
    checks['native_source_fixtures_present'] = (fixture_root/'cpp-testunit/CMakeLists.txt').is_file() and all(
        (fixture_root / 'native-testunit' / name).is_file() for name in (
            'JavaNativeCases.java','c_native_cases.c','EquivalentNativeCases.cpp','EquivalentNativeCases.java'))
    try:
        schema = json.loads((root/'references/test_case_schema_v1.json').read_text(encoding='utf-8'))
        result = json.loads((root/'references/test_result_schema_v1.json').read_text(encoding='utf-8'))
        checks['schema_case_id_only'] = 'case_id' in schema['required'] and 'id' not in schema['properties'] and 'case_id' in str(result)
    except (KeyError, OSError, ValueError) as exc:
        checks['schema_case_id_only'] = False
    manifest = root/'runtime/MANIFEST.sha256'
    checks['obsolete_python_manifest_entry_absent'] = manifest.is_file() and 'runtime/contracts/python/tests.py' not in manifest.read_text()
    # All required release smoke helpers must exist before claiming release verification.
    helpers = sorted(set(re.findall(r'tools/release_checks/([\w-]+\.py)', text)))
    missing_helpers = [p for p in helpers if not (root/'tools/release_checks'/p).is_file()]
    checks['release_helpers_exist'] = not missing_helpers
    # Release verifier must use the existing lifecycle termination primitive,
    # never import a removed or invented process runner.
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            '_rqg_process_lifecycle_gate', root/'runtime/src/process_lifecycle.py'
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        checks['release_process_lifecycle_import'] = (
            hasattr(module, 'terminate_process_tree')
            and 'from runtime.src.process_lifecycle import terminate_process_tree' in text
            and 'run_bounded_command' not in text
        )
    except Exception:
        checks['release_process_lifecycle_import'] = False
    data = {
        'schema': 'rqg.cleanup-checklist.v1',
        'checks': checks,
        'passed': sum(checks.values()),
        'total': len(checks),
        'blocking': [key for key,value in checks.items() if not value],
        'missing_release_helpers': missing_helpers,
        'deprecated_imports': actual_imports,
        'deprecated_references': references,
        'release_status': 'BLOCKED' if not all(checks.values()) else 'STRUCTURAL_ONLY_PASS',
    }
    print(json.dumps(data,ensure_ascii=False,indent=2))
    return 0 if all(checks.values()) else 1

if __name__=='__main__':
    sys.exit(main(Path(sys.argv[1]).resolve()))
