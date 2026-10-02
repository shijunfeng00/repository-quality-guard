# Repository Quality Guard v0.21.4 PLAN

## 1. Planning Authority

This document is the authoritative plan for the v0.21.4 structure-quality refactor. `TODOLIST.md` tracks execution, `CHECKLIST.md` defines acceptance gates, and `ARCH.md` defines the target architecture. Existing tests and current implementation are evidence; they do not override this plan when they encode superseded behavior.

When two quality rules push the implementation in incompatible directions and the conflict cannot be resolved by ownership/reuse evidence, stop the conflicting refactor and request an explicit user decision. Do not weaken one rule merely to satisfy another.

## 2. Release objective

v0.21.4 upgrades RQG from predominantly size/count heuristics to **ownership and reuse topology**, while preserving the v0.21.2 runtime-reliability work: automatic Agent-first dependency bootstrap, bounded network/process timeouts, worker heartbeat/kill-tree cleanup, single-flight snapshot reuse, parent-death protection, slim `.agents` deployment and deterministic full skill packaging.

The release may contain multiple implementation commits. Intermediate checkpoints are recoverability commits, not release identities. Before the final v0.21.4 release, history may be cleaned/squashed so the deliverable is understandable and reproducible.

## 3. Empirical calibration corpus

The following historical code is **read-only calibration evidence, not authority and not a zero-finding target**. Old/manual code may contain genuine debt, local compromises, later AI-assisted fragments, or decisions no longer desirable.

- `HY_Algorithm.git.zip` — SHA256 `3b95f7b36bd3d75a69463ae534d18db56fe0a52e1d28d1aa6a09cfc59f68a62c`
- `SlowJSON.git.zip` — SHA256 `60e9f7319a8ae3b7d0e6d4ac5cb3c26fd70557e36aca0f9727e3a18dfbcd36fb`
- `CrossCameraTracking.7z` — SHA256 `f0a13aa3b52ee71fbab542c7ff3de3aa92fff9a336c29217424a6431459d84c8`
- `端到端车牌识别模型.zip` — SHA256 `8389b2e0782fea70d7fa7d8e4d51b4553dbf20314aaa1eaac0efd555ce02a33c`
- `SI-FCN-Soft-Thresholding-Inception-Network-for-Human-Activity-Recognition-main.zip` — SHA256 `8297729ccd48714bac3a8e29252d8987854a27fdb4caba167c087291151e6529`

The v0.21.2 release baseline is `repository-quality-guard-v0.21.2.skill.zip`, SHA256 `258f1247ad21dc4845c240bc66a29d6a69dfeed0a9ef3bf40fe826a2efabe5d3`.

Calibration corpus findings must be reviewed as evidence. A rule is not changed merely because old code triggers it, and old code is never modified as part of this work.

## 4. Core design principles

1. **One semantic rule set, language-specific fact providers.** Python/C++/JS/TS/other languages must not fork into separately numbered quality policies. Language adapters translate syntax/type-system details into normalized facts. Unsupported facts become unknown/N/A, never guessed.
2. **Explicit polymorphism is encouraged; runtime shape guessing of formal internal objects is not.** Python `Protocol`/ABC/typed unions/decorators/registries and C++ virtual interfaces/concepts/requires/CRTP/variants are legitimate explicit contracts. `hasattr/getattr/default` probing of internal formal objects remains suspicious.
3. **Owner, not raw size, is the architectural unit.** A class, module, namespace, or explicit subsystem may be large if it remains one cohesive owner. Large size triggers evidence/review before hard rejection.
4. **Short is not bad; ephemeral decomposition is.** A short helper reused by several operations, consumed as a callback/protocol hook, or owning a transaction/resource/lifecycle boundary is legitimate. One-shot forwarding/helper chains are the target.
5. **Do not repair a size rule by creating fragmentation.** QG008/QG019 fixes must not create QG001/QG013/QG168/QG185 laundering. Reduction is accepted only when an independent owner/boundary exists.
6. **Static analysis before semantic review.** The scanner should compute caller/reference/callback/protocol/field/owner evidence first. Semantic review receives these facts; it should not rediscover them from prose.
7. **No runtime import for dependency analysis.** Dependency-aware Python resolution uses source/stubs/package metadata when available. Third-party packages are not recursively audited and are never imported merely for introspection.
8. **Unknown is not evidence of guilt.** Missing compiler/stub/dependency information lowers confidence or makes a fact N/A. It must not be replaced with heuristic guessing that pretends certainty.

## 5. Threshold decisions

### QG008 — class method count

- direct authored methods `<= 20`: no method-count finding.
- `21..50`: `SEMANTIC` review, with owner-cohesion/reuse evidence.
- `> 50`: `CRITICAL` by default. Generated/framework-declared code may be excluded only through existing explicit mechanisms.

The 21–50 review must ask whether the methods remain around one state/lifecycle/domain owner and must explicitly forbid splitting merely to reduce the count.

### QG019 — module/file size

- `<= 1000` physical lines: no size finding.
- `1001..2000`: `SEMANTIC` review: verify the file is still one owner and that splitting would create a real independent boundary.
- `> 2000`: `CRITICAL`.

### Unchanged hard/diagnostic thresholds for v0.21.4

- QG014 function length: `> 500` remains Critical.
- QG015 parameter count: `> 8` remains Warning.
- QG016 nesting: existing threshold remains.
- QG017 branch count: existing threshold remains.
- QG168 minimum helper-chain length remains 3, but eligible edges become topology-aware.

Do not add an arbitrary numeric cohesion score threshold in v0.21.4.

## 6. Algorithm changes

### 6.1 Normalized topology facts

Introduce shared facts sufficient for current and future languages:

- Definition/Symbol: language, kind, owner, visibility, source location, generated/foreign flags.
- Owner: class/module/namespace/subsystem identity.
- DirectCallEdge.
- CallableReferenceEdge / callback consumption.
- ProtocolHook / Override.
- FieldAccessEdge / owner-state access.
- DependencyEdge / import/include relationship.
- ClosureCapture / nested callable escape.
- ContractOwnership: `internal_formal`, `external_optional`, `dynamic_boundary`, `unknown`.

Facts providers may enrich these using language-specific parsers, but QG policy consumes the normalized model.

### 6.2 QG001 — ephemeral helper candidate

A short definition is a helper candidate only when static evidence supports all relevant conditions:

- internal/private implementation detail;
- under the configured short-function threshold;
- ordinary direct callers <= configured low-use threshold;
- no material callable/reference consumers;
- not a framework/protocol/override hook;
- not exported public API;
- not an independently justified transaction/resource/lifecycle boundary.

Public/protocol/callback functions may still receive low-confidence informational evidence where useful, but must not be described as one-shot helpers merely because direct call count is zero.

### 6.3 QG013 — fragmented owner / helper swarm

Replace `tiny_method_count` ratio as the primary criterion. Count **ephemeral helper candidates** within the same owner. Initial candidate threshold:

- at least 5 ephemeral helpers; and
- at least 40% of internal implementation functions/methods.

This produces `SEMANTIC`, not automatic Critical. Evidence includes caller counts, callable references, owner, and shared-consumer graph. This threshold is calibration data, not immutable doctrine; adjust only with benchmark evidence.

### 6.4 QG168 — single-use helper chain

Keep Critical for a >=3-layer chain only when every intermediate node is an eligible ephemeral helper and every edge is an ordinary direct-call decomposition edge. Exclude protocol/callback/override/closure-escape/lifecycle/transaction boundaries. Do not treat callback reference graphs as ordinary helper chains.

### 6.5 QG003–QG006 — contract ownership

Preserve strict findings for runtime guessing of `internal_formal` contracts. For `external_optional` framework mappings/objects, use the declared external contract when resolvable; do not equate legitimate optional API access with hiding an internal schema failure. `unknown` becomes semantic/low-confidence evidence rather than a fabricated hard conclusion.

### 6.6 Owner cohesion evidence

Compute owner evidence without introducing a new hard QG number in v0.21.4:

- method/function -> field/state edges;
- public operation -> shared private primitive edges;
- internal dependency clusters;
- module/class owner membership.

Use this evidence in QG008/QG019 semantic review. A large cohesive resource/container/domain owner may pass; multiple weakly connected owner clusters are evidence for a real split.

## 7. Language strategy

### Python first

Python is the primary implementation target for v0.21.4 because current user projects are Python-heavy. Use stdlib `ast` plus static source/stub metadata. Implement module-owner and class-owner topology, callable references, nested closures, decorators, explicit protocols, inheritance and `self.field` access.

Dependency-aware resolution may inspect installed/source `.py`/`.pyi` or package metadata **without importing the dependency**. Resolve only requested symbols/signatures/protocol relationships; do not recursively audit TensorFlow/Keras or other third-party packages.

### C++

Reuse the same normalized facts. Enrich through Clang AST when available: method/access specifier, calls, field/member access, virtual/override, lambda/function references, concept/requires/template protocol facts. Do not infer compiler-semantic facts from regex when Clang evidence is required.

### JS/TS/Java/future languages

Add providers, not new semantic QG families. Rules whose required facts are unavailable return N/A/unknown. CSS/HTML naturally do not participate in function/class topology rules.

## 8. Validation strategy

### Historical calibration

Run the five read-only historical samples before/after. They are used to inspect precision/false positives, not as PASS targets and not as mandatory zero-finding corpora.

### Paired synthetic fixtures

At minimum:

- 30-method cohesive resource -> QG008 Semantic, not Critical.
- 30-method multi-owner god class -> QG008 Semantic with multi-cluster evidence.
- 51-method class -> QG008 Critical.
- 1500-line single-owner module -> QG019 Semantic.
- 2001-line module -> QG019 Critical.
- short shared helper with multiple callers -> no helper-laundering Warning.
- one-shot short private helper -> QG001 candidate.
- 3-layer one-shot helper chain -> QG168 Critical.
- framework callback / Keras-style `build`/`call` -> no low-use helper misclassification when protocol evidence is available.
- callable passed as metric/callback with direct calls=0 -> not low-use helper.
- `hasattr` on internal formal object -> remains strict.
- optional external framework mapping access -> not treated as internal schema laundering when contract ownership is resolved.

### Real modern regression

Select at least three real project histories/mbox/replay sets, including Council of Harnesses, geek-ai-rag and geek-ai-agent. Compare v0.21.2 vs v0.21.4 findings and manually review intentional deltas. The exact timeout reproducer remains part of lifecycle acceptance.

### Self-audit

Every behavior-changing commit must run focused tests and a current-diff self-audit. Major milestones run the full suite. Before release, RQG must audit its own v0.21.4 diff under the current policy. Historical pre-existing debt is recorded but is not a mandatory zero target unless explicitly re-authorized.

## 9. Checkpoint policy

All future persistent checkpoints across projects go under Library `/checkpoints/`.

A checkpoint should be lightweight:

- commit SHA / parent SHA;
- optional small mbox when needed for reconstruction;
- current PLAN/TODO/CHECKLIST status;
- test/audit summary;
- SHA256 and Library path references for already-persisted large inputs;
- no duplicate copy of large repository ZIPs or benchmark archives already in Library.

Prefer a recoverability commit plus manifest over repackaging the repository. Only produce a large full repository artifact for a release or when no reconstructable source exists. Intermediate checkpoint commits may be squashed/rewritten before v0.21.4 final release.

## 10. Explicit non-goals

- Do not make historical manual code an authority or force it to zero findings.
- Do not weaken v0.21.2 lifecycle/bootstrap reliability.
- Do not create per-language copies of the same semantic QG rule.
- Do not introduce a magic cohesion score as a hard gate in this release.
- Do not recursively scan/audit third-party dependency source as if it were project code.
- Do not add compatibility paths merely to preserve superseded v0.21.2 implementation shapes.
