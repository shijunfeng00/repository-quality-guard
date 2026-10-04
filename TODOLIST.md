# v0.21.4 TODO LIST

Baseline: `v0.21.3` / `9b546b8530ce4e703e1d4498afd710cd6f1ba838`.

## Phase 0 — freeze plan

- [x] Freeze v0.21.4 PLAN/CHECKLIST/ARCH before implementation.
- [x] Separate Phase A report/debt accountability from Phase B C++ build-contract work.

## Phase A — modification-report debt accountability

- [x] Add explicit `progressive` (default) / `cleanup` debt policy to CLI/report facts.
- [x] Classify current ordinary C/E/W into new/worsened, touched historical, untouched historical, reduced historical.
- [x] Replace touched-historical sampling with full touched historical `DEBT-*` inventory.
- [x] Require concrete DEFERRED justification only for still-present touched historical debt.
- [x] Keep untouched historical debt machine-visible without model-authored explanation requirements.
- [x] Make cleanup mode require selected-scope historical debt to reach zero.
- [x] Treat non-Git/no-baseline selected scope as fully debt-responsible.
- [x] Add regression tests for scope, deferral validation, cleanup mode, no-baseline behavior and report regeneration.
- [x] Run full gates and self-audit.
- [x] Commit Phase A and persist cumulative v0.21.4 checkpoint before Phase B.

Evidence: implementation `f3269ee`; 153/153 tests PASS; Ruff/format/compileall/diff-check PASS; self-audit ordinary C/E/W delta=0; touched historical debt reduced 15→7 before explicit DEFER ledger; verify REVIEW_REQUIRED rc=5 with no BLOCKING item.

## Phase B — C++ build truth / semantic provider

- [x] Inspect SlowJSON build contract and geek-ai-agent sandbox C++ toolchain without modifying RQG production code.
- [x] Evaluate mature compilation-database / indexer / semantic-provider options; record decision and rejected alternatives.
- [x] Implement build-contract acquisition with `compile_commands.json` first.
- [x] Separate native compiler validity from auxiliary semantic-provider availability.
- [x] Make per-TU and BASE/TARGET semantic unavailability graceful rather than audit-fatal.
- [x] Project available C++ facts into existing normalized topology without compiler-specific QG families.
- [x] Run SlowJSON + second C++ fixture and Python/JS/TS regressions.

C++ fixture evidence: SlowJSON GCC14 compatibility tree builds and runs 34 tests; 36/36 authored GCC TU syntax checks PASS while semantic facts remain N/A. geek-ai-agent sandbox builds/tests under both GCC14 and Clang17; GCC gives 3/3 native PASS + semantic N/A, Clang gives 3/3 native PASS + normalized topology 134 symbols / 13 owners / 120 edges. Two conflicting compdb configurations for the same TU resolve to unknown rather than path-order guessing.

Real-regression evidence: CoH rc2/rc3 v0.21.3→v0.21.4 ScanReport JSON is byte-identical. geek-ai-rag MMR/0025 and geek-ai-agent normal/cut preserve every finding and every by-rule count; only `multilang_summary` gains build-contract/provider metadata. Exact timeout reproduction finishes naturally in direct-source and formally deployed installed `.agents` modes; cache-hit runs are ~1.8–2.0s and leave zero residual workers.
- [x] Run full real-project/lifecycle regression.

## Final v0.21.4 release

- [ ] Full suite / Ruff / format / compileall / diff-check.
- [ ] Whole-version self-audit: no new ordinary C/E/W delta or blocker.
- [ ] Deterministic canonical skill ZIP twice with identical SHA.
- [ ] Final cumulative mbox fresh replay reproduces exact tree.
- [ ] Tag `v0.21.4` and persist final Library release checkpoint.
