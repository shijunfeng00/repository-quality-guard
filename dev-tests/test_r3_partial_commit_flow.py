"""R3 partial-commit candidates must carry an authored two-state witness."""

import ast

from runtime.src.analysis_snapshot import PythonUnit, RepositoryAnalysisSnapshot
from runtime.src.partial_commit_flow import partial_commit_findings


def _find(source):
    return partial_commit_findings(
        RepositoryAnalysisSnapshot(None, "DIRECTORY", {
            "module.py": PythonUnit("module.py", source, ast.parse(source))
        })
    )


def test_both_builtin_container_mutations_inside_swallowed_exception():
    source = (
        "class Owner:\n"
        "    def __init__(self):\n"
        "        self.done: set[str] = set()\n"
        "        self.results: dict[str, str] = {}\n"
        "    def save(self, key, value):\n"
        "        try:\n"
        "            self.done.add(key)\n"
        "            self.results[key] = value\n"
        "            return value\n"
        "        except Exception:\n"
        "            return {'ok': False}\n"
    )
    (finding,) = _find(source)
    assert finding.code == "QG213"
    assert finding.severity == "warning"
    assert (finding.evidence["first_line"], finding.evidence["second_line"]) == (7, 8)
    assert finding.evidence["status"] == "CONDITIONAL_REVIEW_NOT_CONFIRMED_FAILURE"


def test_no_claim_without_both_source_bound_builtins_or_swallow():
    template = (
        "class Owner:\n"
        "    def __init__(self):\n"
        "        self.done = {init}\n"
        "        self.results = {{}}\n"
        "    def save(self, key, value):\n"
        "        try:\n"
        "            self.done.{action}(key)\n"
        "            self.results[key] = value\n"
        "        except {exception}:\n"
        "            {handler}\n"
    )
    for init, action, exception, handler in (
        ("external()", "add", "Exception", "return None"),
        ("set()", "add", "ValueError", "return None"),
        ("set()", "add", "Exception", "raise"),
        ("set()", "custom", "Exception", "return None"),
    ):
        assert not _find(template.format(init=init, action=action, exception=exception, handler=handler))


def test_renaming_fields_does_not_change_detection():
    text = (
        "class O:\n"
        " def __init__(self):\n"
        "  self.a=set()\n"
        "  self.b={}\n"
        " def run(self,k,v):\n"
        "  try:\n"
        "   self.a.add(k)\n"
        "   self.b[k]=v\n"
        "  except Exception:\n"
        "   return None\n"
    )
    a = _find(text)
    b = _find(text.replace("self.a", "self.queue_get").replace("self.b", "self.cache"))
    assert len(a) == len(b) == 1


def test_handler_compensation_keeps_effect_outcome_unknown():
    source = (
        "class O:\n"
        " def __init__(self):\n"
        "  self.seen=set()\n"
        "  self.results={}\n"
        " def run(self,k,v):\n"
        "  try:\n"
        "   self.seen.add(k)\n"
        "   self.results[k]=v\n"
        "  except Exception:\n"
        "   self.seen.discard(k)\n"
        "   return None\n"
    )
    assert not _find(source)


def test_shadowed_builtin_set_is_not_certified():
    source = (
        "set = lambda: other_container()\n"
        "class O:\n"
        " def __init__(self):\n"
        "  self.a=set()\n"
        "  self.b={}\n"
        " def run(self,k,v):\n"
        "  try:\n"
        "   self.a.add(k)\n"
        "   self.b[k]=v\n"
        "  except Exception:\n"
        "   return None\n"
    )
    assert not _find(source)
