"""Repository-owned directory and test-support topology policy (QG240).

The closed RQG source layout is independent of generic rqgtest Case semantics.
This module is invoked only by RQG's own scanner, not for other projects.
"""
from __future__ import annotations

import ast
import os
from importlib.util import resolve_name
from pathlib import Path

from .case_contract_source_identity import bound_names
from .case_contract_rules import case_contract_findings
from .config import is_test_source_path
from .facts import ModuleFacts
from .model import Finding
from .topology_facts import RepositoryTopology, Visibility

_RQG_FROZEN_DIRECTORY_PATHS = frozenset({
    "agents/", "assets/", "docs/", "docs/api-reference/",
    "offline/", "offline/wheelhouse/", "packages/", "packages/rqgtest/",
    "packages/rqgtest/examples/", "packages/rqgtest/examples/quickstart/",
    "packages/rqgtest/examples/quickstart/tests/",
    "packages/rqgtest/examples/quickstart/tests/data/",
    "packages/rqgtest/src/", "packages/rqgtest/src/rqgtest/",
    "packages/rqgtest/src/rqgtest/core/", "packages/rqgtest/src/rqgtest/data/",
    "packages/rqgtest/src/rqgtest/discovery/", "packages/rqgtest/tests/",
    "packages/rqgtest/selftest/", "packages/rqgtest/selftest/tests/", "profiles/",
    "profiles/council-of-harnesses/", "profiles/geek-ai-agent/",
    "profiles/geek-ai-rag/", "profiles/qg-example-profile/",
    "profiles/repository-quality-guard/", "references/", "runtime/",
    "runtime/contracts/", "runtime/contracts/c/", "runtime/contracts/cpp/",
    "runtime/contracts/go/", "runtime/contracts/java/",
    "runtime/contracts/typescript/", "runtime/src/", "scripts/", "templates/",
    "tests/", "tests/cases/", "tests/dataloaders/",
})


def repository_directory_layout_findings(
    root: Path,
    topology: RepositoryTopology | None = None,
    module_facts: list[ModuleFacts] | None = None,
) -> list[Finding]:
    """Audit RQG-owned directory structure and graph-proven test helpers.

    Args:
        root: Source repository root for this RQG-specific policy.
        topology: Optional resolved graph for verifying test-only consumers.
        module_facts: Parsed module facts from the same scanner snapshot.

    Returns:
        Confirmed directory and test-support contract findings.
    """
    root = Path(root).resolve()
    actual: set[str] = set()
    for current, directories, files in os.walk(root):
        relative_parent = Path(current).relative_to(root)
        directories[:] = sorted(
            name for name in directories
            if name not in {".git", "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache", ".qg-work"}
        )
        # Private Profile internals are not part of the product-root directory
        # allowlist. Keep each direct profiles/<profile>/ directory visible,
        # then validate its optional tests/ tree under the same closed test
        # contract below instead of treating it as a root-level extra directory.
        if relative_parent.parts[:1] == ("profiles",) and len(relative_parent.parts) >= 2:
            directories[:] = []
            continue
        for name in directories:
            relative = (relative_parent / name).as_posix() + "/"
            actual.add(relative)

    findings: list[Finding] = []

    def add(path: str, violation: str, message: str) -> None:
        """Record a validated physical directory-layout violation.

        Args:
            path: Repository-relative path supporting the finding.
            violation: Machine-readable layout violation kind.
            message: Human-readable observed layout contradiction.

        Returns:
            None; appends a QG240 Finding.
        """
        findings.append(Finding(
            code="QG240",
            severity="critical",
            confidence="high",
            path=path.rstrip("/"),
            line=1,
            column=1,
            symbol=path.rstrip("/"),
            message=message,
            suggestion="恢复冻结的仓库目录清单；新增、删除、重命名或嵌套目录必须先获得明确批准。",
            evidence={
                "framework": "rqgtest",
                "kind": "RQG_DIRECTORY_LAYOUT",
                "violation": violation,
                "relative_path": path.rstrip("/"),
                "absolute_blocker": True,
            },
            source="test-contract",
        ))

    for relative in sorted(actual - _RQG_FROZEN_DIRECTORY_PATHS):
        add(relative, "unexpected_directory", "仓库目录不在冻结清单内；禁止新增或嵌套未批准目录。")
    for relative in sorted(_RQG_FROZEN_DIRECTORY_PATHS - actual):
        add(relative, "missing_directory", "冻结的仓库目录缺失；禁止未经批准删除、重命名或重嵌套目录。")

    tests_root = root / "tests"
    if tests_root.is_dir():
        for item in sorted(tests_root.iterdir(), key=lambda path: path.name):
            if item.is_dir():
                if item.name in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache", ".qg-work"}:
                    continue  # Regenerable tooling/cache output is not repository source layout.
                if item.name not in {"cases", "dataloaders"}:
                    add(item.relative_to(root).as_posix(), "unexpected_tests_child", "tests/ 直接子目录只允许 cases/ 与 dataloaders/。")
            elif item.is_file() and not (item.name.startswith("test_") and item.suffix == ".py"):
                add(item.relative_to(root).as_posix(), "unexpected_tests_file", "tests/ 根目录只允许 test_*.py Case 模块及 cases/、dataloaders/。")

    dataloaders = tests_root / "dataloaders"
    if dataloaders.is_dir():
        for item in sorted(dataloaders.iterdir(), key=lambda path: path.name):
            if item.is_file() and item.suffix != ".py":
                add(item.relative_to(root).as_posix(), "non_python_dataloader_asset", "dataloaders/ 只允许符合协议的 Python DataLoader 模块；静态数据应放 cases/。")

    # Private Profiles have independent test roots: their fixtures and loaders
    # stay under profiles/<profile>/tests/, never in the public root tests/.
    # The rest of each private Profile tree is intentionally outside the frozen
    # product-root layout contract, but an existing tests/ tree must obey the
    # exact same Case/cases/DataLoader shape and rqgtest module contract.
    profiles_root = root / "profiles"
    known_fact_paths = {
        Path(item.path).resolve() if Path(item.path).is_absolute()
        else (root / item.path).resolve()
        for item in (module_facts or ())
    }
    if profiles_root.is_dir():
        for profile_root in sorted(
            (item for item in profiles_root.iterdir() if item.is_dir()),
            key=lambda path: path.name,
        ):
            profile_tests = profile_root / "tests"
            if not profile_tests.is_dir():
                continue
            for item in sorted(profile_tests.iterdir(), key=lambda path: path.name):
                relative = item.relative_to(root).as_posix()
                if item.is_dir():
                    if item.name in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache", ".qg-work"}:
                        continue
                    if item.name not in {"cases", "dataloaders"}:
                        add(relative, "unexpected_tests_child", "Profile tests/ 直接子目录只允许 cases/ 与 dataloaders/。")
                elif item.is_file() and not (item.name.startswith("test_") and item.suffix == ".py"):
                    add(relative, "unexpected_tests_file", "Profile tests/ 根目录只允许 test_*.py Case 模块及 cases/、dataloaders/。")

            profile_cases = profile_tests / "cases"
            if profile_cases.is_dir():
                for item in sorted(profile_cases.iterdir(), key=lambda path: path.name):
                    if item.is_dir() and item.name in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache", ".qg-work"}:
                        continue
                    if item.is_dir():
                        add(item.relative_to(root).as_posix(), "unexpected_directory", "Profile tests/cases/ 不允许新增或嵌套目录。")

            profile_dataloaders = profile_tests / "dataloaders"
            if profile_dataloaders.is_dir():
                for item in sorted(profile_dataloaders.iterdir(), key=lambda path: path.name):
                    relative = item.relative_to(root).as_posix()
                    if item.is_dir() and item.name in {"__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".cache", ".qg-work"}:
                        continue
                    if item.is_dir():
                        add(relative, "unexpected_directory", "Profile tests/dataloaders/ 不允许新增或嵌套目录。")
                    elif item.is_file() and item.suffix != ".py":
                        add(relative, "non_python_dataloader_asset", "Profile tests/dataloaders/ 只允许 Python DataLoader 模块；静态数据应放本 Profile 的 cases/。")

            # Git ignores private Profile payloads, so parse their test modules
            # explicitly when they are absent from the ordinary scanner facts.
            # This preserves QG240-246 TestCase/DataLoader checks without
            # applying product-root layout rules to unrelated Profile folders.
            for test_file in sorted(profile_tests.rglob("*.py")):
                resolved = test_file.resolve()
                if resolved in known_fact_paths:
                    continue
                try:
                    tree = ast.parse(test_file.read_text(encoding="utf-8"), filename=str(test_file))
                except (OSError, SyntaxError) as error:
                    add(test_file.relative_to(root).as_posix(), "invalid_test_source", f"Profile 测试源码无法解析：{error}")
                    continue
                findings.extend(
                    case_contract_findings(
                        test_file.relative_to(root), tree, root, topology=topology
                    )
                )

    # Reuse the scanner's parsed imports and resolved symbol graph to identify
    # classes consumed only from tests. A spelling-based denylist is insufficient:
    # a real production import or resolved production call suppresses this finding.
    if topology is not None and module_facts is not None:
        module_paths: dict[str, Path] = {}
        fact_by_path: dict[Path, ModuleFacts] = {}
        for fact in module_facts:
            source = Path(fact.path)
            if not source.is_absolute():
                source = root / source
            source = source.resolve()
            module_paths[fact.module] = source
            fact_by_path[source] = fact

        importers: dict[Path, set[Path]] = {}
        for source, fact in fact_by_path.items():
            tree = fact.tree
            if tree is None:
                continue
            relative = source.relative_to(root)
            package = (
                fact.module
                if relative.name == "__init__.py"
                else fact.module.rpartition(".")[0]
            )
            targets: set[str] = set()
            importlib_aliases: set[str] = set()
            import_module_aliases: set[str] = set()
            for statement in tree.body:
                if isinstance(statement, ast.Import):
                    importlib_aliases.update(
                        alias.asname or alias.name.split(".", 1)[0]
                        for alias in statement.names if alias.name == "importlib"
                    )
                elif isinstance(statement, ast.ImportFrom) and statement.module == "importlib":
                    import_module_aliases.update(
                        alias.asname or alias.name
                        for alias in statement.names if alias.name == "import_module"
                    )
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    targets.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    try:
                        base = (
                            resolve_name("." * node.level + (node.module or ""), package)
                            if node.level else (node.module or "")
                        )
                    except ImportError:
                        continue
                    if base:
                        targets.add(base)
                        targets.update(
                            f"{base}.{alias.name}"
                            for alias in node.names if alias.name != "*"
                        )
                elif isinstance(node, ast.Call) and node.args:
                    function = node.func
                    dynamic_import = (
                        isinstance(function, ast.Attribute)
                        and function.attr == "import_module"
                        and isinstance(function.value, ast.Name)
                        and function.value.id in importlib_aliases
                    ) or (
                        isinstance(function, ast.Name)
                        and function.id in import_module_aliases
                    ) or (
                        isinstance(function, ast.Name)
                        and function.id == "__import__"
                        and "__import__" not in bound_names(tree.body, skip_nested=True)
                    )
                    if dynamic_import and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        targets.add(node.args[0].value)

            for imported in targets:
                parts = imported.split(".")
                target = None
                for boundary in range(len(parts), 0, -1):
                    target = module_paths.get(".".join(parts[:boundary]))
                    if target is not None:
                        break
                if target is not None:
                    importers.setdefault(target, set()).add(source)

        incoming: dict[str, list[object]] = {}
        for edge in topology.edges:
            incoming.setdefault(edge.target_id, []).append(edge)
        symbols_by_path: dict[Path, list[object]] = {}
        for symbol in topology.symbols:
            symbol_path = Path(symbol.path)
            if not symbol_path.is_absolute():
                symbol_path = root / symbol_path
            symbol_path = symbol_path.resolve()
            if not symbol_path.is_relative_to(root):
                continue
            symbols_by_path.setdefault(symbol_path, []).append(symbol)

        framework_bases = {
            "TestCase", "StatefulTestCase", "DataLoader", "JSONDataLoader",
            "JSONLDataLoader", "CSVDataLoader", "PickleDataLoader",
            "CoreQualityRule", "QualityRule", "QualityGuardProfile", "NodeVisitor",
        }
        module_helper_paths: set[Path] = set()

        def resolved_consumers(symbol_ids: set[str]) -> tuple[list[object], list[object]]:
            """Partition graph edges into test and production consumers.

            Args:
                symbol_ids: Canonical source symbols whose incoming edges are inspected.

            Returns:
                Separate lists of test and production consumer edges.
            """
            edges = [edge for symbol_id in symbol_ids for edge in incoming.get(symbol_id, ())]
            test_edges = [edge for edge in edges if is_test_source_path(Path(edge.path))]
            production_edges = [edge for edge in edges if not is_test_source_path(Path(edge.path))]
            return test_edges, production_edges

        def emit_helper_finding(
            source: Path,
            symbol_id: str,
            violation: str,
            test_importers: set[Path],
            production_importers: set[Path],
            test_edges: list[object],
            production_edges: list[object],
            line: int = 1,
        ) -> None:
            """Record a test-only support dependency proven by the graph.

            Args:
                source: Resolved source file that owns the unsupported helper.
                symbol_id: Symbol identity or module label.
                violation: Reason the owned helper violates the directory policy.
                test_importers: Test files importing the helper module.
                production_importers: Production files importing the module.
                test_edges: Resolved test consumer edges.
                production_edges: Resolved production consumer edges.
                line: Definition line for the finding.

            Returns:
                None; appends one evidence-backed Finding.
            """
            relative = source.relative_to(root).as_posix()
            findings.append(Finding(
                code="QG240",
                severity="critical",
                confidence="high",
                path=relative,
                line=line,
                column=1,
                symbol=symbol_id,
                message=(
                    "已解析的 import/consumer 图证明该非 Case 辅助模块或类只有测试消费者，"
                    "没有生产、CLI 或 Release 消费者；不得绕过 rqgtest 协议维护测试专用实现。"
                ),
                suggestion=(
                    "删除测试专用辅助实现，或让正式 Case 直接验证真实生产行为；"
                    "不要添加 helper/support 目录、模块或非 Case 辅助类。"
                ),
                evidence={
                    "framework": "rqgtest",
                    "kind": "RQG_TEST_ONLY_SUPPORT_GRAPH",
                    "violation": violation,
                    "relative_path": relative,
                    "symbol_id": symbol_id,
                    "test_importers": sorted(path.relative_to(root).as_posix() for path in test_importers),
                    "production_importers": sorted(path.relative_to(root).as_posix() for path in production_importers),
                    "test_consumers": sorted({Path(edge.path).as_posix() for edge in test_edges}),
                    "production_consumers": sorted({Path(edge.path).as_posix() for edge in production_edges}),
                    "absolute_blocker": True,
                },
                source="test-contract",
            ))

        for source, symbols in sorted(symbols_by_path.items(), key=lambda item: item[0].as_posix()):
            if not source.is_file() or is_test_source_path(source.relative_to(root)):
                continue
            relative = source.relative_to(root)
            # Script/profile entrypoints can be loaded externally rather than
            # imported by another source file; do not infer missing consumers.
            if relative.parts[0] in {"scripts", "profiles"}:
                continue
            fact = fact_by_path.get(source)
            tree = fact.tree if fact is not None else None
            if tree is not None and any(
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
                and any(
                    isinstance(comparator, ast.Constant)
                    and comparator.value == "__main__"
                    for comparator in node.test.comparators
                )
                for node in tree.body
            ):
                continue
            consumers = {
                importer for importer in importers.get(source, set())
                if importer.resolve() != source.resolve()
            }
            test_importers = {
                importer for importer in consumers
                if is_test_source_path(importer.relative_to(root))
            }
            production_importers = {
                importer for importer in consumers
                if not is_test_source_path(importer.relative_to(root))
            }
            if not test_importers:
                continue

            top_level_symbols = [
                symbol for symbol in symbols
                if not symbol.nested and symbol.kind in {"class", "function"}
            ]
            private_only_functions = (
                not production_importers
                and bool(top_level_symbols)
                and all(symbol.kind == "function" for symbol in top_level_symbols)
                and all(
                    symbol.visibility in {Visibility.PRIVATE, Visibility.INTERNAL}
                    and not symbol.exported
                    for symbol in top_level_symbols
                )
                and tree is not None
                and not any(
                    isinstance(node, (ast.Assign, ast.AnnAssign))
                    and "__all__" in ast.unparse(node)
                    for node in tree.body
                )
                and all(
                    isinstance(node, (ast.Import, ast.ImportFrom))
                    or (
                        index == 0
                        and isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    )
                    or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("_")
                    or isinstance(node, (ast.Assign, ast.AnnAssign))
                    and all(
                        isinstance(target, ast.Name) and target.id.startswith("_")
                        for target in (
                            node.targets if isinstance(node, ast.Assign) else [node.target]
                        )
                    )
                    for index, node in enumerate(tree.body)
                )
            )
            if private_only_functions:
                function_ids = {symbol.symbol_id for symbol in top_level_symbols}
                test_edges, production_edges = resolved_consumers(function_ids)
                if test_edges and not production_edges:
                    module_helper_paths.add(source)
                    emit_helper_finding(
                        source,
                        fact.module if fact is not None else relative.as_posix(),
                        "test_only_helper_module",
                        test_importers,
                        production_importers,
                        test_edges,
                        production_edges,
                    )
                    continue

            for symbol in symbols:
                if symbol.exported or symbol.nested or symbol.kind not in {"class", "function"}:
                    continue
                if source in module_helper_paths:
                    continue
                is_internal = symbol.visibility in {Visibility.PRIVATE, Visibility.INTERNAL}
                # Public functions can be legitimate production APIs directly tested
                # through their API. A public class is suspicious only when its whole
                # module has no production importer. Private/internal symbols are
                # checked independently so helpers cannot hide in a shared module.
                if not is_internal and (symbol.kind != "class" or production_importers):
                    continue
                if symbol.kind == "class" and any(
                    base.split(".")[-1] in framework_bases for base in symbol.bases
                ):
                    continue
                target_ids = {symbol.symbol_id}
                if symbol.kind == "class":
                    target_ids.update(
                        candidate.symbol_id
                        for candidate in symbols
                        if candidate.symbol_id.startswith(symbol.symbol_id + ".")
                    )
                test_edges, production_edges = resolved_consumers(target_ids)
                if symbol.kind == "class":
                    production_edges = [
                        edge for edge in production_edges
                        if not edge.source_id.startswith(symbol.symbol_id + ".")
                    ]
                if not test_edges or production_edges:
                    continue
                emit_helper_finding(
                    source,
                    symbol.symbol_id,
                    "test_only_class_consumer" if symbol.kind == "class" else "test_only_helper_function",
                    test_importers,
                    production_importers,
                    test_edges,
                    production_edges,
                    line=symbol.line,
                )
    return findings
