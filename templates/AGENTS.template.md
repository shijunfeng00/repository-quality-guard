# Repository Quality Guard Development Instructions

Use Repository Quality Guard as the repository development and delivery gate.

## Standard workflow

1. Before adding a new helper, class, service, adapter, or public API, refresh/search the API catalog and inspect the strongest existing candidates.
2. Implement the smallest change that preserves the accepted design baseline and existing contracts.
3. Run the affected tests unchanged before changing any existing test expectations.
4. Run `doc-generate` / `doc-search` as needed, then `audit`.
5. Complete `修改说明.md`; `audit GENERATED/UNVERIFIED` is not a final quality decision.
6. Run `verify`; only the final PASS/REVIEW_REQUIRED/REJECT result represents the quality gate.
7. Perform a final reduction pass: delete, inline, merge, reuse, move responsibility to the canonical owner, and remove unnecessary state/modes/layers.
8. Deliver reproducibly and validate from a fresh copy when the task changes repository-tracked Guard/runtime assets.

## Commands

Portable or installed usage exposes the same core workflow:

```bash
python .agents/skills/repository-quality-guard/scripts/quality_guard.py doc-generate .
python .agents/skills/repository-quality-guard/scripts/quality_guard.py doc-search . "capability to reuse"
python .agents/skills/repository-quality-guard/scripts/quality_guard.py audit .
python .agents/skills/repository-quality-guard/scripts/quality_guard.py verify .
```

When running an unpacked Portable release against an external repository, an explicit `--profile <name-or-path>` may be supplied. Installed/sealed Guard instances use the profile frozen at install time and do not switch profiles at runtime.
