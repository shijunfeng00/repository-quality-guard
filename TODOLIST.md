# v0.21.3 TODO LIST

Status legend: `[ ]` pending, `[-]` in progress, `[x]` complete, `[!]` blocked/user decision required.

## Phase 0 — Freeze plan and baselines

- [x] Freeze `PLAN.md`, `TODOLIST.md`, `CHECKLIST.md`, `ARCH.md` as planning authority.
- [x] Record SHA256 of v0.21.2 release and five historical calibration archives.
- [x] Snapshot v0.21.2 unit/self-audit outputs for comparison.
- [x] Record selected CoH / geek-ai-rag / geek-ai-agent regression inputs by SHA256/Library path.

## Phase 1 — Normalized fact model, no policy change

- [x] Add language-neutral definition/owner/edge/contract fact types.
- [x] Adapt current Python facts into normalized topology facts.
- [x] Expose repository-level topology index without changing QG decisions.
- [x] Add unit tests proving v0.21.2 findings do not drift when normalized facts are enabled but unused.
- [x] Commit and self-audit current diff.

## Phase 2 — Python-first topology provider

- [x] Module and class owner facts.
- [x] Direct-call edges.
- [x] Callable-reference/callback edges.
- [x] Nested closure and callable escape facts.
- [x] Public/private/export visibility facts.
- [x] Protocol/override/framework-hook evidence.
- [x] `self.field` access edges and owner state clusters.
- [x] Static dependency symbol resolver using source/stubs/metadata without runtime import.
- [x] Keras/TensorFlow-style callback/protocol fixtures.
- [x] Commit and self-audit current diff.

## Phase 3 — Threshold calibration

- [x] QG008: <=20 none; 21–50 Semantic; >50 Critical.
- [x] QG019: <=1000 none; 1001–2000 Semantic; >2000 Critical.
- [x] Update config schema, documentation and report evidence.
- [x] Add paired synthetic fixtures for size thresholds and anti-fragmentation.
- [x] Commit and self-audit current diff.

## Phase 4 — Helper topology refactor

- [x] QG001 consumes normalized usage topology; distinguish direct call, callable reference and protocol hook.
- [x] QG013 uses ephemeral-helper density rather than all tiny methods.
- [x] QG168 only follows eligible ordinary one-shot helper edges.
- [x] Ensure QG008/QG019 fixes cannot be satisfied by helper/class fragmentation laundering.
- [x] Run historical calibration corpus and inspect deltas manually.
- [x] Commit and self-audit current diff.

## Phase 5 — Contract ownership

- [x] Add `internal_formal / external_optional / dynamic_boundary / unknown` facts.
- [x] Refine QG003–QG006 without weakening internal formal-object protection.
- [x] Add internal `hasattr/getattr/.get(default)` strict fixtures.
- [x] Add external optional framework mapping fixtures.
- [x] Commit and self-audit current diff.

## Phase 6 — Cross-language adapters

- [x] Map C++ Clang facts into the same owner/call/reference/protocol model.
- [x] Keep existing JS/TS facts on the same normalized schema where supported.
- [x] Unsupported facts resolve to unknown/N/A, never guessed.
- [x] Verify CSS/HTML remain unaffected by function/class topology rules.
- [x] Commit and self-audit current diff.

## Phase 7 — Calibration and real regression

- [x] HY_Algorithm read-only analysis.
- [x] SlowJSON read-only analysis.
- [x] CrossCameraTracking read-only analysis.
- [x] 端到端车牌识别模型 read-only analysis + RQG run.
- [x] SI-FCN read-only analysis + RQG run.
- [x] Compare false positives/intentional findings; do not force zero.
- [x] CoH selected mbox/replay regression.
- [x] geek-ai-rag selected mbox/replay regression.
- [ ] geek-ai-agent selected mbox/replay regression, including timeout fixture.
- [ ] Direct source and installed `.agents` lifecycle checks remain timeout-free.

## Phase 8 — Final release v0.21.3

- [ ] Full unit suite, Ruff, format, compileall, diff-check.
- [ ] Final self-audit of v0.21.3 diff.
- [ ] Resolve all new ordinary C/E/W delta and blockers; historical debt is recorded, not forced to zero.
- [ ] Finish documentation and migration notes.
- [ ] Clean/squash intermediate history as appropriate.
- [ ] Build deterministic `repository-quality-guard-v0.21.3.skill.zip`.
- [ ] Create final mbox and prove apply-check from v0.21.2 produces the release tree.
- [ ] Tag `v0.21.3`.
