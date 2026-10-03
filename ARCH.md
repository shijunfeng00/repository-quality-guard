# Repository Quality Guard v0.21.4 Architecture

Stable baseline: v0.21.3. v0.21.4 keeps the normalized topology/rule architecture and changes two ownership boundaries.

## 1. Debt accountability boundary

### Analysis scope

The scanner may analyze the repository broadly to resolve ownership, imports, calls and topology.

### Responsibility scope

A separate report-policy layer classifies ordinary production C/E/W findings relative to the selected Git baseline:

- `DELTA`: introduced or worsened — always blocking until removed.
- `TOUCHED_HISTORICAL`: existed at baseline and remains in a changed production file — progressive mode requires concrete deferral if still present.
- `UNTOUCHED_HISTORICAL`: existed at baseline and remains outside changed production files — automatic inventory only in progressive mode.
- `REDUCED_HISTORICAL`: baseline debt removed or downgraded — automatic paydown fact.

`cleanup` debt mode promotes historical debt in the selected report scope to required cleanup. It does not change repository topology collection.

### Owners

- CLI owns debt-mode selection only.
- `ScanReport` carries the resolved mode as immutable scan context.
- `report_contract` owns ordinary-debt classification, stable `DEBT-*` facts, counts and acceptance semantics.
- `compact_report` owns compact automatic inventory and model-authored DEFERRED rows.
- Static QG rules do not know debt mode.

## 2. C++ build-truth boundary

v0.21.3 had a normalized `CppFactProvider` but could reconstruct a fixed Clang invocation. v0.21.4 separates build truth from semantic extraction.

Target architecture:

```text
Build system / compile_commands / captured commands
                    |
                    v
          TranslationUnitBuildSpec
 compiler, cwd, standard, defines, includes, target, semantic flags
                    |
        +-----------+-----------+
        |                       |
        v                       v
 Native build validity    Semantic provider(s)
 real GCC/Clang/MSVC      mature existing tooling
        |                       |
        +-----------+-----------+
                    v
        normalized C++ topology facts
                    |
                    v
                 QG rules
```

### Principles

- Build command evidence is authoritative when available.
- Native compiler success/failure and auxiliary semantic-provider success/failure are separate facts.
- No provider may turn “I cannot parse this valid native command” into “the source is invalid”.
- Per-TU unavailable facts remain unknown and do not poison other translation units.
- Compiler-specific implementation details stay below normalized topology.
- Prefer existing compilation database/indexing ecosystems to a custom universal AST framework.

Phase B does not begin production implementation until the provider/tooling investigation is recorded after the Phase A checkpoint.
