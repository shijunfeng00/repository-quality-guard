# v0.21.3 Acceptance Checklist

## A. Planning and authority

- [ ] Implementation matches `PLAN.md`; tests do not silently restore superseded v0.21.2 semantics.
- [ ] Any unresolved rule conflict is explicitly escalated instead of bypassed.
- [ ] Historical manual projects are evidence only and remain read-only.

## B. Rule semantics

- [x] QG008: 20/50 two-level behavior is proven by fixtures.
- [x] QG019: 1000/2000 two-level behavior is proven by fixtures.
- [ ] QG014=500, QG015=8, existing QG016/QG017 thresholds remain unchanged unless a later explicit decision says otherwise.
- [x] QG001 does not call a callback/protocol/public API an ephemeral helper merely because direct-call count is zero.
- [x] QG013 does not punish cohesive resource/container/value classes simply because many methods are tiny.
- [x] QG168 still rejects >=3-layer one-shot helper chains and cannot be bypassed by renaming/wrapping.
- [x] QG003–QG006 stay strict for internal formal contracts.
- [x] External optional contracts do not become hard findings solely because `.get()` or equivalent optional access is used according to that contract.

## C. Architecture

- [ ] Semantic QG rules consume normalized facts rather than language names/regex branches wherever feasible.
- [x] Python facts cover module/class owners, call edges, callable references, closures, protocol hooks and field/state access.
- [x] Dependency-aware Python analysis performs no runtime imports.
- [x] C++ semantic facts use Clang when compiler-level evidence is required.
- [x] Unknown/unavailable evidence remains unknown/N/A rather than guessed.
- [x] New topology code has one authoritative owner; no duplicate graph implementations.

## D. Anti-fragmentation and conflict checks

- [ ] A QG008/QG019 remediation fixture cannot pass merely by producing one-shot helpers/wrappers/classes.
- [x] Shared private primitives with multiple consumers are not treated as helper laundering.
- [x] Framework callbacks/overrides/protocol hooks are excluded from ordinary one-shot caller chains.
- [ ] If two rules still recommend contradictory edits, the conflict is surfaced for human adjudication.

## E. Historical calibration

- [x] HY, SlowJSON and CrossCameraTracking are scanned/read-only and reviewed for precision.
- [x] 端到端车牌识别模型 and SI-FCN are scanned/read-only and reviewed for Python-specific precision.
- [x] Findings in historical projects are not automatically waived.
- [x] Historical projects are not required to reach zero findings.

## F. Regression

- [x] v0.21.2 baseline behavior captured.
- [x] CoH real history/mbox regression reviewed.
- [x] geek-ai-rag real history/mbox regression reviewed.
- [ ] geek-ai-agent real history/mbox regression reviewed.
- [ ] Intentional finding changes are documented by rule/reason.
- [ ] No unexplained finding disappearance/addition.

## G. Runtime reliability (must not regress from v0.21.2)

- [ ] Fresh Agent-first CLI bootstrap remains automatic.
- [ ] No local media + no network fails fast with actionable natural-language feedback.
- [ ] Dependency subprocess timeout kills the process tree.
- [ ] Scan worker heartbeat timeout kills the worker tree.
- [ ] Parent-death guard works.
- [ ] Same-snapshot single-flight works.
- [ ] Exact historical timeout reproducer exits naturally with zero residual processes.
- [ ] Direct source and installed `.agents` modes both pass lifecycle acceptance.

## H. Self-audit and release

- [ ] Every behavior-changing commit had focused tests and current-diff self-audit.
- [ ] Major milestones had full suite execution.
- [ ] Final v0.21.3 self-audit has no new ordinary C/E/W delta or absolute blocker.
- [ ] Historical self debt is reported but not used as a hidden release blocker.
- [ ] Final skill ZIP contains valid `.git`, profiles and offline payload and extracts to a valid clean release worktree.
- [ ] Installed `.agents` payload remains slim.
- [ ] Final mbox applies from v0.21.2 and reproduces the exact release tree.
- [ ] Deterministic release build is proven by repeated SHA256 match.
