# Repository Quality Guard Development Instructions

Use Repository Quality Guard as the repository development and delivery gate.

## Standard workflow

1. On a fresh clone or at task start, confirm the managed Repository Quality Guard `pre-push` hook is installed. If missing, run `python .agents/skills/repository-quality-guard/scripts/git_hook_install.py .`. Do not overwrite an unmanaged hook and do not bypass the gate with `git push --no-verify`.
2. Before adding a new helper, class, service, adapter, or public API, refresh/search the API catalog and inspect the strongest existing candidates.
3. Implement the smallest change that preserves the accepted design baseline and existing contracts.
4. Run the affected tests unchanged before changing any existing test expectations.
5. Run `doc-generate` / `doc-search` as needed, then `audit`.
6. Complete `修改说明.md`; `audit GENERATED/UNVERIFIED` is not a final quality decision.
7. Run `verify`; only the final PASS/REVIEW_REQUIRED/REJECT result represents the quality gate.
8. Perform a final reduction pass: delete, inline, merge, reuse, move responsibility to the canonical owner, and remove unnecessary state/modes/layers.
9. Push only through the managed `pre-push` gate, which re-runs `verify` for the outgoing HEAD.
10. Deliver reproducibly and validate from a fresh copy when the task changes repository-tracked Guard/runtime assets.

## Commands

Portable or installed usage exposes the same core workflow:

```bash
python .agents/skills/repository-quality-guard/scripts/quality_guard.py doc-generate .
python .agents/skills/repository-quality-guard/scripts/quality_guard.py doc-search . "capability to reuse"
python .agents/skills/repository-quality-guard/scripts/quality_guard.py audit .
python .agents/skills/repository-quality-guard/scripts/quality_guard.py verify .
python .agents/skills/repository-quality-guard/scripts/git_hook_install.py .
```

When running an unpacked Portable release against an external repository, an explicit `--profile <name-or-path>` may be supplied. Installed/sealed Guard instances use the profile frozen at install time and do not switch profiles at runtime.
