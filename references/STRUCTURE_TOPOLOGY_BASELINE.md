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
