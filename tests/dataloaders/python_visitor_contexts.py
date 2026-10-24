"""Data-source adapter for shared, non-serializable batch9 rule contexts.

These source-backed contexts contain callbacks or graph identities; converting
rows at iteration is necessary where a plain Pickle record cannot represent
an invocation. No production rule is invoked by this loader.
"""
from __future__ import annotations
from rqgtest import JSONDataLoader
import ast
import json
from pathlib import Path
from runtime.src.facts import ModuleFacts
from runtime.src.topology_facts import ContractOwnership
from runtime.src.visitor_rule_objects import CallRuleContext, ExceptionRuleContext, ProjectionRuleContext, NestedDefinitionRuleContext, ContractClaimRuleContext, MissingMappingKeyRule, MappingAccessRule, UnknownMappingOwnerRule, MappingSetdefaultRule, MappingPopDefaultRule, HasattrContractRule, GetattrDefaultContractRule, SuppressedExceptionRule, BroadExceptionRule, SwallowedContractExceptionRule, FallbackImportRule, ManualMappingProjectionRule, NestedDefinitionVisibilityRule, UnsupportedCompatibilityClaimRule

class PythonVisitorContextLoader(JSONDataLoader):
    def __init__(self, path, rule):
        super().__init__(path)
        self.rule = rule

    def _iter(self, path):
        def input_from_source(row):
            """Construct AST and semantic owner facts without evaluating any rule.

            Args:
                row: Authored source program and observed grammar context parameters.

            Returns:
                Actual evaluator argument tuple and keyword map for one fixture.
            """
            source = str(row['source'])
            tree = ast.parse(source)
            facts = ModuleFacts(path=Path('fixture.py'), module='fixture', source=source, tree=tree)
            scope = 'sample'
            family = str(row['family'])
            if family == 'call':
                calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
                full_name = str(row['full_name'])
                node = next((call for call in calls if isinstance(call.func, ast.Name) and call.func.id == full_name or (isinstance(call.func, ast.Attribute) and call.func.attr == full_name.split('.')[-1])), calls[0])
                context = CallRuleContext(facts=facts, node=node, scope=scope, full_name=full_name, receiver_name='mapping', confirmed_mapping=bool(row['confirmed_mapping']), strict_get=bool(row['strict_get']), is_aggregation=bool(row['is_aggregation']), ownership=ContractOwnership(str(row['ownership'])), required_schema_key=bool(row['required_schema_key']), ownership_evidence=tuple((str(item) for item in row['ownership_evidence'])), selector=str(row['selector']))
            elif family == 'exception':
                handler = next((node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)))
                context = ExceptionRuleContext(facts, handler, scope, frozenset((str(x) for x in row['exception_names'])), bool(row['is_swallowed']) if 'is_swallowed' in row else False, bool(row['has_fallback_import']))
            elif family == 'projection':
                node = next((node for node in ast.walk(tree) if isinstance(node, ast.Dict)))
                context = ProjectionRuleContext(facts, node, scope, receiver=str(row['receiver']), fields=[str(x) for x in row['fields']], confidence='high', is_argument=False)
            elif family == 'nested':
                node = next((node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)))
                context = NestedDefinitionRuleContext(facts, node, scope, owner=str(row['owner']), is_class=bool(row['is_class']))
            else:
                node = next((node for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)))
                context = ContractClaimRuleContext(facts, node, scope, str(row['claim']))
            return ((context,), {})
        for document in super()._iter(path):
            for row in document["cases"]:
                if row["rule"] == self.rule:
                    yield input_from_source(row)
