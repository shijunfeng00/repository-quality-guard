"""Contract Cases: real constructor-injected receivers, aliasing and ambiguity."""

from __future__ import annotations

import ast

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.relation_graph import RepositoryRelationGraph, normalized_topology
from runtime.src.topology_facts import UsageKind


def _edges(source: dict[str, str]):
    """Collect observable possible call edges from an authored source snapshot.

    Args:
        source: Repository-relative source names and complete Python contents.

    Returns:
        All medium-confidence, source-bound possible call tuples.
    """
    snapshot = RepositoryAnalysisSnapshot(
        None,
        "DIRECTORY",
        {p: PythonUnit(p, text, ast.parse(text)) for p, text in source.items()},
    )
    result = normalized_topology(RepositoryRelationGraph(snapshot))
    return {
        (e.source_id, e.target_id, e.path.as_posix(), e.line)
        for e in result.edges
        if e.kind == UsageKind.MAY_DISPATCH
    }


CONSUMER = """class Receiver:
    def __init__(self, source):
        self.channel = source

    def work(self):
        return self.channel.record()
"""
PROVIDER = """class Ledger:
    def record(self):
        return 1

class Other:
    def record(self):
        return 2
"""


def test_real_constructor_argument_reaches_field_and_method_call():
    source = {
        "domain.py": PROVIDER,
        "consumer.py": CONSUMER,
        "root.py": """from consumer import Receiver
from domain import Ledger
class Root:
    def __init__(self):
        self.state = Ledger()
        self.receiver = Receiver(source=self.state)
""",
    }
    found = _edges(source)
    assert ("consumer.Receiver.work", "domain.Ledger.record", "consumer.py", 6) in found
    assert not any(target == "domain.Other.record" for _, target, _, _ in found)


def test_argument_alias_chain_preserves_known_receiver_type():
    source = {
        "domain.py": PROVIDER,
        "consumer.py": CONSUMER,
        "root.py": """from consumer import Receiver
from domain import Ledger

def build():
    state = Ledger()
    intermediate = state
    actual = intermediate
    return Receiver(actual)
""",
    }
    found = _edges(source)
    assert ("consumer.Receiver.work", "domain.Ledger.record", "consumer.py", 6) in found


def test_conflicting_injected_types_do_not_invent_unique_target():
    source = {
        "domain.py": PROVIDER,
        "consumer.py": CONSUMER,
        "root.py": """from consumer import Receiver
from domain import Ledger, Other

def one():
    return Receiver(Ledger())

def two():
    return Receiver(Other())
""",
    }
    found = _edges(source)
    assert not any(caller == "consumer.Receiver.work" for caller, _, _, _ in found)


def test_unknown_injected_parameter_is_not_resolved_by_method_name():
    source = {
        "domain.py": PROVIDER,
        "consumer.py": CONSUMER,
        "root.py": """from consumer import Receiver
from domain import Ledger

def one():
    return Receiver(Ledger())

def other(x):
    return Receiver(x)
""",
    }
    found = _edges(source)
    assert not any(caller == "consumer.Receiver.work" for caller, _, _, _ in found)


def test_mutated_alias_never_receives_stale_class_binding():
    source = {
        "domain.py": PROVIDER,
        "consumer.py": CONSUMER,
        "root.py": """from consumer import Receiver
from domain import Ledger

def other(x):
    state = Ledger()
    state = x
    return Receiver(state)
""",
    }
    found = _edges(source)
    assert not any(caller == "consumer.Receiver.work" for caller, _, _, _ in found)


def test_grandparent_method_resolves_but_ambiguous_diamond_does_not_guess():
    source = {
        "family.py": """class Grand:
    def ping(self): return 1
class Parent(Grand): pass
class Child(Parent): pass
class Other:
    def ping(self): return 2
class Two(Grand, Other): pass
""",
        "work.py": """from family import Child, Two

def run(child: Child):
    return child.ping()

def ambiguous(item: Two):
    return item.ping()
""",
    }
    found = _edges(source)
    assert ("work.run", "family.Grand.ping", "work.py", 4) in found
    assert not any(caller == "work.ambiguous" for caller, _, _, _ in found)


def test_evidence_cli_shows_constructor_producer_across_two_classes(tmp_path):
    """Oracle: downstream debugger can inspect the actual upstream initializer."""
    from scripts import evidence_lookup
    from contextlib import redirect_stdout, redirect_stderr
    import io

    files = {
        "domain.py": PROVIDER,
        "consumer.py": CONSUMER,
        "root.py": """from consumer import Receiver
from domain import Ledger
class Root:
    def __init__(self):
        self.state = Ledger()
        self.receiver = Receiver(source=self.state)
""",
    }
    for path, content in files.items():
        (tmp_path / path).write_text(content, encoding="utf-8")
    output, errors = io.StringIO(), io.StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        status = evidence_lookup.main(
            [
                "--repo",
                str(tmp_path),
                "--symbol",
                "consumer.Receiver.work",
                "--variable",
                "self.channel",
                "--depth",
                "2",
                "--cache-dir",
                str(tmp_path.parent / (tmp_path.name + "_cache")),
            ]
        )
    assert status == 0, errors.getvalue()
    result = output.getvalue()
    assert "self.channel = source" in result
    assert "Receiver(source=self.state)" in result
    assert "self.state = Ledger()" in result
    assert "[SOURCE_BOUND_MAY_CALL]" in result


def test_graph_stats_count_actual_connected_methods_not_keyword_matches(tmp_path):
    """Stats expose real connected components without inventing same-name calls."""
    from scripts import evidence_lookup
    from contextlib import redirect_stdout, redirect_stderr
    import io

    for name, text in SOURCES_FOR_STATS.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        status = evidence_lookup.main(
            [
                "--repo",
                str(tmp_path),
                "--graph-stats",
                "--cache-dir",
                str(tmp_path.parent / (tmp_path.name + "_stats")),
            ]
        )
    assert status == 0, err.getvalue()
    text = out.getvalue()
    assert "弱连通分量：" in text
    assert "类型约束可能调用：" in text
    assert "无已解析调用边的符号：" in text


SOURCES_FOR_STATS = {
    "a.py": "def a():\n    return b()\ndef b(): return 1\n",
    "b.py": "def isolated():\n    return 42\n",
}


def test_invalid_duplicate_constructor_argument_never_fabricates_dispatch():
    """Invalid Python input binding cannot establish a graph edge."""
    source = {
        "domain.py": PROVIDER,
        "consumer.py": CONSUMER,
        "root.py": """from consumer import Receiver
from domain import Ledger, Other

def build():
    return Receiver(Ledger(), source=Other())
""",
    }
    assert not any(
        caller == "consumer.Receiver.work" for caller, _, _, _ in _edges(source)
    )


def test_invalid_positional_only_keyword_never_fabricates_dispatch():
    """A keyword cannot bind a positional-only constructor parameter."""
    source = {
        "domain.py": PROVIDER,
        "consumer.py": """class Receiver:
    def __init__(self, source, /):
        self.channel = source

    def work(self):
        return self.channel.record()
""",
        "root.py": """from consumer import Receiver
from domain import Ledger

def build():
    return Receiver(source=Ledger())
""",
    }
    assert not any(
        caller == "consumer.Receiver.work" for caller, _, _, _ in _edges(source)
    )


def test_invalid_extra_constructor_keyword_never_fabricates_dispatch():
    """An unsupported named parameter invalidates the entire source call."""
    source = {
        "domain.py": PROVIDER,
        "consumer.py": CONSUMER,
        "root.py": """from consumer import Receiver
from domain import Ledger, Other

def build():
    return Receiver(Ledger(), bogus=Other())
""",
    }
    assert not any(
        caller == "consumer.Receiver.work" for caller, _, _, _ in _edges(source)
    )


def test_valid_keyword_only_constructor_binding_preserves_dispatch():
    """Legitimate binding remains discoverable after rejecting invalid calls."""
    source = {
        "domain.py": PROVIDER,
        "consumer.py": """class Receiver:
    def __init__(self, *, source):
        self.channel = source

    def work(self):
        return self.channel.record()
""",
        "root.py": """from consumer import Receiver
from domain import Ledger

def build():
    return Receiver(source=Ledger())
""",
    }
    assert (
        "consumer.Receiver.work",
        "domain.Ledger.record",
        "consumer.py",
        6,
    ) in _edges(source)


def test_missing_required_constructor_parameter_never_fabricates_dispatch():
    """Partially bound constructor calls that cannot execute prove no receiver."""
    source = {
        "domain.py": PROVIDER,
        "consumer.py": """class Receiver:
    def __init__(self, source, required):
        self.channel = source

    def work(self):
        return self.channel.record()
""",
        "root.py": """from consumer import Receiver
from domain import Ledger

def build():
    return Receiver(Ledger())
""",
    }
    assert not any(
        caller == "consumer.Receiver.work" for caller, _, _, _ in _edges(source)
    )


def test_optional_constructor_parameter_does_not_block_valid_dispatch():
    """A genuine default is sufficient for a valid partial binding."""
    source = {
        "domain.py": PROVIDER,
        "consumer.py": """class Receiver:
    def __init__(self, source, optional=None):
        self.channel = source

    def work(self):
        return self.channel.record()
""",
        "root.py": """from consumer import Receiver
from domain import Ledger

def build():
    return Receiver(Ledger())
""",
    }
    assert (
        "consumer.Receiver.work",
        "domain.Ledger.record",
        "consumer.py",
        6,
    ) in _edges(source)
