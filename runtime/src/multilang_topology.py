"""Cross-language adapters for the normalized repository topology graph."""

from __future__ import annotations

import shutil
from typing import Any

from .analysis_snapshot import RepositoryAnalysisSnapshot
from .cpp_topology import cpp_topology
from .multilang import SCRIPT_LANGUAGES, node_facts
from .script_topology import script_topology
from .topology_facts import RepositoryTopology, merge_topologies


def normalized_multilang_topology(
    snapshot: RepositoryAnalysisSnapshot,
    script_facts: list[dict[str, Any]] | None = None,
    cpp_paths: list[str] | None = None,
) -> RepositoryTopology:
    """Build normalized JS/TS/C++ topology from one shared analysis snapshot.

    CSS/HTML intentionally emit no callable/owner topology. Missing compiler evidence
    is N/A: C++ facts are produced only through Clang and are never guessed from regex.

    Args:
        snapshot: Shared repository source snapshot.
        script_facts: Optional already-parsed JS/TS/CSS facts from the bundled parser.
        cpp_paths: Optional explicit C++ paths to project through Clang.

    Returns:
        Merged normalized topology containing only statically proven language facts.
    """
    facts = node_facts(snapshot) if script_facts is None else script_facts
    scripts = [item for item in facts if item["language"] in SCRIPT_LANGUAGES]
    script_graph = script_topology(scripts)

    requested_cpp = (
        sorted(
            path
            for path in snapshot.language_paths
            if snapshot.language_units[path].language == "cpp"
        )
        if cpp_paths is None
        else sorted(dict.fromkeys(cpp_paths))
    )
    if not requested_cpp:
        return script_graph
    clang = shutil.which("clang++") or shutil.which("clang")
    if clang is None:
        return script_graph
    cpp_graph = cpp_topology(snapshot, requested_cpp, clang)
    return merge_topologies((script_graph, cpp_graph))
