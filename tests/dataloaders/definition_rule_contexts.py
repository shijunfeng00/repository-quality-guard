"""Data-source adapter for shared, non-serializable definition_batch7 rule contexts.

These source-backed contexts contain callbacks or graph identities; converting
rows at iteration is necessary where a plain Pickle record cannot represent
an invocation. No production rule is invoked by this loader.
"""
from __future__ import annotations
from rqgtest import JSONDataLoader
import ast
import json
from pathlib import Path
from runtime.src.config import GuardConfig
from runtime.src.definition_rule_objects import BooleanModeMatrixRule, DeepNestingRule, DefinitionRuleContext, DuplicateBodyRule, EphemeralHelperRule, FragmentedOwnerRule, HighBranchCountRule, ImportCycleRule, MissingDocstringRule, MissingReturnsSectionRule, ModuleLineLimitRule, ModuleRuleContext, OvergrownClassRule, OversizedFunctionRule, RepositoryDefinitionContext, RepositoryModuleContext, ShortDocstringRule, SmallLowUseClassRule, SuppressionMarkerRule, UndocumentedParametersRule, UnusedPrivateRule, WideFunctionInterfaceRule
from runtime.src.facts import ModuleFacts
from runtime.src.model import Definition
from runtime.src.topology_facts import RepositoryTopology

class DefinitionRuleContextLoader(JSONDataLoader):
    def __init__(self, path, rule):
        super().__init__(path)
        self.rule = rule
        self.uncertainty = "uncertainty" in str(path)

    def _iter(self, path):
        def _optional_take(mapping, key, default):
            """Consume an explicitly optional definition attribute when present.

            Args:
                mapping: A mutable copy of the source definition attributes.
                key: The attribute to remove only when declared.
                default: The documented default for absent optional attributes.

            Returns:
                Authored value or the declared default, with no silent deletion.
            """
            if key in mapping:
                return mapping.pop(key)
            return default
        def definition_input(row):
            """Build rule-specific definition contexts from authored source metadata.

            Args:
                row: Frozen rule ID, definition attributes, and usage observations.

            Returns:
                Invocation arguments containing the native definition rule context.
            """
            code, data = (row['rule'], row['facts'])
            config = GuardConfig(**data.get('config', {}))
            usage = {'direct_callers': 1, 'direct_call_sites': 1, 'callable_consumers': 0, 'protocol_edges': 0, 'visibility': 'private', 'exported': False, 'owner_id': 'fixture.Owner', 'nested': False, 'topology_resolution': 'normalized'}
            usage.update(data.get('usage', {}))

            def usage_lookup(_definition):
                """Return authored usage evidence for the evaluated definition.

                Args:
                    _definition: Definition selected by the production rule.

                Returns:
                    Frozen usage observations for this fixture.
                """
                return usage
            if code in {'QG013', 'QG021', 'QG156'}:
                if code == 'QG013':
                    definitions = [Definition(module='fixture', qualname=f'_helper{i}', name=f'_helper{i}', kind='function', path=Path('fixture.py'), line=1, end_line=1, column=1, code_line_count=2) for i in range(data['helper_count'])]
                    topology = RepositoryTopology((), (), ())
                elif code == 'QG021':
                    definitions = [Definition(module='fixture', qualname=f'function{i}', name=f'function{i}', kind='function', path=Path('fixture.py'), line=1, end_line=1, column=1, code_line_count=8, body_fingerprint='same') for i in range(data['duplicate_count'])]
                    topology = None
                else:
                    attrs = dict(data['definition'])
                    name = _optional_take(attrs, 'name', 'entry')
                    definitions = [Definition(module='fixture', qualname=name, name=name, kind=_optional_take(attrs, 'kind', 'function'), path=Path('fixture.py'), line=1, end_line=1, column=1, code_line_count=_optional_take(attrs, 'code_line_count', 6), **attrs)]
                    topology = RepositoryTopology((), (), ())
                context = RepositoryDefinitionContext(definitions, config, topology, usage_lookup)
            elif code in {'QG019', 'QG020'}:
                source = data['source']
                context = ModuleRuleContext(ModuleFacts(path=Path('fixture.py'), module='fixture', source=source, tree=ast.parse(source)), config)
            elif code == 'QG022':
                first = ModuleFacts(path=Path('a.py'), module='a', source='import b', tree=ast.parse('import b'))
                second = ModuleFacts(path=Path('b.py'), module='b', source='import a', tree=ast.parse('import a'))
                first.imports = {'b': 'b'}
                second.imports = {'a': 'a'} if data['cycle'] else {}
                context = RepositoryModuleContext([first, second])
            else:
                attrs = dict(data['definition'])
                name = _optional_take(attrs, 'name', 'entry')
                definition = Definition(module='fixture', qualname=name, name=name, kind=_optional_take(attrs, 'kind', 'function'), path=Path('fixture.py'), line=1, end_line=1, column=1, code_line_count=_optional_take(attrs, 'code_line_count', 6), **attrs)
                context = DefinitionRuleContext(definition, config, usage_lookup, bool(data.get('private', False)))
            return ((context,), {})
        def uncertainty_input(row):
            """Build native definition contexts with explicit topology uncertainty.

            Args:
                row: Frozen edge resolution, usage counts, and expected rule identity.

            Returns:
                One evaluator context that does not execute the production rule.
            """
            count = int(row['count'])
            definitions = [Definition(module='fixture', qualname=f'_helper_{i}', name=f'_helper_{i}', kind='function', path=Path('fixture.py'), line=i + 1, end_line=i + 1, column=1, code_line_count=2) for i in range(count)]

            def usage(_definition):
                """Return the fixture's explicit topology-resolution observations.

                Args:
                    _definition: Definition whose static edges are under review.

                Returns:
                    Independent call site and uncertainty counters.
                """
                return {'direct_callers': 0, 'direct_call_sites': 0, 'callable_consumers': int(row['callable_consumers']), 'protocol_edges': 0, 'visibility': 'private', 'exported': False, 'owner_id': 'fixture.Owner', 'nested': False, 'topology_resolution': str(row['resolution'])}
            code = row['rule']
            if code == 'QG013':
                context = RepositoryDefinitionContext(definitions, GuardConfig(), RepositoryTopology((), (), ()), usage)
            elif code == 'QG156':
                context = RepositoryDefinitionContext(definitions, GuardConfig(), None, usage)
            else:
                context = DefinitionRuleContext(definitions[0], GuardConfig(), usage, True)
            return ((context,), {})
        for document in super()._iter(path):
            for row in document["cases"]:
                if row["rule"] == self.rule:
                    yield (uncertainty_input(row) if self.uncertainty else definition_input(row))
