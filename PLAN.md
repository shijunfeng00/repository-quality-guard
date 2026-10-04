# Repository Quality Guard v0.21.4 PLAN

## 0. Authority and baseline

This document is the planning authority for v0.21.4. `TODOLIST.md` tracks execution, `CHECKLIST.md` defines acceptance gates, and `ARCH.md` defines target ownership. Tests and historical behavior are evidence, not planning authority when they conflict with this plan.

Stable baseline: tag `v0.21.3`, commit `9b546b8530ce4e703e1d4498afd710cd6f1ba838`.

v0.21.4 has exactly two product problems and they are implemented sequentially. **Phase A must be committed, fully verified, and checkpointed before Phase B changes production code.**

## 1. Phase A — progressive debt accountability and compact modification reports

### 1.1 Product intent

The long-term policy is **zero new debt + progressive repayment of touched historical debt**, not mandatory whole-repository cleanup on every patch.

Repository-wide analysis may still be needed to build call/ownership topology. Analysis scope and responsibility scope are different concepts.

Default mode is `progressive`:

1. **New or worsened ordinary Critical/Error/Warning debt** relative to the Git baseline remains zero-tolerance. It must be fixed/reverted; prose cannot waive it.
2. **Historical ordinary Critical/Error/Warning debt in production files touched by the current diff** is fully in-scope. It must not be sampled. Every still-present item must either:
   - be fixed so it disappears from the current scan; or
   - remain with an explicit `DEFERRED` explanation containing a concrete reason, scope impact and closure condition. “Historical debt”, “out of scope”, or equivalent generic wording is insufficient by itself.
3. **Historical debt outside changed production files** remains visible as machine-generated inventory/statistics but creates no model-authored explanation burden and no default rejection requirement.
4. **Historical debt actually removed or downgraded** is reported as debt paydown.
5. A separate explicit cleanup mode promotes historical debt in the selected repository/path/file scope to mandatory cleanup. This mode is a responsibility policy, not a switch for whether the repository is scanned.
6. If no usable Git/diff baseline exists, the explicitly selected audit scope is treated as fully responsible historical-debt scope; existing ordinary debt there must be cleared rather than silently treated as unrelated history.

### 1.2 CLI contract

Use an explicit policy argument rather than ambiguous `--all` wording:

- `--debt-mode progressive` — default.
- `--debt-mode cleanup` — selected scope historical ordinary C/E/W must be zero before acceptance.

Do not add a second “scan everything” flag. Existing path / `--project` / `--files` selection continues to define user scope; whole-repository facts may still be collected internally for topology.

### 1.3 Report contract

`修改说明.md` must always contain machine-generated debt facts:

- baseline ordinary debt total;
- current ordinary debt total;
- introduced/worsened debt count;
- historical debt reduced count;
- touched historical debt count;
- untouched historical debt count;
- cleanup-required remaining debt count when cleanup mode is active.

Detailed sections:

- `DELTA-*`: new/worsened debt — existing zero-regression semantics remain.
- `DEBT-*`: **all** still-present touched historical ordinary C/E/W in progressive mode; each requires a unique deferral row if not fixed.
- untouched historical debt: machine-generated compact inventory only (`QG`, severity, path:line, message); no manual row.
- cleanup mode: all still-present historical ordinary C/E/W in selected scope become blocking facts; explanations cannot waive them.

Remove the old “touched historical finite sample” and the repository-wide “if zero debt was reduced, explain the whole scope” obligation. Those mechanisms do not match this plan.

### 1.4 Report-size constraint

Do not reduce evidence quality by hiding debt. Reduce **model-authored prose**, not machine facts.

A large repository may legitimately produce a large automatic inventory table, but the Agent must not write hundreds of repetitive paragraphs for untouched debt. Manual rows exist only for:

- new/changed interface and semantic-review decisions already required by their own contracts;
- touched historical debt that remains after the patch.

### 1.5 Phase A acceptance

- Boundary tests prove 3 changed files expose all historical debt in those 3 files and do not demand explanations for identical debt in untouched files.
- A touched historical item cannot pass with generic “historical/out of scope” prose.
- Fixing a touched item removes its manual obligation and increases debt-reduced statistics.
- Cleanup mode rejects any selected-scope remaining historical C/E/W.
- Default progressive mode does not reject solely because untouched historical debt exists.
- Non-Git/no-baseline selected scope is treated as cleanup-responsible.
- Existing new-debt zero-regression behavior remains unchanged.
- Report regeneration preserves completed DEBT decisions by stable ID when the finding identity is unchanged.
- Full suite, self-audit, deterministic replay and checkpoint pass before Phase B begins.

## 2. Phase B — C++ build truth and compiler-neutral semantic facts

### 2.1 Problem statement

v0.21.3 normalized C++ topology around a Clang AST provider, but it can invoke a fixed `clang++ -std=c++20 ...` command and reconstruct include roots instead of consuming the project’s real build command. That is insufficient for GCC/G++, Clang and MSVC projects with compiler-specific flags, macros, system headers, generated headers or target configuration.

A semantic frontend must never override native build truth. A valid target proven by its project compiler must not become an audit failure merely because an auxiliary frontend cannot parse it.

### 2.2 Architecture principles

1. **Build contract first.** Prefer an existing `compile_commands.json` or a build-system-produced/captured compilation database. Do not guess flags when authoritative commands exist.
2. **Reuse existing ecosystem tooling.** Before writing compiler-specific AST adapters, evaluate mature compilation-database/indexing protocols and tools. Do not turn RQG into a home-grown universal C++ AST project.
3. **Native compiler truth is authoritative for build validity.** GCC project validity comes from its GCC command, Clang from Clang, MSVC from the real MSVC build contract where available.
4. **Semantic provider is separable.** A provider may use Clang/clangd/SCIP or another mature indexer against normalized compile commands, but provider incompatibility yields `UNKNOWN/N/A`, not “source is invalid”.
5. **Per-TU graceful degradation.** One unavailable translation unit or BASE snapshot must not abort unrelated rules or the TARGET audit.
6. **Normalized topology remains the policy boundary.** QG rules consume language-neutral owner/call/reference/protocol/state facts and do not grow GCC/Clang/MSVC-specific rule families.

### 2.3 Required empirical fixtures

- **SlowJSON**: GCC/G++ project with CMake and unit tests. The temporary GCC14 compatibility mbox is calibration/reference only, not the authoritative future SlowJSON design. Do not optimize SlowJSON itself as part of RQG work.
- **geek-ai-agent sandbox C++**: determine its actual build compiler/toolchain and use it as a second compiler-family/build-system fixture if suitable.
- Preserve public project behavior; RQG fixture preparation must not redefine project APIs or add runtime behavior merely to help the auditor.

### 2.4 Phase B investigation gate

Before production implementation, compare available mature approaches against these requirements: compilation database fidelity, GCC/Clang/MSVC coverage, offline/package burden, structured symbol/call/type facts, licensing, deterministic installation and graceful degradation. Prefer composition over embedding a new universal AST system.

The chosen design and rejected alternatives must be recorded before implementation.

### 2.5 Phase B acceptance

- Real compile commands are consumed when available; tests prove compiler/flags/macros/includes/cwd are not replaced by fixed defaults.
- SlowJSON GCC fixture: native build truth can be consumed without pretending fixed Clang parsing is authoritative.
- Second C++ fixture exercises a different toolchain/compiler path where available.
- BASE semantic-provider failure cannot abort the complete diff audit.
- Missing/incompatible semantic facts remain explicit `UNKNOWN/N/A`.
- Python/JS/TS behavior and v0.21.3 topology regressions remain unchanged.
- Full real-project regressions, lifecycle gates, deterministic package and final v0.21.4 release pass.

### 2.6 Investigation decision

Phase B will **not** embed GCC/MSVC-specific AST frontends or a new universal C++ AST system. The first v0.21.4 implementation uses three layers:

1. `compile_commands.json` is the authoritative translation-unit build contract when available. Compiler executable, cwd, standard, defines, include paths, target and semantic flags are preserved rather than replaced by fixed defaults.
2. Native compiler identity/availability is recorded separately from semantic-provider availability. GCC/Clang/MSVC build truth is not rewritten into a different compiler invocation.
3. Existing Clang AST extraction remains an optional semantic enrichment provider. Native-Clang compile commands may feed it directly; non-Clang commands remain valid build evidence but do not become Clang commands by guesswork. Provider failure is per-TU `UNKNOWN/N/A` and never an audit-wide source-invalid verdict.

Mature alternatives were evaluated before implementation: compilation databases are the portable build-contract protocol; clangd/LibTooling consume them and can query GCC-compatible drivers, while SCIP-clang also consumes compilation databases but remains Clang-frontend-based and adds a heavier platform/package dependency. Tree-sitter C++ is a suitable future compiler-neutral syntax-floor provider, but adding a new grammar/wheel matrix is deliberately deferred from v0.21.4. The normalized topology boundary remains unchanged so such providers can be added later without compiler-specific QG families.

Empirical fixtures: SlowJSON and geek-ai-agent sandbox are both GCC/G++ CMake projects. Therefore compiler-family diversity is covered by explicit Clang/MSVC-style compilation-database contract tests in this release; native Windows/MSVC validity remains `UNKNOWN` when no Windows toolchain exists rather than being simulated.

## 3. Non-goals

- Do not redesign SlowJSON in this release.
- Do not implement one AST frontend per compiler unless the Phase B investigation proves there is no smaller mature composition.
- Do not weaken new-debt zero-regression to reduce report noise.
- Do not require explanations for unrelated untouched historical debt in progressive mode.
- Do not let cleanup mode become the default.
