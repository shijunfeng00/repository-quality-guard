# Structure Topology Refactor Baseline

This file records reproducible evidence for the ownership/reuse-topology refactor. It is evidence, not planning authority; `PLAN.md` remains authoritative.

## Release baseline

- baseline tag: `v0.21.2`
- baseline commit: `8bca8d1fffa452c75a10cd66c97a34d55a182185`
- canonical skill ZIP SHA256: `258f1247ad21dc4845c240bc66a29d6a69dfeed0a9ef3bf40fe826a2efabe5d3`

## Baseline execution

- development suite: **91/91 PASS**
- Ruff check: PASS
- Ruff format: PASS
- compileall: PASS
- self-audit worker: natural exit, `14.4s`, runtime cleanup complete
- self-audit absolute C/E/W/I: `2 / 92 / 223 / 299`
- self-audit ordinary C/E/W delta against `v0.21.1`: **0**
- semantic delta: `13`
- test-contract delta: `15`
- baseline self-audit report SHA256: `5cc9db7843e7b3c4d8adb5efb94abd4140363abd39f853ceb7dfdff239bd9c96`

The baseline audit is intentionally `UNVERIFIED`: its purpose is to freeze automatic facts and runtime behavior, not to manufacture a human-reviewed release report for an already released version.

## Python calibration finding snapshots

These hashes cover the complete deterministic JSON payload emitted by the v0.21.2 Python scanner under the fixed calibration harness.

- 端到端车牌识别 package: `275` findings; payload SHA256 `6fefe43c8b4e0d636cd844ce6fdc36acb3fdc5708ee0ec602c6e978003c71f7e`
- SI-FCN Python files: `54` findings; payload SHA256 `0af391090762bdfa22317267bb55e52d0f3511f3deaf80010eb9899b5bab7974`

## Real regression inputs

Large repositories are not copied into checkpoints. The following Library paths and SHA256 values identify the selected real-history inputs.

### Council of Harnesses

- `/council-of-harness-v2.8-rc2.mbox` — `0a4d5c09c5a95ed7689ac9fcd94ab287c97a2125fdbcceecc48518be5f2c3b5f`
- `/council-of-harness-v2.8-rc3.mbox` — `70f9ac60189cfde580ede6107d7b800eb40af0782b262ca79828fa9f08c0376d`

### geek-ai-rag

- `/geek-ai-rag-question-decomposition-mmr-20260922-v3.mbox` — `c27fa28aebb86df87cbac8bf234cc5a7d7aa3c17ceba046f758fefc3a33e6417`
- `/geek-ai-rag-0025-three-commit-v0.21.1-20260924.mbox` — `95ddbcc3906bdbf51e08c02884907f13c7507dae0f8befde07d4482988d2e9fb`

### geek-ai-agent

- `/geek-ai-agent-0021-normal-from-73fc0df.mbox` — `036254851d79a9714f5418b237cc4e72c17e794a7a38fb07e36ff02d7ef21026`
- `/geek-ai-agent-0021-cut-from-73fc0df.mbox` — `903254b2070e8700ded090731d2c6e7a3352bb66f07870c697a277b1d89779c4`
- `/geek-ai-agent-73fc0df-to-0026-cut-timeout-repro.mbox` — `b6336b98d26baea411c95184757023c01798d360c99907bba65f1ee1dc54f08f`
- `/geek-ai-agent-qg-timeout-repro.bundle` — `4015671713a9152e3022abd341dc61d9acdb6328a3b54a1ab1aee6e3d8390f3e`

## Historical calibration corpus

The historical manual-code archives listed in `PLAN.md` remain read-only evidence. Findings are reviewed for precision; they are not waived automatically and the projects are not required to reach zero findings.

## Phase 1 normalized-fact verification

- development suite after topology integration: **93/93 PASS**
- Ruff check / format / compileall / diff-check: PASS
- current-diff self-audit against baseline commit `b14e33e`: ordinary C/E/W delta **0**; absolute blockers **0**
- 端到端车牌 package finding payload remains exactly `6fefe43c8b4e0d636cd844ce6fdc36acb3fdc5708ee0ec602c6e978003c71f7e`
- SI-FCN finding payload remains exactly `0af391090762bdfa22317267bb55e52d0f3511f3deaf80010eb9899b5bab7974`

Phase 1 generates normalized topology facts but shared QG policy does not consume them yet.
## Phase 7 real regression — Council of Harnesses

The frozen `v2.8-rc2` and `v2.8-rc3` snapshots were scanned with the same `council-of-harnesses` Profile under v0.21.2 and current v0.21.3. Historical project source was not modified.

- rc2 total findings: `6229 -> 5973` (`-256`).
- rc3 total findings: `5758 -> 5479` (`-279`).
- QG001 ephemeral-helper findings: rc2 `286 -> 34`; rc3 `311 -> 33`. The reductions are expected from normalized reuse/callback/protocol evidence.
- QG003/QG004 candidates largely reclassify to QG026 when mapping ownership cannot be proven: QG003 `705 -> 149`, QG004 `65 -> 18`, QG026 `0 -> 603` on both snapshots. This is the intended `unknown` contract-ownership behavior, not silent waiver.
- QG013 `9 -> 0` on both snapshots under ephemeral-helper density semantics. QG019 gains only the expected two-tier owner-size review candidates (`+1` rc2, `+2` rc3).
- Real regression exposed one v0.21.3 defect before the final comparison: a function-local class (`SpyBinder`) produced a field-access `class_id` that did not match any relation node and crashed normalized topology projection. Commit `4a5cd4d` fixes lexical owner ordering at the collector and adds a regression fixture; both CoH snapshots then scan to completion.
- Final v0.21.3 payload SHA256: rc2 `358b84ae3c9fc47daa6b14955e57a154446dece9afb0c086b40a9d7b78e30ce6`; rc3 `7a5fbf9f0d24b8ec11abf114a337065c857ef99ebd4ee5ba9f3bd36d0af47436`.

Conclusion: the CoH regression is reviewed. The finding changes are explained by the authorized helper/contract/owner-size policy changes, and the only scanner crash discovered by the corpus is now covered by an automated regression test.

## Phase 7 real regression — geek-ai-rag

The frozen MMR and 0025 snapshots were replayed from the recorded baseline and scanned with the same compatibility-equivalent `geek-ai-rag` Profile under v0.21.2 and current v0.21.3. The project source was not modified.

- MMR snapshot total findings: `1966 -> 1918` (`-48`).
- 0025 final snapshot total findings: `2023 -> 1971` (`-52`).
- QG001 topology-aware helper findings: MMR `154 -> 98` (`-56`); 0025 `157 -> 95` (`-62`).
- Contract ownership is a count-preserving reclassification for the affected families: MMR QG003 `409 -> 258` (`-151`) plus QG004 `83 -> 66` (`-17`) exactly equals QG026 `0 -> 168`; 0025 QG003 `386 -> 242` (`-144`) plus QG004 `83 -> 66` (`-17`) exactly equals QG026 `0 -> 161`. No contract candidate disappears silently.
- QG019 adds only the expected 1001–2000-line semantic-review band: MMR `3 -> 9`; 0025 `2 -> 10`. QG013 changes from one legacy critical density finding to two informational owner-review findings under the authorized topology semantics.
- QG002 initially showed an unauthorized `+5/+6` drift. The cause was an off-by-one introduced during refactor (`<= short_max_lines` instead of the established strict `<`) plus one legitimate package re-export that the stricter resolver could not follow. The implementation restores the strict boundary and adds deterministic repository-local explicit re-export canonicalization shared by Scanner and RelationGraph. Final QG002 counts exactly match v0.21.2: MMR `78 -> 78`, 0025 `84 -> 84`.
- The re-export fix does not restore the removed global unique-simple-name guess. It follows only authored import bindings, terminates cycles as unknown, performs no runtime imports, and leaves unsupported dynamic export behavior unresolved.
- QG156 gains one finding on both snapshots (`15 -> 16`): `OpenAITransport._arguments_json_complete` has no real usage. The old simple-name resolver had incorrectly credited it with calls to a same-named module function; the stricter resolver correctly exposes the unused wrapper.
- RQG self-audit during this regression also found that `annotation_names()` ignored the `None` constant inside PEP 604 annotations such as `str | None`, causing a false QG046 return-contract finding. The annotation fact now records `None`, while a non-nullable `str` contract still reports an implicit-None path.
- Regression-derived implementation commit: `eac0361` (`fix(topology): 修正 Python 静态符号解析边界`). Its full development suite is `143/143 PASS`; Ruff, format, compileall and diff-check pass; fresh current-diff audit has zero ordinary C/E/W delta and zero absolute blockers; read-only verify returns `REVIEW_REQUIRED (rc=5)` only for the reviewed internal interface ledger, with no BLOCKING item.
- Two early current-version runs exceeded the outer harness timeout, but controlled repeats completed naturally in approximately 20–23 seconds and left zero residual worker processes. This was not reproducible as a stable performance/lifecycle regression, so no performance code was changed on that evidence. The exact lifecycle acceptance remains assigned to the frozen geek-ai-agent timeout reproducer.

Conclusion: the geek-ai-rag real regression is reviewed. All persistent rule-count changes are explained by the frozen v0.21.3 policy or by concrete resolver precision fixes; no unexplained QG002 drift remains.

## Phase 7 real regression — geek-ai-agent

The frozen `0021-normal`, `0021-cut`, and exact timeout snapshots were replayed from the recorded `73fc0df` baseline. The normal/cut scans use one compatibility-equivalent `geek-ai-agent` Profile under v0.21.2 and current v0.21.3. Project source was not modified.

- normal total findings: `2430 -> 2138` (`-292`).
- cut total findings: `2437 -> 2145` (`-292`).
- The rule-count deltas are identical on normal and cut: QG001 `435 -> 140` (`-295`), QG003 `325 -> 149` (`-176`), QG004 `79 -> 40` (`-39`), QG026 `0 -> 215`, QG019 `2 -> 12` (`+10`), and QG013 `8 -> 1` (`-7`). Every other rule count is unchanged.
- Contract ownership is count-preserving for the affected families: QG003 `-176` plus QG004 `-39` equals QG026 `+215`. The remaining deltas are exactly the authorized helper-density and owner-size semantics. No unexplained rule family appears or disappears.

The exact historical timeout snapshot was then exercised with the current implementation in both runtime modes. The report return code remains `3` because the historical project has a static REJECT report with intentionally unfilled human audit fields; lifecycle acceptance concerns natural process termination and cleanup, not making the historical project green.

- direct-source mode: three consecutive runs completed naturally in approximately `20–22s`; each log reaches `scan worker completed` and `runtime cleanup complete`; zero residual RQG worker processes remained after each run.
- installed `.agents` mode: the current source tree was deployed through `runtime/deploy.py` into an isolated timeout-snapshot copy with the same Profile frozen as immutable installed policy. The cold run completed naturally after the worker finished in `32.9s`; subsequent installed runs used the sealed scan snapshot cache and completed naturally, including a measured `1.90s` third run. Installed integrity passed (`79` protected files), the sealed `geek-ai-agent` Profile was selected without a runtime `--profile`, and zero residual worker processes remained.
- The snapshot's pre-existing `.agents` payload was v0.21.1 and was not used as the current installed acceptance target; current installed mode was produced by the current deploy path.

Conclusion: the geek-ai-agent normal/cut finding deltas are fully explained by the frozen v0.21.3 policy, and the exact historical lifecycle reproducer exits naturally with zero residual processes in both direct-source and formally deployed installed `.agents` modes. Phase 7 / Step 5 real regression is complete.
