"""Authoring helper for freezing stable Profile rule-code assignments."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.src.project_profiles import (
    CUSTOM_RULE_CODE_MAX,
    CUSTOM_RULE_CODE_MIN,
    PROFILE_LOCK_SCHEMA,
    _profile_lock_codes,
    _validate_rule_code,
    load_quality_profile,
)


def build_profile_lock(profile_dir: Path) -> dict[str, object]:
    """Freeze custom rule keys to stable QG10000+ codes without recycling tombstones."""
    profile_dir = profile_dir.resolve()
    profile = load_quality_profile(str(profile_dir))
    if profile is None:
        raise ValueError(f"profile not found: {profile_dir}")
    existing = dict(_profile_lock_codes(str(profile_dir)))
    used = set(existing.values())
    mapping = dict(existing)
    next_number = CUSTOM_RULE_CODE_MIN

    for rule_cls in profile._custom_rules:  # authoring surface; configure() already ran.
        key = str(rule_cls.key)
        explicit = _validate_rule_code(rule_cls.code, custom=True) if rule_cls.code else None
        previous = mapping.get(key)
        if previous and explicit and previous != explicit:
            raise ValueError(
                f"rule {key!r} is already frozen as {previous}; refusing renumber to {explicit}"
            )
        if previous:
            continue
        if explicit:
            if explicit in used:
                owner = next((k for k, v in mapping.items() if v == explicit), "unknown")
                raise ValueError(f"QG code {explicit} is already owned by {owner!r}")
            mapping[key] = explicit
            used.add(explicit)
            continue
        while next_number <= CUSTOM_RULE_CODE_MAX and f"QG{next_number}" in used:
            next_number += 1
        if next_number > CUSTOM_RULE_CODE_MAX:
            raise ValueError("Profile custom QG code space exhausted")
        code = f"QG{next_number}"
        mapping[key] = code
        used.add(code)
        next_number += 1

    payload = {
        "schema": PROFILE_LOCK_SCHEMA,
        "profile_name": str(profile.manifest.get("name") or profile.name),
        "profile_version": str(profile.manifest.get("version") or profile.version or ""),
        "rule_codes": dict(sorted(mapping.items())),
        "active_rule_keys": sorted(rule.key for rule in profile._custom_rules),
    }
    path = profile_dir / "PROFILE.lock"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", help="Profile directory containing profile.json and extension.py")
    args = parser.parse_args(argv)
    payload = build_profile_lock(Path(args.profile))
    print(f"PROFILE.lock frozen: {args.profile}")
    for key, code in payload["rule_codes"].items():
        print(f"  {code}  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
