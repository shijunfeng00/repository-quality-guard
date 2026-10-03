# v0.21.4 Acceptance Checklist

## Phase A — progressive debt accountability

- [ ] Default debt mode is `progressive`.
- [ ] New/worsened ordinary C/E/W remains zero-tolerance and cannot be waived by prose.
- [ ] Every still-present historical ordinary C/E/W in changed production files is listed, not sampled.
- [ ] Every such touched historical item is either gone or has a unique concrete DEFERRED decision.
- [ ] Generic “historical/out of scope” text alone is rejected as insufficient deferral evidence.
- [ ] Untouched historical ordinary debt is automatically inventoried but requires no manual explanation.
- [ ] Aggregate report distinguishes baseline total, current total, new/worsened, reduced, touched remaining and untouched remaining.
- [ ] Cleanup mode makes selected-scope remaining historical ordinary debt blocking.
- [ ] Non-Git/no-baseline selected scope is fully debt-responsible.
- [ ] Existing zero-new-debt gate behavior is unchanged.
- [ ] Completed DEBT decisions survive report regeneration when stable finding identity is unchanged.
- [ ] No repository-wide “zero debt reduction requires one generic explanation” gate remains.
- [ ] Phase A full tests/static gates/self-audit pass.
- [ ] Phase A commit + cumulative checkpoint are persisted before Phase B starts.

## Phase B — C++ build truth

- [ ] Real build contract is preferred over guessed compiler flags.
- [ ] Existing `compile_commands.json` can be consumed without rewriting compiler identity/semantic flags.
- [ ] Mature tooling alternatives are evaluated before adding compiler-specific AST code.
- [ ] Native compiler validity and semantic-provider availability are distinct facts.
- [ ] Semantic-provider failure yields `UNKNOWN/N/A`, not an audit-wide source-invalid error.
- [ ] BASE parse/provider failure cannot abort unrelated TARGET/rules.
- [ ] SlowJSON GCC fixture validates the GCC/build-contract path.
- [ ] A second C++ fixture validates a different toolchain path where available.
- [ ] Normalized topology remains the only QG-facing C++ fact schema.
- [ ] Existing Python/JS/TS and real-project regressions pass.

## Release

- [ ] v0.21.4 full suite/static gates pass.
- [ ] Whole-version ordinary C/E/W delta is zero and absolute blockers are zero.
- [ ] Canonical ZIP is deterministic and integrity-clean.
- [ ] Final cumulative mbox fresh replay matches exact release tree.
- [ ] `v0.21.4` tag exists on the verified final commit.
