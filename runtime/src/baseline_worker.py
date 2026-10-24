"""Independent Git-baseline worker entry point.

This one-way dependency imports the production scan API; the supervisor must
not import it, avoiding a scanner-to-supervisor-to-scanner import cycle.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

from .config import GuardConfig
from .process_lifecycle import install_isolated_parent_death_guard


def _baseline_worker(input_path: Path, output_path: Path) -> None:
    """Compute one immutable revision, without importing the caller's main script."""
    from .cli import scan_git_baseline, get_project_profile

    with input_path.open("rb") as source:
        target, config, revision, profile_reference, snapshot = pickle.load(source)
    profile = get_project_profile(profile_reference)
    if profile is not None:
        # Profile construction also installs nonblocking path classification.
        # The new interpreter must receive the same Profile context as its owner.
        GuardConfig().with_project_profile(profile)
    result = scan_git_baseline(target, config, revision, profile, snapshot)
    with output_path.open("wb") as destination:
        pickle.dump(result, destination, protocol=pickle.HIGHEST_PROTOCOL)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Git baseline worker requires input and output paths")
    install_isolated_parent_death_guard()
    _baseline_worker(Path(sys.argv[1]), Path(sys.argv[2]))
