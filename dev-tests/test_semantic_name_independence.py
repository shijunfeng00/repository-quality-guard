"""Validate behavior evidence without interpreting user-authored symbol spelling."""

from __future__ import annotations

from pathlib import Path

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


def _rules(tmp_path: Path, source: str) -> list[str]:
    """Scan one source file using the real Python production rule engine."""
    path = tmp_path / "module.py"
    path.write_text(source, encoding="utf-8")
    return [
        finding.code
        for finding in RepositoryScanner(tmp_path, GuardConfig()).scan().findings
    ]


def test_renaming_function_does_not_change_parameter_write(tmp_path: Path) -> None:
    """The observed subscript store matters; method names and formals do not."""
    template = "def {function}({parameter}):\n    {parameter}['key'] = 1\n"
    a = _rules(tmp_path, template.format(function="get_items", parameter="data"))
    b = _rules(tmp_path, template.format(function="save_items", parameter="collector"))
    assert "QG050" in a
    assert a.count("QG050") == b.count("QG050")


def test_method_spelling_cannot_prove_purity_or_mutation(tmp_path: Path) -> None:
    """Queue.get is a consuming operation; unbound names prove neither effect."""
    before = _rules(
        tmp_path,
        "def get_items(queue):\n    queue.get()\n    queue.save()\n",
    )
    after = _rules(
        tmp_path,
        "def save_items(queue):\n    queue.get()\n    queue.save()\n",
    )
    assert "QG123" not in before + after
    assert before.count("QG050") == after.count("QG050")


def test_immutable_result_requires_actual_builtin_type(tmp_path: Path) -> None:
    """An unresolved receiver called replace is not a string instance."""
    ambiguous = _rules(
        tmp_path,
        "def operation(candidate):\n    candidate.replace('old', 'new')\n",
    )
    literal = _rules(tmp_path, "def operation():\n    'ABC'.lower()\n")
    assert "QG070" not in ambiguous
    assert "QG070" in literal


def test_augmented_name_assignment_is_not_always_object_mutation(
    tmp_path: Path,
) -> None:
    """Only a real store through parameter reference is deterministic."""
    rebinding = _rules(tmp_path, "def operation(value):\n    value += 1\n")
    element_store = _rules(tmp_path, "def operation(value):\n    value[0] += 1\n")
    assert "QG050" not in rebinding
    assert "QG050" in element_store


def test_boolean_rule_uses_annotation_not_parameter_name(tmp_path: Path) -> None:
    """Changing a keyword-like identifier without changing types changes no proof."""
    template = "def operation({formal}='false'):\n    return {formal}\n"
    a = _rules(tmp_path, template.format(formal="is_enabled"))
    b = _rules(tmp_path, template.format(formal="credential"))
    assert "QG039" not in a + b
    assert a.count("QG040") == b.count("QG040")


def test_qg017_default_is_advisory_at_twenty_five_branches() -> None:
    """Threshold and severity are release behavior, not a naming heuristic."""
    assert GuardConfig().max_branches == 25


def test_optional_contract_must_not_be_treated_as_one_concrete_type(
    tmp_path: Path,
) -> None:
    """A declared union legitimately needs a runtime branch at its boundary."""
    source = (
        "from typing import Optional\n"
        "def operation(candidate: Optional[str]):\n"
        "    if not isinstance(candidate, str):\n"
        "        raise ValueError('required')\n"
        "    return candidate\n"
    )
    assert "QG060" not in _rules(tmp_path, source)


def test_class_suffix_does_not_establish_wrapper_behavior(tmp_path: Path) -> None:
    """A suffix like Manager or Adapter proves no useless abstraction."""
    a = _rules(tmp_path, "class DataManager:\n    pass\n")
    b = _rules(tmp_path, "class DataAdapter:\n    pass\n")
    assert "QG089" not in a + b


def test_variable_spelling_does_not_establish_config_fallback(
    tmp_path: Path,
) -> None:
    """The names config/settings cannot classify a fallback as a config read."""
    a = _rules(
        tmp_path, "def operation(config, settings):\n    return config or settings\n"
    )
    b = _rules(tmp_path, "def operation(first, second):\n    return first or second\n")
    assert "QG106" not in a + b


def test_best_effort_spelling_cannot_suppress_swallowed_exception(
    tmp_path: Path,
) -> None:
    """A caught exception returning a default is the same effect under renaming."""
    template = (
        "def {method}():\n"
        "    try:\n"
        "        raise RuntimeError('failed')\n"
        "    except Exception:\n"
        "        return None\n"
    )
    observed = []
    for method in ("safe_get", "probe_health", "operation"):
        source = template.format(method=method)
        (tmp_path / "module.py").write_text(source, encoding="utf-8")
        report = RepositoryScanner(tmp_path, GuardConfig()).scan()
        observed.append(
            [(f.code, f.severity) for f in report.findings if f.code == "QG007"]
        )
    assert observed[0] == observed[1] == observed[2] == [("QG007", "error")]


def test_mapping_spelling_cannot_hide_get_setdefault_pop(
    tmp_path: Path,
) -> None:
    """Mapping operations remain candidates regardless of registry/cache names."""
    template = (
        "def operation():\n"
        "    {receiver}: dict[str, object] = {{}}\n"
        "    {receiver}.get('missing', 0)\n"
        "    {receiver}.setdefault('missing', 0)\n"
        "    {receiver}.pop('missing', 0)\n"
    )
    signatures = []
    for receiver in ("metadata", "by_id", "object_cache", "unusual_value"):
        (tmp_path / "module.py").write_text(
            template.format(receiver=receiver), encoding="utf-8"
        )
        report = RepositoryScanner(tmp_path, GuardConfig()).scan()
        signatures.append(
            sorted(
                (f.code, f.severity)
                for f in report.findings
                if f.code in {"QG004", "QG012", "QG025"}
            )
        )
    assert signatures[0] == signatures[1] == signatures[2] == signatures[3]
    assert ("QG004", "warning") in signatures[0]
    assert ("QG012", "warning") in signatures[0]
    assert ("QG025", "warning") in signatures[0]


def test_projection_observation_is_structural_not_name_driven(
    tmp_path: Path,
) -> None:
    """The same mapped field projection exists regardless of receiver naming."""
    template = (
        "def operation({receiver}):\n"
        "    return {{'a': {receiver}.get('a'), "
        "'b': {receiver}.get('b'), 'c': {receiver}.get('c')}}\n"
    )
    findings = []
    for receiver in ("metadata", "registry", "anything"):
        (tmp_path / "module.py").write_text(
            template.format(receiver=receiver), encoding="utf-8"
        )
        report = RepositoryScanner(tmp_path, GuardConfig()).scan()
        findings.append(
            [(f.code, f.severity) for f in report.findings if f.code == "QG153"]
        )
    assert findings[0] == findings[1] == findings[2] == [("QG153", "warning")]


def test_nested_wrapper_is_based_on_return_binding_not_name(
    tmp_path: Path,
) -> None:
    """A lexical return, not a decorator/wrapper spelling, owns QG154."""
    template = (
        "def {outer}():\n    def {nested}():\n        return 1\n    return {nested}\n"
    )
    for outer, nested in (("decorator", "wrapper"), ("produce", "transform")):
        rules = _rules(tmp_path, template.format(outer=outer, nested=nested))
        assert "QG154" not in rules
    rules = _rules(
        tmp_path,
        "def produce():\n    def compute():\n        return 1\n    return 42\n",
    )
    assert "QG154" in rules
