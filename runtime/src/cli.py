from __future__ import annotations

import argparse
import copy
from fnmatch import fnmatch
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from .audit_target import (
    AuditTarget as AuditTarget,
    GIT_COMMAND_NOT_FOUND_EXIT_CODE as GIT_COMMAND_NOT_FOUND_EXIT_CODE,
    GitRootLookup as GitRootLookup,
    build_parser as build_parser,
    git_failure_lookup as git_failure_lookup,
    resolve_target as resolve_target,
    git_comparison_plan,
    apply_audit_mode,
    git_stdout,
)
from .baseline_parallel import GitBaselineTask
from .analysis_snapshot import (
    RepositoryAnalysisSnapshot,
    directory_analysis_snapshot,
    index_analysis_snapshot,
    revision_analysis_snapshot,
    worktree_analysis_snapshot,
)
from .api_catalog import apply_search_strategy, build_api_catalog, search_api_catalog
from .architecture_diff import compare_git_architecture
from .call_chain_rules import single_use_chain_findings
from .config import GuardConfig, is_test_path
from .fallback_laundering import fallback_laundering_findings
from .integrity import INTEGRITY_RULE_CODE, verify_release_integrity
from .interface_diff import GitWorktreeInterfaceComparator, PythonInterfaceExtractor
from .interface_policy_rules import (
    InterfaceChangeReviewRule,
    InterfaceDefinitionGrowthRule,
    InterfaceGrowthContext,
)
from .interface_visibility import filter_interface_symbols, resolve_import_aliases
from .multilang import multilang_findings
from .baseline_regression_rules import (
    DocstringRegressionRule,
    QualityFingerprintRegressionRule,
    QualityRegressionContext,
)
from .model import (
    Finding,
    InterfaceChange,
    InterfaceDiffReport,
    QualityBaseline,
    ScanReport,
)
from .project_contracts import validate_project_contracts
from .release_identity_rules import release_identity_findings
from .project_profiles import (
    ProfileSelection,
    ProjectProfile,
    RuleContext,
    apply_project_profile,
    get_project_profile,
    resolve_profile_reference,
)
from .report_contract import (
    code_status,
    render_strict_modification_report,
    report_final_status,
    validate_modification_report,
)
from .report_schema import REPORT_FILENAME
from .reporting import (
    SEVERITY_RANK,
    merge_findings,
    render_audit_markdown,
)
from .relation_graph import (
    RepositoryRelationGraph,
    apply_relation_finding_context,
    build_relation_graph_summary,
    normalized_topology,
)
from .python_dependency_facts import StaticPythonDependencyResolver
from .ruff_runner import RuffRunner
from .scanner import RepositoryScanner, snapshot_only_rule_findings
from .scoped_rule_policy import ScopedRuleMatcher
from .semantic_heuristic_candidates import semantic_heuristic_candidate_findings
from .semantic_review import annotate_semantic_review, semantic_review_key

MODIFICATION_REPORT_NAME = REPORT_FILENAME
REPORT_GATE_EXIT_CODE = 3
INTEGRITY_GATE_EXIT_CODE = 4
REVIEW_REQUIRED_EXIT_CODE = 5
_INTERFACE_DEFINITION_KINDS = frozenset({"class", "function", "method"})
_INTERFACE_POLICY_CODES = frozenset({"QG180", "QG181", "QG182"})


def _filter_test_quality_findings(
    findings: list[Finding],
    passthrough_patterns: tuple[str, ...],
) -> list[Finding]:
    """Apply the one canonical test-quality baseline projection."""
    return [
        item
        for item in findings
        if any(
            fnmatch(Path(item.path).as_posix().lstrip("./"), pattern)
            for pattern in passthrough_patterns
        )
        or item.code
        in {"QG000", "QG149", "QG240", "QG241", "QG242", "QG243", "QG244", "QG245", "QG246"}
        or item.source in {"ruff", "integration"}
    ]


def _cache_root() -> Path:
    """
    返回 Ruff/uv 运行时缓存根目录。

    Returns:
        可写缓存目录路径。
    """
    if "REPO_QUALITY_GUARD_CACHE_DIR" in os.environ:
        return Path(os.environ["REPO_QUALITY_GUARD_CACHE_DIR"])
    return Path(tempfile.gettempdir()) / "repository-quality-guard"


def _filter_interface_diff(
    interface_diff: InterfaceDiffReport | None,
    target: AuditTarget,
) -> InterfaceDiffReport | None:
    """
    把接口差异明细限制到聚焦文件或目录。

    Args:
        interface_diff: 原始接口差异报告。
        target: 当前审计目标。

    Returns:
        过滤后的接口差异报告；为空时仍返回空报告。
    """
    if interface_diff is None:
        return None
    if not target.focus_files and not target.focus_roots:
        return interface_diff
    return InterfaceDiffReport(
        base=interface_diff.base,
        target=interface_diff.target,
        changes=tuple(
            change
            for change in interface_diff.changes
            if (
                (target.root / change.path).resolve() in target.focus_files
                if target.focus_files
                else any(
                    (target.root / change.path).resolve() == root
                    or (target.root / change.path).resolve().is_relative_to(root)
                    for root in target.focus_roots
                )
            )
        ),
        base_files=interface_diff.base_files,
        target_files=interface_diff.target_files,
        errors=interface_diff.errors,
        contract_findings=tuple(
            finding
            for finding in interface_diff.contract_findings
            if (
                (target.root / finding.path).resolve() in target.focus_files
                if target.focus_files
                else any(
                    (target.root / finding.path).resolve() == root
                    or (target.root / finding.path).resolve().is_relative_to(root)
                    for root in target.focus_roots
                )
            )
        ),
        api_scope=interface_diff.api_scope,
        private_min_lines=interface_diff.private_min_lines,
    )


def _apply_focus(report: ScanReport, target: AuditTarget) -> ScanReport:
    """
    应用输出聚焦范围和审计范围说明。

    Args:
        report: 待处理的扫描报告。
        target: 当前审计目标。

    Returns:
        原地更新后的报告。
    """
    if target.focus_files or target.focus_roots:
        report.findings = [
            item
            for item in report.findings
            if (
                (target.root / item.path).resolve() in target.focus_files
                if target.focus_files
                else any(
                    (target.root / item.path).resolve() == root
                    or (target.root / item.path).resolve().is_relative_to(root)
                    for root in target.focus_roots
                )
            )
        ]
        report.interface_diff = _filter_interface_diff(report.interface_diff, target)
        report.focus_paths = tuple(
            sorted(str(path) for path in (*target.focus_files, *target.focus_roots))
        )
    report.scope_notes = (
        (
            *target.notes,
            target.git_issue_message or "Git 历史比较不可用；当前源码扫描正常完成。",
        )
        if not target.git_enabled
        else target.notes
    )
    report.findings.sort(
        key=lambda item: (
            -SEVERITY_RANK[item.severity],
            item.path,
            item.line,
            item.code,
        )
    )
    return report


def _apply_rule_level(
    item: Finding,
    config: GuardConfig,
    matcher: ScopedRuleMatcher | None = None,
    disabled: frozenset[str] | None = None,
    levels: Mapping[str, str] | None = None,
) -> Finding | None:
    """Apply one declarative Profile rule level without changing core rule ownership.

    ``SEMANTIC`` remains an Info finding but is forced into the semantic-review
    ledger. ``BLOCKER`` remains a Critical finding for display while carrying an
    independent absolute-blocker marker; this preserves the historical meaning
    that Critical is baseline-aware while Blocker is not.
    """
    disabled_codes = (
        disabled if disabled is not None else frozenset(config.disabled_rules)
    )
    if item.code in disabled_codes or (
        matcher is not None and matcher.matches(item.code, item.path, item.line)
    ):
        return None
    rule_levels = levels if levels is not None else dict(config.profile_rule_levels)
    level = rule_levels.get(item.code)
    if not level:
        return item
    evidence = dict(item.evidence)
    evidence["profile_rule_level"] = level
    if level in {"info", "warning", "error", "critical"}:
        return replace(item, severity=level, evidence=evidence)
    if level == "semantic":
        evidence["semantic_review_required"] = True
        evidence.setdefault("semantic_review_question", "Q1,Q2,Q6,Q11")
        evidence.setdefault("semantic_review_kind", "profile-semantic")
        return replace(item, severity="info", evidence=evidence)
    if level == "blocker":
        evidence["absolute_blocker"] = True
        return replace(item, severity="critical", evidence=evidence)
    raise ValueError(f"unsupported profile rule level: {level!r}")


def _apply_profile_rule_policy(report: ScanReport, config: GuardConfig) -> None:
    """Apply declarative disable/level policy before baseline and static gating."""
    matcher = (
        ScopedRuleMatcher(report.root, config.profile_scoped_rule_disables)
        if config.profile_scoped_rule_disables
        else None
    )
    disabled = frozenset(config.disabled_rules)
    levels = dict(config.profile_rule_levels)
    report.findings = [
        mapped
        for item in report.findings
        if (mapped := _apply_rule_level(item, config, matcher, disabled, levels))
        is not None
    ]
    if report.interface_diff is not None:
        contract_findings = tuple(
            mapped
            for item in report.interface_diff.contract_findings
            if (mapped := _apply_rule_level(item, config, matcher, disabled, levels))
            is not None
        )
        report.interface_diff = replace(
            report.interface_diff,
            contract_findings=contract_findings,
        )
    report.findings.sort(
        key=lambda item: (
            -SEVERITY_RANK[item.severity],
            item.path,
            item.line,
            item.code,
        )
    )


def _apply_report_extensions(
    report: ScanReport, profile: ProjectProfile | None
) -> None:
    """Collect stable Profile metadata without allowing extensions to control gate status."""
    if profile is None or not profile.report_extensions:
        return
    merged: dict[str, str] = {}
    for extension_cls in profile.report_extensions:
        extension = extension_cls()
        for key, value in extension.metadata().items():
            normalized = str(key).strip()
            if not normalized:
                raise ValueError("ReportExtension metadata keys must be non-empty")
            if normalized in merged:
                raise ValueError(
                    f"duplicate ReportExtension metadata key: {normalized}"
                )
            merged[normalized] = str(value)
    report.profile_metadata.update(merged)


def _profile_custom_findings(
    profile: ProjectProfile | None, ctx: RuleContext
) -> list[Finding]:
    """Execute Profile custom rules against the Core-owned read-only analysis context."""
    if profile is None or not profile.custom_rules:
        return []
    findings: list[Finding] = []
    disabled = frozenset(ctx.config.disabled_rules)
    scoped = {item.code for item in ctx.config.profile_scoped_rule_disables}
    for rule_cls in profile.custom_rules:
        rule = rule_cls()
        code = profile.rule_codes.get(rule.key)
        if not code:
            raise ValueError(f"custom rule {rule.key!r} has no frozen QG code")
        if code in disabled or code in scoped:
            if not rule.suppressible:
                raise ValueError(
                    f"custom rule {rule.key!r} ({code}) is not suppressible"
                )
            if code in disabled:
                continue
        for item in rule.evaluate(ctx):
            if not isinstance(item, Finding):
                raise TypeError(f"custom rule {rule.key!r} must yield Finding objects")
            findings.append(
                replace(
                    item,
                    code=code,
                    severity=rule.severity,
                    source=f"profile:{profile.name}",
                )
            )
    return findings


def _quality_baseline(report: ScanReport, revision: str) -> QualityBaseline:
    """把一次基线扫描压缩为可比较的质量摘要。

    Args:
        report: 已完成聚焦处理的基线扫描报告。
        revision: Git 基线 revision。

    Returns:
        严重级别、规则和文件维度的基线摘要。
    """
    summary = {"critical": 0, "error": 0, "warning": 0, "info": 0}
    by_rule: dict[str, dict[str, int]] = {}
    by_file: dict[str, dict[str, int]] = {}
    for finding in report.findings:
        summary[finding.severity] += 1
        if finding.code not in by_rule:
            by_rule[finding.code] = {
                "critical": 0,
                "error": 0,
                "warning": 0,
                "info": 0,
            }
        by_rule[finding.code][finding.severity] += 1
        if finding.path not in by_file:
            by_file[finding.path] = {
                "critical": 0,
                "error": 0,
                "warning": 0,
                "info": 0,
            }
        by_file[finding.path][finding.severity] += 1
    return QualityBaseline(
        revision=revision,
        files_scanned=report.files_scanned,
        definitions=report.definitions,
        documented_definitions=report.documented_definitions,
        complete_docstrings=report.complete_docstrings,
        docstring_definitions=report.docstring_definitions,
        summary=summary,
        by_rule=by_rule,
        by_file=by_file,
        finding_severities={
            finding.fingerprint: finding.severity
            for finding in report.findings
            if finding.severity in {"critical", "error", "warning"}
            and not finding.code.startswith("QG98")
        },
        semantic_review_fingerprints=dict(
            Counter(
                semantic_review_key(finding)
                for finding in annotate_semantic_review(list(report.findings))
                if finding.evidence.get("semantic_review_required") is True
            )
        ),
    )


def _baseline_target(target: AuditTarget, baseline_root: Path) -> AuditTarget:
    """把当前聚焦范围映射到临时 Git 基线目录。

    Args:
        target: 当前工作区审计目标。
        baseline_root: 解压后的 Git 基线根目录。

    Returns:
        保持相同相对聚焦范围的基线审计目标。
    """
    focus_files = frozenset(
        baseline_root / path.relative_to(target.root) for path in target.focus_files
    )
    focus_roots = tuple(
        baseline_root / path.relative_to(target.root) for path in target.focus_roots
    )
    return AuditTarget(
        root=baseline_root,
        focus_files=focus_files,
        focus_roots=focus_roots,
        notes=(f"静态质量基线来自 Git `{target.root}`。",),
        git_enabled=True,
    )


_BASELINE_CACHE_SCHEMA = "repository-quality-guard/baseline-cache-v3"


def baseline_cache_path(
    target: AuditTarget, config: GuardConfig, revision: str
) -> Path | None:
    """Key immutable baseline cache entries by revision, policy and scan scope.

    A resolved Git commit, release seal, target root, effective GuardConfig and
    focused paths all contribute to the cache identity; unknown refs bypass it.

    Args:
        target: Repository and optional focus paths for the baseline scan.
        config: Effective policy whose findings are preserved in the cache.
        revision: Git revision to resolve to an immutable commit object.

    Returns:
        Content-addressed baseline cache path, or None when Git cannot
        resolve revision to a commit.
    """
    resolved_revision = git_stdout(
        target.root, ("rev-parse", "--verify", f"{revision}^{{commit}}")
    )
    if not resolved_revision:
        return None
    focus_files = sorted(
        path.relative_to(target.root).as_posix() for path in target.focus_files
    )
    focus_roots = sorted(
        path.relative_to(target.root).as_posix() for path in target.focus_roots
    )
    payload = json.dumps(
        {
            "schema": _BASELINE_CACHE_SCHEMA,
            "root": str(target.root.resolve()),
            "revision": resolved_revision,
            "release_seal": os.environ["REPO_QUALITY_GUARD_RELEASE_SEAL"],
            "config": repr(config),
            "focus_files": focus_files,
            "focus_roots": focus_roots,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    key = hashlib.sha256(payload).hexdigest()
    return _cache_root() / "baseline-v3" / f"{key}.json"


def _baseline_payload(item: QualityBaseline) -> dict[str, object]:
    """把质量基线转换为稳定缓存结构。"""
    return {
        "revision": item.revision,
        "files_scanned": item.files_scanned,
        "definitions": item.definitions,
        "documented_definitions": item.documented_definitions,
        "complete_docstrings": item.complete_docstrings,
        "docstring_definitions": item.docstring_definitions,
        "summary": item.summary,
        "by_rule": item.by_rule,
        "by_file": item.by_file,
        "finding_severities": item.finding_severities,
        "semantic_review_fingerprints": item.semantic_review_fingerprints,
    }


def _baseline_from_payload(payload: dict[str, object]) -> QualityBaseline:
    """从已验证 schema 的缓存结构恢复质量基线。"""
    return QualityBaseline(
        revision=str(payload["revision"]),
        files_scanned=int(payload["files_scanned"]),
        definitions=int(payload["definitions"]),
        documented_definitions=int(payload["documented_definitions"]),
        complete_docstrings=int(payload["complete_docstrings"]),
        docstring_definitions=int(payload["docstring_definitions"]),
        summary=dict(payload["summary"]),
        by_rule=dict(payload["by_rule"]),
        by_file=dict(payload["by_file"]),
        finding_severities=dict(payload["finding_severities"]),
        semantic_review_fingerprints={
            str(key): int(value)
            for key, value in dict(payload["semantic_review_fingerprints"]).items()
        },
    )


def load_baseline_cache(
    path: Path | None,
) -> tuple[QualityBaseline, QualityBaseline] | None:
    """Read a previously frozen pair of production and test baselines.

    The cache stores two independently classified QualityBaseline records;
    absent files or an older payload schema are cache misses, not findings.

    Args:
        path: Cache file selected from the revision/policy fingerprint, or
            None for an uncacheable revision.

    Returns:
        Production and test baselines on a valid cache hit; otherwise None.
    """
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["schema"] != _BASELINE_CACHE_SCHEMA:
        return None
    return (
        _baseline_from_payload(payload["production"]),
        _baseline_from_payload(payload["test"]),
    )


def _write_baseline_cache(
    path: Path | None, production: QualityBaseline, test: QualityBaseline
) -> None:
    """原子写入不可变 Git 基线缓存，不改变任何审计结果。"""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": _BASELINE_CACHE_SCHEMA,
        "production": _baseline_payload(production),
        "test": _baseline_payload(test),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _compute_git_baseline(
    target: AuditTarget,
    config: GuardConfig,
    revision: str,
    profile: ProjectProfile | None = None,
    revision_snapshot: RepositoryAnalysisSnapshot | None = None,
) -> tuple[QualityBaseline, QualityBaseline]:
    """重新计算指定 Git revision 的生产代码与测试代码质量基线。

    Args:
        target: 当前工作区审计目标。
        config: 已启用项目 profile 的固定规则配置。
        revision: 待扫描的 Git revision。

    Returns:
        生产代码基线与测试代码独立基线。

    Raises:
        RuntimeError: Git 快照生成失败。
    """
    archive = subprocess.run(
        ["git", "archive", "--format=tar", revision],
        cwd=target.root,
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        detail = archive.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"无法生成 Git 基线 `{revision}`: {detail}")
    with tempfile.TemporaryDirectory(prefix="quality-guard-baseline-") as temp_dir:
        baseline_root = Path(temp_dir).resolve()
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
            for member in tar.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise RuntimeError(f"Git 基线快照包含不安全路径: {member.name}")
            tar.extractall(baseline_root, filter="data")
        baseline_target = _baseline_target(target, baseline_root)
        production_config = config.with_overrides(include_tests=False)
        test_config = config.with_overrides(include_tests=True)
        # The same Git revision was already parsed for interface and relation
        # comparison in scan_target. Rebind only the physical repository root:
        # the immutable source/AST units belong to the identical Git archive.
        # Direct baseline callers still parse the extracted directory normally.
        if revision_snapshot is not None:
            if revision_snapshot.label != revision:
                raise RuntimeError("Git baseline snapshot revision mismatch")
            baseline_analysis = replace(revision_snapshot, root=baseline_root)
        else:
            baseline_analysis = directory_analysis_snapshot(baseline_root, config)
        baseline_topology = normalized_topology(
            RepositoryRelationGraph(baseline_analysis),
            StaticPythonDependencyResolver.from_environment(),
        )
        shared_snapshot_findings = snapshot_only_rule_findings(baseline_analysis)
        production_scanner = RepositoryScanner(
            baseline_root,
            production_config,
            baseline_analysis,
            baseline_topology,
            shared_snapshot_findings,
        )
        production_report = production_scanner.scan()
        baseline_architecture_findings = single_use_chain_findings(
            baseline_root,
            production_config,
            baseline_topology,
        )
        merge_findings(
            production_report,
            (
                item
                for item in baseline_architecture_findings
                if not is_test_path(item.path, config.project_name)
            ),
        )
        if production_config.project_name:
            sources: dict[str, str] = {}
            for path in production_scanner.discover_python_files():
                relative = path.relative_to(baseline_root).as_posix()
                unit = baseline_analysis.unit(relative)
                if unit is not None:
                    sources[relative] = unit.source
            symbols = {}
            import_aliases = []
            for path, source in sorted(sources.items()):
                unit = baseline_analysis.unit(path)
                extractor = PythonInterfaceExtractor(
                    path,
                    source,
                    tree=unit.tree if unit is not None else None,
                )
                file_symbols, _errors = extractor.extract()
                symbols.update(file_symbols)
                import_aliases.extend(extractor.import_aliases)
            symbols = resolve_import_aliases(symbols, import_aliases, tuple(sources))
            symbols = filter_interface_symbols(
                symbols,
                sources,
                import_aliases,
                forced_symbols=set(production_config.forced_interface_symbols),
                include_private=True,
                private_min_lines=production_config.api_private_min_lines,
            )
            merge_findings(
                production_report,
                validate_project_contracts(
                    profile,
                    sources,
                    sources,
                    symbols,
                    base_trees=baseline_analysis.trees(),
                    target_trees=baseline_analysis.trees(),
                ),
            )
        test_scanner = RepositoryScanner(
            baseline_root,
            test_config,
            baseline_analysis,
            shared_snapshot_findings=shared_snapshot_findings,
        )
        test_files = {
            path
            for path in test_scanner.discover_python_files()
            if is_test_path(path.relative_to(baseline_root), config.project_name)
        }
        test_report = test_scanner.scan(selected_files=test_files)
        test_report.findings = _filter_test_quality_findings(
            test_report.findings,
            config.profile_test_baseline_passthrough_paths,
        )
        merge_findings(
            test_report,
            (
                item
                for item in baseline_architecture_findings
                if is_test_path(item.path, config.project_name)
            ),
        )
        ruff_runner = RuffRunner(
            baseline_root,
            cache_root=_cache_root(),
            environment=os.environ,
        )
        ruff_findings = ruff_runner.run(check_format=True)
        merge_findings(
            production_report,
            (
                item
                for item in ruff_findings
                if not is_test_path(item.path, config.project_name)
            ),
        )
        merge_findings(
            test_report,
            (
                item
                for item in ruff_findings
                if is_test_path(item.path, config.project_name)
            ),
        )
        language_findings, language_summary = multilang_findings(
            baseline_analysis, production_config
        )
        merge_findings(production_report, language_findings)
        production_report.multilang_summary = language_summary
        identity_findings = release_identity_findings(baseline_root, production_config)
        # Release/version identity is always a semantic-review concern. Keep test
        # candidates in the main report so QG203/QG205 cannot become a hidden
        # test-only static rejection path.
        merge_findings(production_report, identity_findings)
        custom_findings = _profile_custom_findings(
            profile,
            RuleContext(
                root=baseline_root,
                config=production_config,
                analysis=baseline_analysis,
            ),
        )
        merge_findings(
            production_report,
            (
                item
                for item in custom_findings
                if not is_test_path(item.path, config.project_name)
            ),
        )
        merge_findings(
            test_report,
            (
                item
                for item in custom_findings
                if is_test_path(item.path, config.project_name)
            ),
        )
        _apply_profile_rule_policy(production_report, production_config)
        _apply_profile_rule_policy(test_report, test_config)
        _apply_report_extensions(production_report, profile)
        _apply_report_extensions(test_report, profile)
        _apply_focus(production_report, baseline_target)
        _apply_focus(test_report, baseline_target)
        return (
            _quality_baseline(production_report, revision),
            _quality_baseline(test_report, revision),
        )


def scan_git_baseline(
    target: AuditTarget,
    config: GuardConfig,
    revision: str,
    profile: ProjectProfile | None = None,
    revision_snapshot: RepositoryAnalysisSnapshot | None = None,
) -> tuple[QualityBaseline, QualityBaseline]:
    """Produce both baseline ledgers for one immutable Git revision.

    Preserve the production/test distinction and reuse a compatible cache;
    on a miss, scan the frozen source and atomically write the resulting pair.

    Args:
        target: Repository whose revision is being compared to the worktree.
        config: Effective scan policy for the frozen revision.
        revision: Commit-ish to resolve and label both baseline ledgers.
        profile: Optional resolved project Profile for the baseline scan.
        revision_snapshot: Optional prebuilt immutable AST/source snapshot.

    Returns:
        A tuple of production and test QualityBaseline records.
    """
    cache_path = baseline_cache_path(target, config, revision)
    cached = load_baseline_cache(cache_path)
    if cached is not None:
        return cached
    production, test = _compute_git_baseline(
        target, config, revision, profile, revision_snapshot
    )
    _write_baseline_cache(cache_path, production, test)
    return production, test


def _annotate_interface_changes(
    interface_diff: InterfaceDiffReport,
) -> InterfaceDiffReport:
    """标注 property 新增/形态变化和 private→public 权限提升。"""
    removed_lookup = {
        (change.path, change.kind, change.symbol): change
        for change in interface_diff.changes
        if change.change == "removed"
    }
    annotated: list[InterfaceChange] = []
    property_decorators = {"property", "cached_property", "setter", "deleter"}
    for original in interface_diff.changes:
        details = dict(original.details)
        policy = dict(details.get("interface_policy", {}))
        symbol = original.after or original.before
        decorators = () if symbol is None else symbol.decorators
        leaves = {item.rsplit(".", 1)[-1] for item in decorators}
        if original.change == "added" and original.kind in _INTERFACE_DEFINITION_KINDS:
            if leaves & property_decorators:
                policy["property_added"] = True
            owner, separator, leaf = original.symbol.rpartition(".")
            private_symbol = f"{owner}._{leaf}" if separator else f"_{leaf}"
            promoted = removed_lookup.get(
                (original.path, original.kind, private_symbol)
            )
            if promoted is not None and not leaf.startswith("_"):
                policy["visibility_promotion_from"] = promoted.symbol
        decorator_change = details.get("decorators")
        if (
            original.change == "modified"
            and original.kind in {"function", "method"}
            and isinstance(decorator_change, dict)
        ):
            before = {
                item.rsplit(".", 1)[-1] for item in decorator_change.get("before", [])
            }
            after = {
                item.rsplit(".", 1)[-1] for item in decorator_change.get("after", [])
            }
            if (before | after) & property_decorators:
                policy["property_decorator_changed"] = True
        if policy:
            details["interface_policy"] = policy
            annotated.append(replace(original, details=details))
        else:
            annotated.append(original)
    return replace(interface_diff, changes=tuple(annotated))


def _run_interface_diff(
    target: AuditTarget,
    config: GuardConfig,
    args: argparse.Namespace,
    base_analysis: RepositoryAnalysisSnapshot | None = None,
    target_analysis: RepositoryAnalysisSnapshot | None = None,
    profile: ProjectProfile | None = None,
) -> InterfaceDiffReport:
    """执行不可关闭的 Git 接口差异审查。

    Args:
        target: 当前审计目标。
        config: 已合并项目档案的质量配置。
        args: 已解析命令行参数。
        base_analysis: 可选的 Git before 统一源码/AST 快照。
        target_analysis: 可选的 after 统一源码/AST 快照。
        profile: 已由 CLI 解析完成的项目 Profile；内部不得按名称二次解析。

    Returns:
        已附加 QG180/QG181 与项目协议发现的接口差异报告。
    """
    if not target.git_enabled:
        return InterfaceDiffReport(
            base=args.diff_base,
            target="unavailable:no-git",
            changes=(),
            base_files=0,
            target_files=0,
            api_scope="all",
            private_min_lines=config.api_private_min_lines,
        )
    interface_diff = GitWorktreeInterfaceComparator(
        target.root,
        config,
        base_revision=args.diff_base,
        include_private=True,
        target="staged" if args.staged else "worktree",
        base_analysis=base_analysis,
        target_analysis=target_analysis,
        profile=profile,
    ).compare()
    interface_diff = _annotate_interface_changes(interface_diff)
    production_changes = [
        change
        for change in interface_diff.changes
        if not is_test_path(change.path, config.project_name)
    ]
    added = [
        change
        for change in production_changes
        if change.change == "added" and change.kind in _INTERFACE_DEFINITION_KINDS
    ]
    removed = [
        change
        for change in production_changes
        if change.change == "removed" and change.kind in _INTERFACE_DEFINITION_KINDS
    ]
    interface_review_changes = [
        change
        for change in production_changes
        if change.change == "modified" and change.kind in _INTERFACE_DEFINITION_KINDS
    ]
    policy_findings = [
        finding
        for change in (*added, *interface_review_changes)
        for finding in InterfaceChangeReviewRule().evaluate(change)
    ]
    policy_findings.extend(
        InterfaceDefinitionGrowthRule().evaluate(
            InterfaceGrowthContext(tuple(added), tuple(removed))
        )
    )
    return replace(
        interface_diff,
        contract_findings=(*interface_diff.contract_findings, *policy_findings),
    )


def _profile_selection(root: Path, explicit: str | None):
    """解析 Profile 并把自动/显式/通用来源明确打印给用户。"""
    selection = resolve_profile_reference(root, explicit)
    if selection.source == "explicit-cli":
        print(f"[QG] Profile selected: '{selection.name}' (source=explicit-cli).")
    elif selection.source == "auto-directory-name":
        print(
            f"[QG] Auto-selected profile '{selection.name}' because repository directory "
            "name matches an available profile. (source=auto-directory-name)"
        )
    elif selection.source in {"sealed-installed", "sealed-installed-explicit"}:
        print(
            f"[QG] Using sealed installed profile '{selection.name}'. "
            f"(source={selection.source})"
        )
    elif selection.source == "sealed-installed-generic":
        print(
            "[QG] Using sealed installed generic policy. (source=sealed-installed-generic)"
        )
    elif selection.source == "generic":
        print("[QG] No profile selected; using generic policy. (source=generic)")
    else:
        raise ValueError(f"unknown Profile selection source: {selection.source}")
    return selection


def api_catalog_context(
    target: AuditTarget, profile_name: str | None
) -> tuple[GuardConfig, ProjectProfile | None]:
    """Resolve the production catalog policy and its optional Profile hooks.

    Args:
        target: Resolved repository and focus selection.
        profile_name: Explicit Profile reference, or automatic selection when absent.

    Returns:
        Production-only catalog configuration and the selected Profile instance.
    """
    selection = _profile_selection(target.root, profile_name)
    profile = get_project_profile(selection.reference)
    config = apply_project_profile(
        GuardConfig.load(target.root),
        selection.reference,
        selection_source=selection.source,
    ).with_overrides(include_tests=False)
    return config, profile


def api_catalog_config(target: AuditTarget, profile_name: str | None) -> GuardConfig:
    """Return the production configuration used for API catalog extraction.

    Args:
        target: Resolved repository and focus selection.
        profile_name: Explicit Profile reference, or automatic selection when absent.

    Returns:
        The configuration from the same selection used by catalog search hooks.
    """
    config, _profile = api_catalog_context(target, profile_name)
    return config


def _run_api_catalog_mode(args: argparse.Namespace) -> int | None:
    """执行开发前接口目录生成或 BM25 候选检索模式。"""
    if not args.build_api_docs and args.search_api is None:
        return None
    target = resolve_target(args)
    config, profile = api_catalog_context(target, args.profile)
    output = target.root / "docs" / "api-reference"
    if args.build_api_docs:
        metrics = build_api_catalog(target.root, output, config)
        print(
            "API 目录生成完成："
            f"symbols={metrics['symbols']} "
            f"extract={metrics['extract_seconds']:.3f}s "
            f"index={metrics['index_seconds']:.3f}s "
            f"write={metrics['write_seconds']:.3f}s "
            f"total={metrics['total_seconds']:.3f}s"
        )
        print(f"API 目录：{output / 'INDEX.md'}")
        return 0
    if args.api_limit <= 0:
        raise ValueError("--api-top-k 必须大于 0")
    hits, metrics = search_api_catalog(
        target.root,
        output,
        config,
        args.search_api,
        limit=args.api_limit,
    )
    hits = apply_search_strategy(
        profile, args.search_api, hits, target.root, args.api_limit
    )
    print(f"API BM25 查询：{args.search_api}")
    print(
        "性能："
        f"cache_hit={metrics['cache_hit']} "
        f"source_check={metrics['source_check_seconds']:.3f}s "
        f"index={metrics['index_seconds']:.3f}s "
        f"search={metrics['search_seconds'] * 1000:.3f}ms "
        f"total={metrics['total_seconds']:.3f}s"
    )
    if not hits:
        print("未召回候选；必须继续直接检索源码，不能据此认定能力不存在。")
        return 0
    for rank, hit in enumerate(hits, start=1):
        summary = hit.record.docstring.splitlines()[0] if hit.record.docstring else ""
        print(
            f"{rank:>2}. {hit.record.symbol} "
            f"score={hit.score:.3f} {hit.record.path}:{hit.record.line} "
            f"[{hit.record.visibility}] {summary}"
        )
    print("提示：候选排名不是语义裁决；新增接口前必须阅读 Top10 及相关调用方源码。")
    return 0


def integrity_gate() -> int | None:
    """
    执行不可降级的 Skill 发布内容完整性门禁。

    Returns:
        完整性通过时返回 None；失败时返回固定完整性门禁退出码。
    """
    result = verify_release_integrity()
    if result.passed:
        print(f"Skill 完整性门禁：PASS（{result.protected_files} 个受保护文件）")
        return None
    print(f"Skill 完整性门禁：REJECT（{INTEGRITY_RULE_CODE}）", file=sys.stderr)
    for issue in result.issues:
        print(f"- {issue}", file=sys.stderr)
    return INTEGRITY_GATE_EXIT_CODE


def _append_docstring_regression_finding(report: ScanReport) -> None:
    """Delegate QG169 baseline evaluation to its sole rule owner."""
    findings = DocstringRegressionRule().evaluate(report)
    if findings:
        report.findings.extend(findings)
        report.findings.sort(
            key=lambda item: (
                -SEVERITY_RANK[item.severity],
                item.path,
                item.line,
                item.code,
            )
        )


def _append_quality_count_regression_finding(report: ScanReport) -> None:
    """Delegate QG179 baseline evaluation to its sole rule owner."""
    findings = QualityFingerprintRegressionRule().evaluate(
        QualityRegressionContext(report, _INTERFACE_POLICY_CODES)
    )
    if findings:
        report.findings.extend(findings)
        report.findings.sort(
            key=lambda item: (
                -SEVERITY_RANK[item.severity],
                item.path,
                item.line,
                item.code,
            )
        )


def _apply_git_quality_baseline(
    target: AuditTarget,
    config: GuardConfig,
    revision: str,
    report: ScanReport,
    profile: ProjectProfile | None = None,
    revision_snapshot: RepositoryAnalysisSnapshot | None = None,
    baseline_task: GitBaselineTask | None = None,
) -> None:
    """扫描并应用生产/测试 Git 质量基线及不可豁免退化门禁。"""
    try:
        production_baseline, test_baseline = (
            baseline_task.result(revision)
            if baseline_task is not None
            else scan_git_baseline(target, config, revision, profile, revision_snapshot)
        )
    except RuntimeError as error:
        report.baseline_error = str(error)
        return
    report.baseline = production_baseline
    report.test_baseline = test_baseline
    _append_docstring_regression_finding(report)
    _append_quality_count_regression_finding(report)


def _scan_target_impl(
    args: argparse.Namespace,
    baseline_task: GitBaselineTask,
) -> tuple[AuditTarget, ScanReport, InterfaceDiffReport]:
    """
    执行目标解析、生产/测试分账扫描、Git 差分、Ruff 与基线计算。

    Args:
        args: 现有扫描内核兼容的命令行参数命名空间。

    Returns:
        解析后的审计目标、生产扫描报告与接口差分报告。
    """
    target = apply_audit_mode(
        resolve_target(args), args.mode, args.diff_base, args.staged
    )
    comparison = git_comparison_plan(target, args)
    scan_args = copy.copy(args)
    scan_args.diff_base = comparison.base_revision
    if scan_args.resolved_profile_source:
        selection = ProfileSelection(
            reference=scan_args.resolved_profile_reference or None,
            name=scan_args.resolved_profile_name,
            source=scan_args.resolved_profile_source,
            reason="profile selection inherited from the isolated scan parent",
        )
    else:
        selection = _profile_selection(target.root, scan_args.profile)
    scan_args.profile = selection.reference
    profile = get_project_profile(scan_args.profile)
    if target.git_enabled and scan_args.staged:
        staged_config = subprocess.run(
            ["git", "show", ":pyproject.toml"],
            cwd=target.root,
            capture_output=True,
            check=False,
        )
        if staged_config.returncode == 0:
            with tempfile.TemporaryDirectory(prefix="rqg-staged-config-") as config_dir:
                (Path(config_dir) / "pyproject.toml").write_bytes(staged_config.stdout)
                source_config = GuardConfig.load(Path(config_dir))
        else:
            indexed_files = subprocess.run(
                ["git", "ls-files", "--cached", "--", "pyproject.toml"],
                cwd=target.root,
                capture_output=True,
                check=False,
            )
            if indexed_files.returncode != 0 or indexed_files.stdout.strip():
                raise RuntimeError("无法读取已暂存的 pyproject.toml 配置")
            source_config = GuardConfig()
    else:
        source_config = GuardConfig.load(target.root)
    base_config = apply_project_profile(
        source_config,
        scan_args.profile,
        selection_source=selection.source,
    )
    production_config, test_config = (
        base_config.with_overrides(include_tests=False),
        base_config.with_overrides(include_tests=True),
    )
    current_analysis = (
        worktree_analysis_snapshot(target.root, base_config)
        if target.git_enabled
        else directory_analysis_snapshot(target.root, base_config)
    )
    base_analysis = (
        revision_analysis_snapshot(target.root, scan_args.diff_base, base_config)
        if target.git_enabled
        else None
    )
    if target.git_enabled and base_analysis is not None:
        baseline_task.start(
            target,
            base_config,
            scan_args.diff_base,
            scan_args.profile or "",
            base_analysis,
            cached=load_baseline_cache(
                baseline_cache_path(target, base_config, scan_args.diff_base)
            ),
        )
    comparison_analysis = (
        index_analysis_snapshot(target.root, base_config)
        if target.git_enabled and scan_args.staged
        else current_analysis
    )
    # The staged target is the INDEX, including its test source.  A working
    # tree scan here would silently approve an invalid staged test when the
    # corresponding file has already been fixed but not re-staged.
    active_analysis = comparison_analysis
    interface_diff = _run_interface_diff(
        target,
        base_config,
        scan_args,
        base_analysis=base_analysis,
        target_analysis=comparison_analysis,
        profile=profile,
    )

    current_relation_graph = RepositoryRelationGraph(active_analysis)
    production_topology = normalized_topology(
        current_relation_graph, StaticPythonDependencyResolver.from_environment()
    )
    compiler_entry = os.environ.get("RQG_PYRIGHT_ENTRY")
    checker = None
    provider_identity: dict[str, str] = {}
    if compiler_entry and not scan_args.staged:
        from shutil import which
        from tools.spike_core.provider_audit import collect
        from .compiler_contracts import r2_initialization_observations

        checker = collect(target.root, Path(compiler_entry))
        pyrefly_executable = which("pyrefly")
        if pyrefly_executable is None:
            raise RuntimeError("Compiler provider was requested but pyrefly is missing")
        provider_identity = {
            "pyright_version": checker["version"],
            "pyright_sha256": checker["provider_entry_sha256"],
            "typeshed_sha256": checker["stdlib_builtins_sha256"],
            "pyrefly_sha256": hashlib.sha256(
                Path(pyrefly_executable).read_bytes()
            ).hexdigest(),
        }
        production_topology = replace(
            production_topology,
            compiler_diagnostics=r2_initialization_observations(
                active_analysis,
                checker["observations"],
                production_topology,
                provider_identity=provider_identity,
            ),
        )
    shared_snapshot_findings = snapshot_only_rule_findings(active_analysis)
    production_scanner = RepositoryScanner(
        target.root,
        production_config,
        active_analysis,
        production_topology,
        shared_snapshot_findings,
    )
    selected_files = (
        set(target.focus_files)
        if target.focus_files and not target.git_enabled
        else None
    )
    report = production_scanner.scan(selected_files=selected_files)
    # Compiler evidence is optional until the offline toolchain is certified for
    # every supported deployment. Explicit activation must fail visibly on an
    # incomplete toolchain; never silently downgrade to name-based inference.
    if checker is not None:
        from tools.spike_core.python_compiler_provider import (
            certified_static_call_mismatches,
        )
        from .compiler_contracts import r1_bound_call_findings

        witnesses = certified_static_call_mismatches(
            current_analysis, checker["observations"]
        )
        merge_findings(
            report,
            r1_bound_call_findings(
                active_analysis, witnesses, provider_identity=provider_identity
            ),
        )
    report.interface_diff = interface_diff
    report.comparison_mode = f"{args.mode}: {comparison.mode}"
    report.comparison_target = comparison.target_label

    base_relation_graph = (
        RepositoryRelationGraph(base_analysis) if base_analysis is not None else None
    )
    comparison_relation_graph = (
        current_relation_graph
        if comparison_analysis is current_analysis
        else RepositoryRelationGraph(comparison_analysis)
    )
    base_relation_graph, relation_graph, relation_summary = (
        build_relation_graph_summary(
            base_analysis if target.git_enabled else None,
            comparison_analysis,
            interface_diff,
            base_graph=base_relation_graph,
            current_graph=comparison_relation_graph,
        )
    )
    report.relation_graph = relation_summary

    merge_findings(
        report,
        (
            finding
            for finding in interface_diff.contract_findings
            if not is_test_path(finding.path, base_config.project_name)
        ),
    )

    test_scanner = RepositoryScanner(
        target.root,
        test_config,
        active_analysis,
        production_topology,
        shared_snapshot_findings,
    )
    test_files = {
        path
        for path in test_scanner.discover_python_files()
        if is_test_path(path.relative_to(target.root), base_config.project_name)
    }
    if selected_files is not None:
        test_files &= selected_files
    test_report = test_scanner.scan(selected_files=test_files)
    test_report.findings = _filter_test_quality_findings(
        test_report.findings,
        base_config.profile_test_baseline_passthrough_paths,
    )

    if target.git_enabled:
        architecture_findings = []
        if not scan_args.staged:
            architecture_findings.extend(
                compare_git_architecture(
                    target.root,
                    base_config,
                    scan_args.diff_base,
                    base_analysis=base_analysis,
                    target_analysis=current_analysis,
                )
            )
            architecture_findings.extend(
                single_use_chain_findings(
                    target.root,
                    production_config,
                    production_topology,
                )
            )
        architecture_findings.extend(
            fallback_laundering_findings(
                target.root,
                production_config,
                scan_args.diff_base,
                staged=scan_args.staged,
                base_analysis=base_analysis,
                target_analysis=comparison_analysis,
            )
        )
        if "semantic-heuristic-candidates" in production_config.profile_capabilities:
            architecture_findings.extend(
                semantic_heuristic_candidate_findings(
                    target.root,
                    production_config,
                    scan_args.diff_base,
                    staged=scan_args.staged,
                    static_exemptions=(
                        profile.semantic_heuristic_exemptions
                        if profile is not None
                        else ()
                    ),
                    target_analysis=comparison_analysis,
                )
            )
        merge_findings(
            report,
            (
                item
                for item in architecture_findings
                if not is_test_path(item.path, base_config.project_name)
            ),
        )
        merge_findings(
            test_report,
            (
                item
                for item in architecture_findings
                if is_test_path(item.path, base_config.project_name)
            ),
        )
    report.findings, report.relation_graph = apply_relation_finding_context(
        list(report.findings),
        relation_graph,
        base_relation_graph,
        report.relation_graph,
        interface_diff,
    )

    # Ruff must analyze the same Git INDEX revision as the AST scanner.  A
    # working-tree Ruff invocation here can silently approve an invalid
    # staged file or reject an unstaged edit that is not part of the change.
    if scan_args.staged:
        with tempfile.TemporaryDirectory(prefix="rqg-ruff-index-") as index_dir:
            checkout = subprocess.run(
                ["git", "checkout-index", "--all", f"--prefix={index_dir}/"],
                cwd=target.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="surrogateescape",
                check=False,
            )
            if checkout.returncode != 0:
                raise RuntimeError(
                    "Git INDEX Ruff 快照构建失败: "
                    + (checkout.stderr.strip() or f"exit={checkout.returncode}")
                )
            index_root = Path(index_dir)
            git_dir = subprocess.run(
                ["git", "rev-parse", "--absolute-git-dir"],
                cwd=target.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="surrogateescape",
                check=False,
            )
            if git_dir.returncode != 0:
                raise RuntimeError(
                    "无法解析 staged 快照的 Git metadata: " + git_dir.stderr.strip()
                )
            (index_root / ".git").write_text(
                f"gitdir: {git_dir.stdout.strip()}\n", encoding="utf-8"
            )
            ruff_findings = RuffRunner(
                index_root, cache_root=_cache_root(), environment=os.environ
            ).run(check_format=True)
            identity_findings = release_identity_findings(index_root, production_config)
            custom_findings = _profile_custom_findings(
                profile,
                RuleContext(
                    root=index_root,
                    config=production_config,
                    analysis=comparison_analysis,
                    base_analysis=base_analysis,
                    interface_diff=interface_diff,
                    diff_base=scan_args.diff_base,
                    staged=True,
                ),
            )
    else:
        ruff_findings = RuffRunner(
            target.root, cache_root=_cache_root(), environment=os.environ
        ).run(check_format=True)
        identity_findings = release_identity_findings(target.root, production_config)
    merge_findings(
        report,
        (
            item
            for item in ruff_findings
            if not is_test_path(item.path, base_config.project_name)
        ),
    )
    merge_findings(
        test_report,
        (
            item
            for item in ruff_findings
            if is_test_path(item.path, base_config.project_name)
        ),
    )
    language_findings, language_summary = multilang_findings(
        comparison_analysis,
        production_config,
        base_analysis if target.git_enabled else None,
    )
    merge_findings(report, language_findings)
    report.multilang_summary = language_summary
    # QG203/QG205 are semantic candidates regardless of whether the evidence
    # lives in production code, tests, fixtures or configuration.
    merge_findings(report, identity_findings)
    if not scan_args.staged:
        custom_findings = _profile_custom_findings(
            profile,
            RuleContext(
                root=target.root,
                config=production_config,
                analysis=comparison_analysis,
                base_analysis=base_analysis,
                interface_diff=interface_diff,
                diff_base=scan_args.diff_base,
                staged=scan_args.staged,
            ),
        )
    merge_findings(
        report,
        (
            item
            for item in custom_findings
            if not is_test_path(item.path, base_config.project_name)
        ),
    )
    merge_findings(
        test_report,
        (
            item
            for item in custom_findings
            if is_test_path(item.path, base_config.project_name)
        ),
    )
    _apply_profile_rule_policy(report, production_config)
    _apply_profile_rule_policy(test_report, test_config)
    _apply_report_extensions(report, profile)
    _apply_report_extensions(test_report, profile)
    interface_diff = report.interface_diff or interface_diff
    report.findings = annotate_semantic_review(list(report.findings))
    _apply_focus(report, target)
    _apply_focus(test_report, target)
    report.test_findings, report.test_files_scanned, report.test_definitions = (
        list(test_report.findings),
        test_report.files_scanned,
        test_report.definitions,
    )

    if target.git_enabled:
        _apply_git_quality_baseline(
            target,
            base_config,
            scan_args.diff_base,
            report,
            profile,
            base_analysis,
            baseline_task=baseline_task,
        )
    return target, report, interface_diff


def scan_target(
    args: argparse.Namespace,
) -> tuple[AuditTarget, ScanReport, InterfaceDiffReport]:
    """Scan the requested repository while owning the baseline child lifetime.

    A context-managed GitBaselineTask overlaps frozen baseline analysis with
    the current scan, then cleans up the child even on scanner failure.

    Args:
        args: Parsed CLI scan arguments, including diff base and Profile.

    Returns:
        Resolved AuditTarget, completed ScanReport and interface delta report.
    """
    with GitBaselineTask() as baseline_task:
        return _scan_target_impl(args, baseline_task)


def _review_required_notice(
    report: ScanReport,
    interface_diff: InterfaceDiffReport,
) -> str:
    """渲染必须直接展示给用户的接口人工审核警告。"""
    lines = [
        "",
        "人工审核警告：当前版本存在生产接口或核心协议变动，不能自动批准提交。",
        "必须由用户逐项确认；接口变动较多时应视为改动范围过大，谨慎考虑拆分或重新设计。",
    ]
    for change in interface_diff.changes:
        if (
            is_test_path(change.path, report.project_name)
            or change.change == "removed"
            or change.kind == "file"
        ):
            continue
        details = "、".join(change.details) if change.details else "声明变化"
        lines.append(
            f"- [{change.change.upper()}][{change.kind}] {change.path}:{change.symbol}（{details}）"
        )
    for finding in interface_diff.contract_findings:
        if finding.code != "QG182" or is_test_path(finding.path, report.project_name):
            continue
        lines.append(
            f"- [PROTOCOL][{finding.code}] {finding.path}:{finding.symbol}（{finding.message}）"
        )
    return "\n".join(lines) + "\n"


def _final_gate_status(
    final_check: bool,
    report_contract_findings: list[Finding],
    tool_status: str,
    semantic_status: str,
) -> str:
    """合并报告、静态事实与模型结论，返回最终三态门禁。"""
    if (
        report_contract_findings
        or not final_check
        or "REJECT"
        in {
            tool_status,
            semantic_status,
        }
    ):
        return "REJECT"
    if "REVIEW_REQUIRED" in {tool_status, semantic_status}:
        return "REVIEW_REQUIRED"
    return "ACCEPT"


def _final_exit_code(
    final_status: str,
    report_contract_findings: list[Finding],
    code_exit: int,
) -> int:
    """把最终三态和报告契约结果映射为稳定退出码。"""
    if report_contract_findings:
        return REPORT_GATE_EXIT_CODE
    if final_status == "REJECT":
        return 1
    if final_status == "REVIEW_REQUIRED":
        return REVIEW_REQUIRED_EXIT_CODE
    return code_exit


def main(argv: list[str] | None = None) -> int:
    """
    执行完整性、代码和修改说明三道不可降级门禁。

    Args:
        argv: 可选命令行参数；为 None 时读取当前进程参数。

    Returns:
        稳定的质量门禁退出码。
    """
    args = build_parser().parse_args(argv)
    try:
        integrity_exit = integrity_gate()
        if integrity_exit is not None:
            return integrity_exit
        if args.verify_integrity:
            return 0
        api_exit = _run_api_catalog_mode(args)
        if api_exit is not None:
            return api_exit

        target, report, interface_diff = scan_target(args)
        revision = (
            report.baseline.revision
            if report.baseline is not None
            else (args.diff_base or "HEAD")
        )
        effective_interface_diff = report.interface_diff or interface_diff
        tool_status = code_status(report)
        code_exit = (
            2
            if effective_interface_diff.errors
            else 1
            if tool_status == "REJECT"
            else REVIEW_REQUIRED_EXIT_CODE
            if tool_status == "REVIEW_REQUIRED"
            else 0
        )
        modification_report = target.root / MODIFICATION_REPORT_NAME

        if args.final_check:
            if not modification_report.is_file():
                print(
                    f"修改说明门禁：REJECT（缺少 {modification_report}）",
                    file=sys.stderr,
                )
                print(f"静态事实门禁：{tool_status}")
                return REPORT_GATE_EXIT_CODE
            strict_report = modification_report.read_text(encoding="utf-8")
        else:
            existing_report = (
                modification_report.read_text(encoding="utf-8")
                if modification_report.is_file()
                else None
            )
            strict_report = render_strict_modification_report(
                report,
                existing=existing_report,
                revision=revision,
            )
            modification_report.write_text(strict_report, encoding="utf-8")

        report_contract_findings = validate_modification_report(
            strict_report,
            report,
            revision=revision,
        )
        sys.stdout.write(render_audit_markdown(report))
        if report_contract_findings:
            sys.stdout.write("\n## 修改说明门禁问题（不计入代码三档统计）\n\n")
            for finding in report_contract_findings:
                sys.stdout.write(f"- [{finding.code}] {finding.message}\n")
        report_status = "PASS" if not report_contract_findings else "REJECT"
        semantic_status = (
            report_final_status(strict_report)
            if not report_contract_findings
            else "INVALID"
        )
        mode = "最终检查" if args.final_check else "生成后检查"
        sys.stdout.write(
            f"\n修改说明：{modification_report}\n"
            f"修改说明门禁（{mode}）：{report_status}\n"
            f"静态事实门禁：{tool_status}\n"
            f"模型语义结论：{semantic_status}\n"
        )
        if tool_status == "REVIEW_REQUIRED":
            sys.stdout.write(_review_required_notice(report, effective_interface_diff))
        final_status = _final_gate_status(
            final_check=args.final_check,
            report_contract_findings=report_contract_findings,
            tool_status=tool_status,
            semantic_status=semantic_status,
        )
        sys.stdout.write(f"最终门禁：{final_status}\n")
        return _final_exit_code(
            final_status,
            report_contract_findings=report_contract_findings,
            code_exit=code_exit,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"quality-guard 执行失败: {error}", file=sys.stderr)
        return 2
