"""Data-source adapter for shared, non-serializable batch6 rule contexts.

These source-backed contexts contain callbacks or graph identities; converting
rows at iteration is necessary where a plain Pickle record cannot represent
an invocation. No production rule is invoked by this loader.
"""
from __future__ import annotations
from rqgtest import JSONDataLoader
import json
from pathlib import Path
import ast
from pathlib import Path
import tempfile
import shutil
import weakref
from runtime.src.analysis_snapshot import directory_analysis_snapshot
from runtime.src.architecture_rules import ArchitectureSignalContext, ArchitectureDefinitionContext, SingleImplementationAbstractRule, DuplicateConfigSourcesRule, DuplicateConfigDefaultsRule, DuplicatePromptTextRule, UnusedDeprecatedDefinitionRule, UnusedModuleConstantRule
from runtime.src.call_chain_rules import SingleUseChainContext, SingleUseChainRule
from runtime.src.class_quality_rules import ClassShapeContext, MemberRuleContext, ClassRuleContext, SinglePublicMethodClassRule, PrivateHelperDominanceRule, StaticMethodDominanceRule, UnusedInstanceStateRule, SingleFieldDataclassRule, PassThroughMemberRule, ConstructorDependencyRule, FieldLocalityRule, class_methods, method_does_not_use_self
from runtime.src.config import GuardConfig
from runtime.src.facts import ModuleFacts
from runtime.src.model import Definition
from runtime.src.agent_rules import UndeclaredStateReadRuleContext, UndeclaredStateReadRule, UndeclaredStateWriteRuleContext, UndeclaredStateWriteRule, NestedSequenceStateWriteRuleContext, NestedSequenceStateWriteRule, StateScopeEscapeRuleContext, StateScopeEscapeRule, UnscopedStateUpdateEntryRuleContext, UnscopedStateUpdateEntryRule, StateMergeAmbiguityRuleContext, StateMergeAmbiguityRule, InconsistentStateWriteTypeRuleContext, InconsistentStateWriteTypeRule, DirectToolStateMutationContext, DirectToolStateMutationRule, ToolReturnContractContext, BareToolStringRule, UndocumentedToolResultRule, HistoryTraceRuleContext, MixedHistoryTraceRule, FailureHistoryPollutionRule
from runtime.src.policy_common import ParsedModule, RepositorySignals, StateContract, StateField, direct_body_nodes, call_name
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
from runtime.src.state_boundary_rules import StateBoundaryRuleContext, StateMutationBoundaryRule, DynamicExecutionContext, DynamicExecutionBoundaryRule

class CoreRuleContextLoader(JSONDataLoader):
    def __init__(self, path, rule):
        super().__init__(path)
        self.rule = rule

    def _iter(self, path):
        def input_from_source(row):
            """Construct genuine evaluator contexts without executing production rules.

            Args:
                row: An authored source module and the required context-selection rule.

            Returns:
                Evaluator positional and keyword arguments for one fixture record.
            """
            source = str(row['source'])
            code, kind = (str(row['rule']), str(row['kind']))
            tree = ast.parse(source)
            path = Path('subject.py')
            facts = ModuleFacts(path=path, module='subject', source=source, tree=tree)
            config = GuardConfig(require_docstrings=False, require_docstring_sections=False)
            signals = RepositorySignals()
            class_rules = {'QG080': SinglePublicMethodClassRule, 'QG081': PrivateHelperDominanceRule, 'QG082': StaticMethodDominanceRule, 'QG083': UnusedInstanceStateRule, 'QG084': SingleFieldDataclassRule, 'QG085': PassThroughMemberRule, 'QG086': ConstructorDependencyRule, 'QG087': FieldLocalityRule}
            architecture_rules = {'QG088': SingleImplementationAbstractRule, 'QG101': DuplicateConfigSourcesRule, 'QG107': DuplicateConfigDefaultsRule, 'QG113': DuplicatePromptTextRule, 'QG188': UnusedModuleConstantRule}
            if code == 'QG168':
                return ((_chain_public_context(source, config),), {})
            if code == 'QG189':
                from runtime.src.policy_common import ParsedModule
                nodes = tuple(ast.walk(tree))
                parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
                return ((ParsedModule(facts, tree, nodes, parents, (), frozenset()),), {})
            if code in class_rules:
                context = _class_context(tree, facts, code, config)
            elif code in architecture_rules:
                context = _architecture_context(signals, path, kind, code)
            elif code == 'QG098':
                definition = Definition(module='subject', qualname='old', name='old', kind='function', path=path, line=1, end_line=1, column=1, code_line_count=1, decorators=('deprecated',), calls=0 if kind == 'TP' else 1)
                context = ArchitectureDefinitionContext([definition])
            elif code == 'QG190':
                nodes = tuple(ast.walk(tree))
                parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
                context = DynamicExecutionContext(facts, nodes, parents, 'production boundary', 'fixture')
            else:
                context = _agent_context(tree, facts, code, kind, signals, path, config)
            return ((context,), {})
        def _class_context(tree, facts, code, config):
            """Create class_context passive production context from authored source observations."""
            cls = next((n for n in tree.body if isinstance(n, ast.ClassDef)))
            methods = class_methods(cls)
            match code:
                case 'QG080' | 'QG081' | 'QG082' | 'QG083' | 'QG084':
                    from runtime.src.ast_utils import decorator_names
                    public = [n for n in methods if not n.name.startswith('_')]
                    private = [n for n in methods if n.name.startswith('_') and n.name != '__init__']
                    static_like = [n for n in methods if set(decorator_names(n.decorator_list)) & {'staticmethod', 'classmethod'}]
                    no_self = [n for n in methods if method_does_not_use_self(n)]
                    ctx = ClassShapeContext(facts, cls, cls.name, methods, config, public, private, static_like, no_self)
                case 'QG085':
                    ctx = MemberRuleContext(facts, methods, cls.name)
                case 'QG086':
                    ctx = ClassRuleContext(facts, next((n for n in methods if n.name == '__init__')), cls.name, config)
                case _:
                    ctx = ClassRuleContext(facts, cls, cls.name, config)
            context = ctx
            return context
        def _architecture_context(signals, path, kind, code):
            """Create architecture_context passive production context from authored source observations."""
            if code == 'QG088':
                signals.abstract_classes['Base'] = (path, 1, 'Base')
                signals.subclass_bases['Base'] = [(Path('impl.py'), 2, 'Impl')] if kind == 'TP' else []
            elif code == 'QG101':
                signals.env_reads['KEY'] = [(path, 1, 'a'), (Path('impl.py') if kind == 'TP' else path, 2, 'b')]
            else:
                match code:
                    case 'QG107':
                        signals.config_defaults['KEY', 'default'] = [(path, 1), (Path('impl.py') if kind == 'TP' else path, 2)]
                    case 'QG113':
                        signals.prompt_literals['frozen long prompt contract'] = [(path, 1, 'PROMPT'), (Path('impl.py') if kind == 'TP' else path, 2, 'PROMPT')]
                    case 'QG188':
                        signals.module_constants['FEATURE'] = [(path, 1, 'subject')]
                        if kind == 'TN':
                            signals.referenced_names.add('FEATURE')
            context = ArchitectureSignalContext(signals)
            return context
        def _agent_context(tree, facts, code, kind, signals, path, config):
            """Create agent_context passive production context from authored source observations."""
            fn = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))))
            qualname = fn.name
            state_contract = StateContract(name='State', module='subject', fields={'known': StateField('known', 'int', False), 'items': StateField('items', 'list', True)})
            match code:
                case 'QG125':
                    context = UndeclaredStateReadRuleContext(facts, fn, qualname, {'state': state_contract}, ('state', 'missing' if kind == 'TP' else 'known'))
                case 'QG124':
                    context = UndeclaredStateWriteRuleContext(facts, fn, qualname, state_contract, 'missing' if kind == 'TP' else 'known')
                case 'QG127':
                    context = NestedSequenceStateWriteRuleContext(facts, fn, qualname, state_contract, 'items', 'list', 'append' if kind == 'TP' else 'extend')
                case 'QG128':
                    signals.callback_names_by_module['subject'] = frozenset()
                    fields = {'known'} if kind == 'TP' else {'known', 'items'}
                    context = StateScopeEscapeRuleContext(facts, fn, qualname, {'state': fields}, signals)
                case 'QG134':
                    context = UnscopedStateUpdateEntryRuleContext(facts, fn, qualname, {'state'}, config)
                case 'QG135':
                    methods = {('state', 'items'): {'append', 'extend'} if kind == 'TP' else {'append'}}
                    context = StateMergeAmbiguityRuleContext(facts, fn, qualname, methods)
                case 'QG126':
                    signals.state_writes['State', 'known'] = [('int', path, 1), ('list' if kind == 'TP' else 'int', path, 2)]
                    context = InconsistentStateWriteTypeRuleContext(signals)
                case 'QG120':
                    context = DirectToolStateMutationContext(facts, fn, qualname, 'state', 'known', kind == 'TP')
                case 'QG122' | 'QG131':
                    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
                    from runtime.src.return_rules import return_shape
                    from runtime.src.agent_rules import tool_return_dict_keys
                    shapes = {return_shape(n.value) for n in returns}
                    keys = tool_return_dict_keys(returns)
                    undocumented = sorted((k for k in keys if k not in (ast.get_docstring(fn) or '')))
                    context = ToolReturnContractContext(facts, fn, qualname, shapes, keys, undocumented)
                case 'QG129' | 'QG130':
                    config = GuardConfig(history_markers=('history',), trace_markers=('trace',))
                    nodes = list(direct_body_nodes(fn))
                    names = {call_name(n).lower() for n in nodes if isinstance(n, ast.Call)}
                    uses_history = any(('history' in n and 'trace' not in n for n in names))
                    uses_trace = any(('trace' in n and 'history' not in n for n in names))
                    ctx = HistoryTraceRuleContext(facts, fn, qualname, nodes, config, uses_history, uses_trace)
                    context = ctx
            return context
        def _chain_public_context(source, config):
            """Return graph evidence for one authored ephemeral-helper call chain.

            Args:
                source: The original Python program without any execution.
                config: The rule's ordinary repository settings.

            Returns:
                A source-backed Core rule context.
            """
            import atexit
            import shutil
            import tempfile
            from runtime.src.call_chain_rules import SingleUseChainContext
            from runtime.src.analysis_snapshot import directory_analysis_snapshot
            from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
            from runtime.src.python_dependency_facts import StaticPythonDependencyResolver
            root = Path(tempfile.mkdtemp(prefix='rqg-chain-public-'))
            atexit.register(shutil.rmtree, root, ignore_errors=True)
            (root / 'sample.py').write_text(source, encoding='utf-8')
            snapshot = directory_analysis_snapshot(root, config)
            graph = normalized_topology(RepositoryRelationGraph(snapshot), StaticPythonDependencyResolver.from_environment())
            return SingleUseChainContext(root, config, graph, None)
        for document in super()._iter(path):
            for row in document["cases"]:
                if row["rule"] == self.rule:
                    yield input_from_source(row)
