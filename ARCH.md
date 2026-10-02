# Repository Quality Guard v0.21.4 Architecture

## 1. Architectural intent

RQG separates **syntax/type-system extraction** from **quality policy**. A language provider may know how Python callbacks or C++ overrides are represented, but QG001/QG008/QG013/QG019/QG168 must reason about common ownership/reuse facts rather than branch on language names.

```text
Python AST + static stubs ─┐
                          │
Clang AST ────────────────┤
                          ├──> Normalized Topology Facts ──> Shared QG Policy
JS/TS parser ─────────────┤              │
                          │              └──> Semantic Review Evidence
future adapters ──────────┘
```

CSS/HTML or any language lacking a relevant concept simply does not emit the corresponding facts.

## 2. Normalized fact model

### SymbolFact

Represents an authored definition without assuming Python/C++ terminology.

- stable symbol id
- path/line/language
- kind: function/method/class/module/namespace/etc.
- owner id and owner kind
- visibility: public/protected/private/internal/unknown
- generated/foreign status
- source size and signature metrics

### OwnerFact

Represents a cohesive code owner. Owners may be classes, modules, namespaces or explicit subsystems. A language does not need classes to participate in cohesion analysis.

### UsageEdge

Typed edges, not one undifferentiated caller count:

- `DIRECT_CALL`
- `CALLABLE_REFERENCE`
- `CALLBACK_REGISTRATION`
- `PROTOCOL_HOOK`
- `OVERRIDE`
- `FIELD_ACCESS`
- `DEPENDENCY`
- `CLOSURE_CAPTURE`

QG policy decides which edge kinds matter. A `DIRECT_CALL` chain may prove helper laundering; a callback/protocol edge must not be interpreted as ordinary one-shot decomposition.

### ContractFact

`ContractOwnership` is one of:

- `INTERNAL_FORMAL`: RQG can establish that project code owns a stable formal contract.
- `EXTERNAL_OPTIONAL`: an external API explicitly allows an optional/dynamic field/value.
- `DYNAMIC_BOUNDARY`: a declared boundary whose job is to validate/normalize dynamic input.
- `UNKNOWN`: static evidence is insufficient.

Unknown never silently becomes internal or external.

## 3. Provider architecture

### PythonFactProvider

Primary v0.21.4 provider. Uses Python `ast` and static files/metadata only.

Responsibilities:

- module/class/function symbol and owner graph;
- imports/exports/visibility;
- direct calls;
- callable references and callback escape;
- nested functions/closures;
- inheritance/decorators/explicit protocol evidence;
- instance-field access (`self.x`) and owner state graph;
- optional dependency symbol resolution from source/`.pyi`/metadata.

It must not import TensorFlow/Keras or execute dependency code to discover signatures.

### CppFactProvider

Uses Clang semantic evidence where available. It maps C++ concepts onto the same model:

- access specifiers -> visibility;
- namespaces/classes -> owners;
- `CallExpr`/member calls -> direct-call edges;
- field/member accesses -> field edges;
- virtual/override/concept/requires/template relationships -> explicit protocol evidence;
- lambda/function-pointer/callback usage -> callable-reference edges.

Compiler-semantic facts are not reconstructed from regex when Clang evidence is unavailable.

### Script/JS/TS providers

Existing parser facts are adapted to the normalized model incrementally. No new QG code family is created merely for a language.

## 4. Rule consumption

### QG001

Consumes SymbolFact + UsageEdges. The rule asks whether a short internal definition is an **ephemeral decomposition helper**, not merely whether `direct_calls <= 1`.

### QG008

Method count is a trigger, not a complete architectural diagnosis:

- 21–50 -> Semantic with OwnerFact state/reuse evidence.
- >50 -> Critical.

The semantic review must explicitly test for a real second owner before recommending a split.

### QG013

Counts only ephemeral internal helpers, not all tiny methods. It reports an owner-level fragmentation candidate when one-shot helper density becomes substantial.

### QG019

1001–2000 line owner -> Semantic. >2000 -> Critical. Semantic evidence includes definitions, owner clusters, dependency/state edges and candidate split boundaries.

### QG168

Builds chains only from eligible ordinary direct-call edges between ephemeral helpers. Callback/protocol/override/closure/resource boundaries break the chain.

### QG003–QG006

Use ContractFact to distinguish internal contract guessing from legitimate external optional access. This refines confidence/severity without weakening explicit internal-contract enforcement.

## 5. Cohesion evidence

v0.21.4 intentionally avoids a magic numeric cohesion hard gate. Instead the semantic evidence bundle includes:

- owner method/function count;
- field/state connected components;
- shared private primitive consumers;
- direct/callback/protocol edge counts;
- owner dependency clusters;
- suggested independent boundaries derived from disconnected clusters.

Semantic review decides whether the owner remains cohesive. A later release may introduce a quantitative rule only after corpus validation.

## 6. Dependency-aware analysis

The resolver is demand-driven. Given an external symbol, it may inspect local source, installed package source, `.pyi` stubs or static package metadata. It resolves only enough information to classify signature/protocol/callback relationships.

It does not:

- execute `import` merely for introspection;
- recursively audit third-party packages;
- convert unresolved dependency information into a hard conclusion.

## 7. Checkpoint/release architecture

Intermediate state is represented primarily by Git commits and small metadata. Library `/checkpoints/` contains lightweight manifests/mbox/logs. Large already-persisted inputs are referenced by Library path + SHA256 instead of copied.

The final release remains a deterministic full skill ZIP plus mbox. The `.agents` deployment stays slim and must not inherit the release wheelhouse/source profiles/dev tooling.
