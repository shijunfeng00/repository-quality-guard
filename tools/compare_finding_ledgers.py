"""Compare immutable baseline/current Finding ledgers without invoking QG detectors.

Only the declared retired-baseline finding deletion is a permitted CORE-03 migration difference.
Every other field or ordered Finding difference remains an explicit mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
from difflib import SequenceMatcher
import json
from pathlib import Path
import sys
from typing import Any

FINDING_FIELDS = (
    "code",
    "severity",
    "confidence",
    "path",
    "line",
    "column",
    "symbol",
    "message",
    "suggestion",
    "evidence",
    "source",
)


def compare_finding_ledgers(
    baseline: dict[str, object], current: dict[str, object]
) -> dict[str, object]:
    """Compare ordered, full-field Finding records on identical frozen inputs.

    Args:
        baseline: Baseline scan export with an ordered ``findings`` array.
        current: Current scan export with the same export schema.

    Returns:
        Comparison summary retaining every non-retired mismatch and original order.

    Raises:
        ValueError: Required fields or a valid ordered ledger are missing.
    """

    def validate(ledger: dict[str, object], label: str) -> list[dict[str, Any]]:
        items = ledger.get("findings")
        if not isinstance(items, list):
            raise ValueError(f"{label}: missing ordered findings array")
        for position, item in enumerate(items):
            if not isinstance(item, dict) or any(
                field not in item for field in FINDING_FIELDS
            ):
                raise ValueError(f"{label}: incomplete Finding at index {position}")
        return items

    def frozen_identity(ledger: dict[str, object], label: str) -> tuple[str, str, str]:
        """Authenticate source/configuration manifests and compare one scan scope.

        A copied hash string is not evidence of identical source files.  The
        exported file inventory and effective normalized config must match
        their digests before two ledgers may be compared as frozen inputs.
        """
        identity = ledger.get("frozen_input")
        if not isinstance(identity, dict):
            raise ValueError(f"{label}: missing frozen input identity")
        values = []
        for key in ("source_sha256", "config_sha256"):
            value = identity.get(key)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(letter not in "0123456789abcdef" for letter in value)
            ):
                raise ValueError(f"{label}: invalid {key}")
            values.append(value)
        scope = identity.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError(f"{label}: missing scan scope")
        verification = ledger.get("verification")
        if not isinstance(verification, dict):
            raise ValueError(f"{label}: missing frozen input provenance")
        files = verification.get("files")
        config = verification.get("normalized_config")
        if not isinstance(files, list) or not files or not isinstance(config, dict):
            raise ValueError(f"{label}: incomplete source/config provenance")
        paths: set[str] = set()
        for entry in files:
            if (
                not isinstance(entry, (list, tuple))
                or len(entry) != 2
                or not isinstance(entry[0], str)
                or not entry[0]
                or entry[0].startswith("/")
                or ".." in entry[0].split("/")
                or entry[0] in paths
                or not isinstance(entry[1], str)
                or len(entry[1]) != 64
                or any(letter not in "0123456789abcdef" for letter in entry[1])
            ):
                raise ValueError(f"{label}: invalid or duplicate source file identity")
            paths.add(entry[0])

        def normalized(data: object) -> bytes:
            return json.dumps(
                data, sort_keys=True, separators=(",", ":"), default=str
            ).encode()

        if hashlib.sha256(normalized(files)).hexdigest() != values[0]:
            raise ValueError(f"{label}: source manifest hash mismatch")
        if hashlib.sha256(normalized(config)).hexdigest() != values[1]:
            raise ValueError(f"{label}: config provenance hash mismatch")
        return values[0], values[1], scope

    baseline_identity = frozen_identity(baseline, "baseline")
    current_identity = frozen_identity(current, "current")
    if baseline_identity != current_identity:
        raise ValueError("baseline and current scan different frozen inputs")
    before = validate(baseline, "baseline")
    after = validate(current, "current")
    filtered_before = [item for item in before if item["code"] != "QG050"]
    retired = len(before) - len(filtered_before)

    def identity(item: dict[str, Any]) -> str:
        return json.dumps(
            {name: item[name] for name in FINDING_FIELDS},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    before_ids = [identity(item) for item in filtered_before]
    after_ids = [identity(item) for item in after]
    differences = []
    matcher = SequenceMatcher(a=before_ids, b=after_ids, autojunk=False)
    for kind, i, j, k, m in matcher.get_opcodes():
        if kind == "equal":
            continue
        differences.append(
            {
                "kind": kind,
                "baseline_span": [i, j],
                "current_span": [k, m],
                "baseline": filtered_before[i:j],
                "current": after[k:m],
            }
        )
    return {
        "schema": "rqg.core03.finding-parity.v1",
        "equivalent": not differences,
        "baseline_count": len(before),
        "current_count": len(after),
        "removed_retired_baseline_findings": retired,
        "differences": differences,
    }


def main(argv: list[str] | None = None) -> int:
    """Compare existing JSON ledgers and emit a deterministic machine-readable result.

    Args:
        argv: Optional CLI arguments; None reads sys.argv.

    Returns:
        Zero only for complete parity after the sole permitted retired-baseline deletion.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    current = json.loads(args.current.read_text(encoding="utf-8"))
    result = compare_finding_ledgers(baseline, current)
    data = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(data)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding="utf-8")
    return 0 if result["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
