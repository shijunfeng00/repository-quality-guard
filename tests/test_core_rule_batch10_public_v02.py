"""Production module, wrapper and mapping rule behavior with native entrypoints."""
from __future__ import annotations
from rqgtest import JSONDataLoader
from rqgtest import PickleDataLoader
from rqgtest import TestCase
from runtime.src.private_boundary_rules import ModuleConstantNamingRule
from runtime.src.rules import DelegationWrapperRule, PrivateFacadeWrapperRule
from runtime.src.scanner import RepositoryInstructionUniquenessRule, RepositoryScanner
from runtime.src.visitor_rule_objects import GuardedMappingFallbackRule

class Batch10QG150Case(TestCase):
    """Prove authored QG150 positive/negative facts using real production ownership."""

    def get_enterpoint(self):
        """Return the real Core evaluator without a test adapter."""
        return ModuleConstantNamingRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core_rule_batch10_public_v02-input-oracle.pkl'):
            if record['case'] == 'Batch10QG150Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core_rule_batch10_public_v02-input-oracle.pkl'):
            if record['case'] == 'Batch10QG150Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require complete rule-projected Finding identity and location."""
        observed = tuple(((f.code, f.severity, f.path, f.line) for f in actual))
        return expected == observed

class Batch10QG157PositiveCase(TestCase):
    """Prove authored QG157 positive/negative facts using real production ownership."""

    def get_enterpoint(self):
        """Return the real Core evaluator without a test adapter."""
        return GuardedMappingFallbackRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core_rule_batch10_public_v02-input-oracle.pkl'):
            if record['case'] == 'Batch10QG157PositiveCase':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core_rule_batch10_public_v02-input-oracle.pkl'):
            if record['case'] == 'Batch10QG157PositiveCase':
                yield record['expected']

    def equals(self, expected, actual):
        """Require complete rule-projected Finding identity and location."""
        observed = tuple(((f.code, f.severity, f.path, f.line) for f in actual))
        return expected == observed

class Batch10QG157NegativeCase(TestCase):
    """Prove authored QG157 positive/negative facts using real production ownership."""

    def get_enterpoint(self):
        """Return the real Core evaluator without a test adapter."""
        return RepositoryScanner.scan

    def get_input_data(self):
        """Supply passive contexts for this rule and production entrypoint."""
        import ast
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.facts import ModuleFacts
        from runtime.src.policy_common import parse_modules
        from runtime.src.rules import DelegationRuleContext
        from runtime.src.visitor_rule_objects import MappingFallbackContext
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.model import Definition
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/core-rule-batch10-public.json')))['cases']:
            if row['rule'] == 'QG157' and row['kind'] == 'TN':
                _case_arg_1_row = row
                while True:
                    mode = _case_arg_1_row['mode']
                    if mode == 'instruction':
                        root = Path(tempfile.mkdtemp(prefix='rqg-instruction-test-'))
                        for name in _case_arg_1_row['files']:
                            target = root / name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text('instructions\n', encoding='utf-8')
                        _case_value_1 = ((root,), {})
                        break
                    if mode == 'wrapper':
                        definition = Definition(module='fixture', qualname='wrapper', name='wrapper', kind='function', path=Path('fixture.py'), line=2, end_line=3, column=1, code_line_count=2, wrapper_target='_secret', calls=1)
                        context = DelegationRuleContext(definition, bool(_case_arg_1_row['private']), False, bool(_case_arg_1_row['critical']), bool(_case_arg_1_row['reported']), {'incoming_overrides': [], 'outgoing_overrides': [], 'polymorphic_root_slot': False})
                        _case_value_1 = ((context,), {})
                        break
                    source = str(_case_arg_1_row['source'])
                    tree = ast.parse(source)
                    is_test = _case_arg_1_row['case_id'] == 'qg157-exception-tp'
                    path = Path('tests/fixture.py') if is_test else Path('fixture.py')
                    facts = ModuleFacts(path=path, module='fixture', source=source, tree=tree)
                    if mode == 'module':
                        _case_value_1 = ((parse_modules([facts])[0],), {})
                        break
                    if mode == 'mapping' and _case_arg_1_row['kind'] == 'TN':
                        root = Path(tempfile.mkdtemp(prefix='rqg-qg157-negative-'))
                        (root / 'fixture.py').write_text(source, encoding='utf-8')
                        scanner = RepositoryScanner(root, GuardConfig())
                        weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
                        _case_value_1 = ((scanner,), {})
                        break
                    if mode == 'mapping':
                        form = _case_arg_1_row['form']
                        kind = {'expression': ast.IfExp, 'assignment': ast.If, 'exception': ast.ExceptHandler}[form]
                        origin = next((n for n in ast.walk(tree) if isinstance(n, kind)))
                        context = MappingFallbackContext(facts, origin, 'sample', str(_case_arg_1_row['receiver']), str(_case_arg_1_row['field']), bool(_case_arg_1_row['confirmed']), form)
                        _case_value_1 = ((context,), {})
                        break
                    raise ValueError('Unknown public Core rule mode: ' + str(mode))
                    break
                yield _case_value_1

    def get_output_data(self):
        """Read independently frozen Finding code, path and line predictions."""
        import ast
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.facts import ModuleFacts
        from runtime.src.policy_common import parse_modules
        from runtime.src.rules import DelegationRuleContext
        from runtime.src.visitor_rule_objects import MappingFallbackContext
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.model import Definition
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/core-rule-batch10-public.json')))['cases']:
            if row['rule'] == 'QG157' and row['kind'] == 'TN':
                yield tuple((tuple(item) for item in row['expected']))

    def equals(self, expected, actual):
        """Require complete rule-projected Finding identity and location."""
        observed = tuple(((f.code, f.severity, f.path, f.line) for f in actual.findings if f.code == 'QG157'))
        return expected == observed

class Batch10QG187Case(TestCase):
    """Prove authored QG187 positive/negative facts using real production ownership."""

    def get_enterpoint(self):
        """Return the real Core evaluator without a test adapter."""
        return RepositoryInstructionUniquenessRule().evaluate

    def get_input_data(self):
        """Supply passive contexts for this rule and production entrypoint."""
        import ast
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.facts import ModuleFacts
        from runtime.src.policy_common import parse_modules
        from runtime.src.rules import DelegationRuleContext
        from runtime.src.visitor_rule_objects import MappingFallbackContext
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.model import Definition
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/core-rule-batch10-public.json')))['cases']:
            if row['rule'] == 'QG187' and True:
                _case_arg_2_row = row
                while True:
                    mode = _case_arg_2_row['mode']
                    if mode == 'instruction':
                        root = Path(tempfile.mkdtemp(prefix='rqg-instruction-test-'))
                        for name in _case_arg_2_row['files']:
                            target = root / name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text('instructions\n', encoding='utf-8')
                        _case_value_2 = ((root,), {})
                        break
                    if mode == 'wrapper':
                        definition = Definition(module='fixture', qualname='wrapper', name='wrapper', kind='function', path=Path('fixture.py'), line=2, end_line=3, column=1, code_line_count=2, wrapper_target='_secret', calls=1)
                        context = DelegationRuleContext(definition, bool(_case_arg_2_row['private']), False, bool(_case_arg_2_row['critical']), bool(_case_arg_2_row['reported']), {'incoming_overrides': [], 'outgoing_overrides': [], 'polymorphic_root_slot': False})
                        _case_value_2 = ((context,), {})
                        break
                    source = str(_case_arg_2_row['source'])
                    tree = ast.parse(source)
                    is_test = _case_arg_2_row['case_id'] == 'qg157-exception-tp'
                    path = Path('tests/fixture.py') if is_test else Path('fixture.py')
                    facts = ModuleFacts(path=path, module='fixture', source=source, tree=tree)
                    if mode == 'module':
                        _case_value_2 = ((parse_modules([facts])[0],), {})
                        break
                    if mode == 'mapping' and _case_arg_2_row['kind'] == 'TN':
                        root = Path(tempfile.mkdtemp(prefix='rqg-qg157-negative-'))
                        (root / 'fixture.py').write_text(source, encoding='utf-8')
                        scanner = RepositoryScanner(root, GuardConfig())
                        weakref.finalize(scanner, shutil.rmtree, root, ignore_errors=True)
                        _case_value_2 = ((scanner,), {})
                        break
                    if mode == 'mapping':
                        form = _case_arg_2_row['form']
                        kind = {'expression': ast.IfExp, 'assignment': ast.If, 'exception': ast.ExceptHandler}[form]
                        origin = next((n for n in ast.walk(tree) if isinstance(n, kind)))
                        context = MappingFallbackContext(facts, origin, 'sample', str(_case_arg_2_row['receiver']), str(_case_arg_2_row['field']), bool(_case_arg_2_row['confirmed']), form)
                        _case_value_2 = ((context,), {})
                        break
                    raise ValueError('Unknown public Core rule mode: ' + str(mode))
                    break
                yield _case_value_2

    def get_output_data(self):
        """Read independently frozen Finding code, path and line predictions."""
        import ast
        import json
        import shutil
        import tempfile
        import weakref
        from pathlib import Path
        from runtime.src.facts import ModuleFacts
        from runtime.src.policy_common import parse_modules
        from runtime.src.rules import DelegationRuleContext
        from runtime.src.visitor_rule_objects import MappingFallbackContext
        from runtime.src.scanner import RepositoryScanner
        from runtime.src.model import Definition
        from runtime.src.config import GuardConfig
        for row in next(iter(JSONDataLoader('tests/cases/core-rule-batch10-public.json')))['cases']:
            if row['rule'] == 'QG187' and True:
                yield tuple((tuple(item) for item in row['expected']))

    def equals(self, expected, actual):
        """Require complete rule-projected Finding identity and location."""
        observed = tuple(((f.code, f.severity, f.path, f.line) for f in actual))
        return expected == observed

class Batch10QG010Case(TestCase):
    """Prove authored QG010 positive/negative facts using real production ownership."""

    def get_enterpoint(self):
        """Return the real Core evaluator without a test adapter."""
        return DelegationWrapperRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core_rule_batch10_public_v02-input-oracle.pkl'):
            if record['case'] == 'Batch10QG010Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core_rule_batch10_public_v02-input-oracle.pkl'):
            if record['case'] == 'Batch10QG010Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require complete rule-projected Finding identity and location."""
        observed = tuple(((f.code, f.severity, f.path, f.line) for f in actual))
        return expected == observed

class Batch10QG185Case(TestCase):
    """Prove authored QG185 positive/negative facts using real production ownership."""

    def get_enterpoint(self):
        """Return the real Core evaluator without a test adapter."""
        return PrivateFacadeWrapperRule().evaluate

    def get_input_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core_rule_batch10_public_v02-input-oracle.pkl'):
            if record['case'] == 'Batch10QG185Case':
                yield record['input']

    def get_output_data(self):
        """Load a frozen, independent rule input or authored expected record."""
        for record in PickleDataLoader('tests/cases/core_rule_batch10_public_v02-input-oracle.pkl'):
            if record['case'] == 'Batch10QG185Case':
                yield record['expected']

    def equals(self, expected, actual):
        """Require complete rule-projected Finding identity and location."""
        observed = tuple(((f.code, f.severity, f.path, f.line) for f in actual))
        return expected == observed
