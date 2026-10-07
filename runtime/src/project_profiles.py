from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import util as importlib_util
import json
import re
from types import MappingProxyType
from typing import Any

from .commit_policy import parse_commit_policy
from .config import GuardConfig
from .model import Finding


@dataclass(slots=True, frozen=True)
class StableMappingContract:
    """
    描述需要跟踪或冻结的返回映射键接口。

    ``keys`` 为空时，检查器只比较所选 Git 基线与当前工作区；键集合发生变化
    才报告一次。``keys`` 非空时，接口被视为冻结协议，任何快照都必须保持该集合。
    默认通用模式不会猜测任何业务返回字段。``relative_severity`` 仅用于
    基线差分模式；冻结契约仍按既有 Error 规则处理。
    """

    path: str
    qualname: str
    keys: tuple[str, ...] = ()
    channel: str = "return"
    relative_severity: str = "warning"
    relative_code: str = "QG146"
    review_kind: str = ""
    state_type: str = ""
    state_source_suffix: str = ""
    state_conventional_names: tuple[str, ...] = ()
    state_constructor_fields: tuple[str, ...] = ()
    state_constructor_excluded_keywords: tuple[str, ...] = ()
    state_writer_methods: tuple[str, ...] = ()
    state_key_writer_methods: tuple[str, ...] = ()

    @property
    def frozen(self) -> bool:
        """
        判断当前映射契约是否使用固定键集合。

        Returns:
            声明了固定键集合时返回 True，否则返回 False。
        """
        return bool(self.keys)


@dataclass(slots=True, frozen=True)
class CallableContract:
    """
    描述一个框架适配入口必须维持的最小调用契约。

    参数项依次保存参数名、参数种类和是否必须具有默认值。类型注解只校验
    返回类型中的稳定核心名称，避免 ``List`` 与 ``list`` 等等价拼写造成误报。
    """

    path: str
    qualname: str
    parameters: tuple[tuple[str, str, bool], ...]
    return_markers: tuple[str, ...] = ()
    is_async: bool | None = None
    decorators: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class FrozenSSEProtocolContract:
    """
    描述明确对外冻结的 SSE 事件协议。

    契约由事件方法与事件类型映射、统一事件外壳字段和结束事件内容字段组成。
    它只验证 Python 静态结构，不读取或解释 Prompt、Skills 等非代码文件。
    """

    path: str
    class_name: str
    event_methods: tuple[tuple[str, str], ...]
    envelope_method: str
    envelope_keys: tuple[str, ...]
    usage_attribute: str
    close_builder: str
    close_keys: tuple[str, ...]
    allowed_event_prefixes: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class RouteContract:
    """
    描述需要对比变化的 HTTP 路由边界。

    路由契约只做当前 Git 基线差分，不永久冻结某一份实现快照。
    """

    name: str
    paths: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class HeaderContract:
    """
    描述需要对比变化的 HTTP 请求/响应 Header 边界。

    Header 契约用于发现代理、鉴权、调度和流式响应边界漂移。
    """

    name: str
    paths: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class SSEProtocolContract:
    """
    描述需要按 Git 基线对比的 SSE 事件协议边界。

    该契约只报告事件结构变化，不把字段集合写死为永久冻结值。
    """

    path: str
    class_name: str
    envelope_method: str
    usage_attribute: str
    close_builder: str
    code: str = "QG148"
    severity: str = "warning"
    review_kind: str = ""


_RULE_CODE_RE = re.compile(r"^QG(?P<number>\d{3,5})$")
# Report/release integrity is part of the guard's trust boundary, not project policy.
UNSUPPRESSIBLE_RULE_PREFIXES = ("QG98", "QG99")
CUSTOM_RULE_CODE_MIN = 10000
CUSTOM_RULE_CODE_MAX = 99999
PROFILE_LOCK_SCHEMA = "repository-quality-guard/profile-lock-v1"
RULE_LEVELS = frozenset({"info", "warning", "error", "critical", "semantic", "blocker"})
_PROFILE_MANIFEST_DEFAULTS: Mapping[str, Any] = MappingProxyType(
    {
        "rules": MappingProxyType({"disable": (), "levels": MappingProxyType({})}),
        "capabilities": (),
        "nonblocking_paths": (),
        "test_baseline_passthrough_paths": (),
        "settings": MappingProxyType({}),
        "commit_policy": MappingProxyType({}),
    }
)


def validate_rule_code(code: str, custom: bool = False) -> str:
    """Validate and normalize one Repository Quality Guard rule code.

    Args:
        code: Candidate rule code such as ``QG149``.
        custom: Require the dedicated custom-Profile numeric range.

    Returns:
        Uppercase validated rule code.
    """
    normalized = str(code).strip().upper()
    match = _RULE_CODE_RE.fullmatch(normalized)
    if match is None:
        raise ValueError(f"invalid QG code: {code!r}")
    number = int(match.group("number"))
    if custom and not (CUSTOM_RULE_CODE_MIN <= number <= CUSTOM_RULE_CODE_MAX):
        raise ValueError(
            f"custom Profile rules must use QG{CUSTOM_RULE_CODE_MIN}+ codes: {normalized}"
        )
    return normalized


def _string_sequence(value: Any) -> tuple[str, ...]:
    """Normalize one JSON scalar-or-array setting to a stable string tuple."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _prepare_profile_manifest(
    manifest: Mapping[str, Any] | None,
    default_name: str = "",
    default_version: str = "1",
    default_agents_file: str = "",
) -> Mapping[str, Any]:
    """Complete optional JSON Profile fields once at the input boundary.

    Args:
        manifest: Parsed ``profile.json`` mapping, or None for a generic Profile.
        default_name: Class-level name used only by direct authoring construction.
        default_version: Class-level version used only by direct authoring construction.
        default_agents_file: Class-level AGENTS path used only by direct authoring construction.

    Returns:
        Read-only manifest whose optional policy fields are always present.

    Raises:
        ValueError: A structured field has an incompatible JSON type.
    """
    raw = dict(manifest or {})
    normalized: dict[str, Any] = {
        **_PROFILE_MANIFEST_DEFAULTS,
        "name": default_name,
        "version": default_version,
        "agents_file": default_agents_file,
        "entrypoint": None,
    }
    normalized.update(raw)

    rules = normalized["rules"]
    if not isinstance(rules, Mapping):
        raise ValueError("profile rules must be an object")
    normalized_rules = {"disable": (), "levels": {}}
    normalized_rules.update(rules)
    if not isinstance(normalized_rules["levels"], Mapping):
        raise ValueError("profile rules.levels must be an object")
    normalized["rules"] = MappingProxyType(normalized_rules)

    normalized["commit_policy"] = parse_commit_policy(normalized["commit_policy"])

    settings = normalized["settings"]
    if not isinstance(settings, Mapping):
        raise ValueError("profile settings must be an object")
    normalized["settings"] = MappingProxyType(dict(settings))
    return MappingProxyType(normalized)


def profile_lock_codes(source: str) -> Mapping[str, str]:
    """Read immutable custom-rule assignments from one Profile lock.

    Args:
        source: Profile directory containing ``PROFILE.lock``.

    Returns:
        Read-only rule-key to stable QG-code mapping; empty when no lock exists.
    """
    from pathlib import Path

    if not source:
        return MappingProxyType({})
    path = Path(source) / "PROFILE.lock"
    if not path.is_file():
        return MappingProxyType({})
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema = payload["schema"]
    if schema != PROFILE_LOCK_SCHEMA:
        raise ValueError(f"unsupported PROFILE.lock schema: {schema!r}")
    raw = payload["rule_codes"]
    if not isinstance(raw, dict):
        raise ValueError("PROFILE.lock rule_codes must be an object")
    result: dict[str, str] = {}
    for key, code in raw.items():
        result[str(key)] = validate_rule_code(str(code), custom=True)
    return MappingProxyType(result)


@dataclass(slots=True, frozen=True)
class RuleContext:
    """Read-only repository context exposed to custom Profile rules.

    The context owns no mutation path and exposes only the normalized Guard
    configuration plus immutable analysis snapshots needed by rule evaluation.
    """

    root: Any
    config: GuardConfig
    analysis: Any
    base_analysis: Any = None
    interface_diff: Any = None
    diff_base: str | None = None
    staged: bool = False

    def source(self, path: str) -> str:
        """Return source text from the unified analysis snapshot.

        Args:
            path: Repository-relative source path.

        Returns:
            Source text for the requested analysis unit.

        Raises:
            KeyError: The path is not present in the current analysis snapshot.
        """
        unit = self.analysis.unit(path) if self.analysis is not None else None
        if unit is None:
            raise KeyError(path)
        return unit.source

    def python_ast(self, path: str) -> Any:
        """Return the parsed Python AST for one analysis unit.

        Args:
            path: Repository-relative Python source path.

        Returns:
            Parsed AST when available, otherwise None for absent or non-Python units.
        """
        unit = self.analysis.unit(path) if self.analysis is not None else None
        return unit.tree if unit is not None else None

    @property
    def settings(self) -> Mapping[str, Any]:
        """返回 Profile settings 的只读映射。"""
        return MappingProxyType(dict(self.config.profile_settings))


class QualityRule(ABC):
    """Base contract for custom quality rules supplied by a Profile.

    Subclasses declare stable metadata and implement ``evaluate`` against the
    read-only ``RuleContext`` without mutating repository or Guard state.
    """

    key: str = ""
    code: str | None = None
    title: str = ""
    description: str = ""
    severity: str = "warning"
    suppressible: bool = True
    scope: str = "repository"

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        """Evaluate one custom rule against repository facts.

        Args:
            ctx: Read-only repository and configuration context.

        Returns:
            Iterable of normalized ``Finding`` objects.
        """
        raise NotImplementedError


class SearchStrategy(ABC):
    """Optional Profile extension for deterministic API-search reranking.

    Strategies receive the Core BM25 result set and may reorder it without
    owning catalog construction, persistence, or command exit semantics.
    """

    def rerank(self, query: str, rows: Sequence[Any], root: Any) -> Sequence[Any]:
        """Return the search rows in the desired deterministic order.

        Args:
            query: User search query.
            rows: Core BM25 result rows.
            root: Repository root used by project-specific ranking evidence.

        Returns:
            Reordered result sequence; the default implementation preserves order.
        """
        return rows


class ReportExtension(ABC):
    """Optional Profile-owned report metadata extension.

    Extensions may add stable metadata only; they never control process exit
    status, release integrity, report schema, or verification authority.
    """

    def metadata(self) -> Mapping[str, str]:
        """Return stable metadata to append to an audit report.

        Returns:
            Read-only-compatible string mapping; empty by default.
        """
        return {}


@dataclass(slots=True, frozen=True)
class ProjectProfile:
    """Frozen runtime policy produced from one configured authoring Profile.

    The dataclass contains normalized immutable tuples/mappings consumed by the
    scanning Core, separating authoring mutation from runtime policy reads.
    """

    name: str
    version: str = ""
    source: str = ""
    tool_base_classes: tuple[str, ...] = ()
    tool_registration_methods: tuple[str, ...] = ()
    state_types: tuple[str, ...] = ()
    state_writer_names: tuple[str, ...] = ()
    history_markers: tuple[str, ...] = ()
    trace_markers: tuple[str, ...] = ()
    config_module_markers: tuple[str, ...] = ()
    boundary_module_markers: tuple[str, ...] = ()
    forced_interface_symbols: tuple[str, ...] = ()
    stable_mapping_contracts: tuple[StableMappingContract, ...] = ()
    callable_contracts: tuple[CallableContract, ...] = ()
    frozen_sse_contracts: tuple[FrozenSSEProtocolContract, ...] = ()
    sse_contracts: tuple[SSEProtocolContract, ...] = ()
    route_contracts: tuple[RouteContract, ...] = ()
    header_contracts: tuple[HeaderContract, ...] = ()
    semantic_heuristic_exemptions: tuple[str, ...] = ()
    semantic_authorization_path: str = ""
    enable_semantic_heuristic_candidates: bool = False
    nonblocking_paths: tuple[str, ...] = ()
    test_baseline_passthrough_paths: tuple[str, ...] = ()
    disabled_rules: tuple[str, ...] = ()
    rule_levels: Mapping[str, str] = MappingProxyType({})
    capabilities: tuple[str, ...] = ()
    settings: Mapping[str, Any] = MappingProxyType({})
    commit_policy: Mapping[str, Any] = MappingProxyType({})
    custom_rules: tuple[type[QualityRule], ...] = ()
    rule_codes: Mapping[str, str] = MappingProxyType({})
    search_strategy: type[SearchStrategy] | None = None
    report_extensions: tuple[type[ReportExtension], ...] = ()
    agents_file: str = ""


class RulePack(ABC):
    """Composable rule bundle for explicit Profile registration.

    Rule packs centralize related policy registration without Profile multiple
    inheritance, runtime reflection, or hidden global side effects.
    """

    @abstractmethod
    def register(self, profile: "QualityGuardProfile") -> None:
        """Register this bundle against one explicit Profile instance.

        Args:
            profile: Mutable authoring Profile receiving the bundle declarations.

        Returns:
            None after registration completes.
        """
        raise NotImplementedError


class QualityGuardProfile:
    """Authoring base class for explicit repository quality policy.

    Subclasses extend ``configure`` to register rules and capabilities; ``build``
    freezes that mutable authoring state into one immutable ``ProjectProfile``.
    """

    name = ""
    version = "1"
    agents_file = ""

    def __init__(
        self, manifest: Mapping[str, Any] | None = None, source: str = ""
    ) -> None:
        """Initialize one mutable Profile authoring instance.

        Args:
            manifest: Parsed declarative Profile manifest, or None for generic policy.
            source: Filesystem directory used for Profile lock and authoring assets.

        Returns:
            None after initializing empty registration state.
        """
        self.manifest = _prepare_profile_manifest(
            manifest, self.name, self.version, self.agents_file
        )
        self.source = source
        self.settings: dict[str, Any] = dict(self.manifest["settings"])
        self._configured = False
        self._disabled_rules: list[str] = []
        self._rule_levels: dict[str, str] = {}
        self._capabilities: list[str] = []
        self._nonblocking_paths: list[str] = []
        self._test_baseline_passthrough_paths: list[str] = []
        self._custom_rules: list[type[QualityRule]] = []
        self._search_strategy: type[SearchStrategy] | None = None
        self._report_extensions: list[type[ReportExtension]] = []
        self._legacy: dict[str, list[Any]] = {
            "tool_base_classes": [],
            "tool_registration_methods": [],
            "state_types": [],
            "state_writer_names": [],
            "history_markers": [],
            "trace_markers": [],
            "config_module_markers": [],
            "boundary_module_markers": [],
            "forced_interface_symbols": [],
            "stable_mapping_contracts": [],
            "callable_contracts": [],
            "frozen_sse_contracts": [],
            "sse_contracts": [],
            "route_contracts": [],
            "header_contracts": [],
            "semantic_heuristic_exemptions": [],
        }
        self.semantic_authorization_path = ""

    def configure(self) -> None:
        """Apply normalized declarative policy to this authoring instance.

        Returns:
            None after rules, capabilities, and nonblocking paths are registered.
        """
        rules = self.manifest["rules"]
        for code in _string_sequence(rules["disable"]):
            self.disable_rule(code)
        for code, level in rules["levels"].items():
            self.set_rule_level(str(code), str(level))

        self.add_capability(_string_sequence(self.manifest["capabilities"]))
        self.add_nonblocking_path(_string_sequence(self.manifest["nonblocking_paths"]))
        self._test_baseline_passthrough_paths.extend(
            _string_sequence(self.manifest["test_baseline_passthrough_paths"])
        )

    def add_values(self, field: str, values: Iterable[Any]) -> None:
        """Append values to one explicit legacy-compatible contract field.

        Args:
            field: Supported normalized policy field name.
            values: Values to append in registration order.

        Returns:
            None after extending the selected field.
        """
        if field not in self._legacy:
            raise ValueError(f"unknown profile field: {field}")
        self._legacy[field].extend(values)

    def add_rule(self, rule: type[QualityRule]) -> None:
        """Register one custom quality rule class.

        Args:
            rule: ``QualityRule`` subclass to append to this Profile.

        Returns:
            None after the rule is registered.
        """
        if not isinstance(rule, type) or not issubclass(rule, QualityRule):
            raise TypeError("profile rule must subclass QualityRule")
        if not rule.key:
            raise ValueError("profile rule key must be non-empty")
        self._custom_rules.append(rule)

    @property
    def custom_rules(self) -> tuple[type[QualityRule], ...]:
        """Return the configured custom rule classes as an immutable tuple.

        Returns:
            Custom rule classes in registration order.
        """
        return tuple(self._custom_rules)

    def disable_rule(self, code: str) -> None:
        """Disable one suppressible rule for this Profile.

        Args:
            code: QG rule code to disable.

        Returns:
            None after recording the normalized rule code.
        """
        normalized = validate_rule_code(code)
        if normalized.startswith(UNSUPPRESSIBLE_RULE_PREFIXES):
            raise ValueError(
                f"rule {normalized} is part of the guard trust boundary and cannot be disabled"
            )
        self._disabled_rules.append(normalized)

    def set_rule_level(self, code: str, level: str) -> None:
        """为某条通用规则设置项目级处置等级。

        ``BLOCKER`` 是独立硬门禁：无论问题来自当前工作树还是 Git 基线，
        只要该 finding 存在就必须 REJECT。``SEMANTIC`` 只要求进入语义审计，
        不把静态匹配本身当作有罪结论。

        Args:
            code: 需要重映射的 QG 规则编码。
            level: JSON Profile 声明的目标等级。

        Returns:
            None。
        """
        normalized = validate_rule_code(code)
        normalized_level = str(level).strip().lower()
        if normalized_level not in RULE_LEVELS:
            allowed = ", ".join(sorted(item.upper() for item in RULE_LEVELS))
            raise ValueError(
                f"invalid rule level {level!r}; expected one of: {allowed}"
            )
        if normalized.startswith(UNSUPPRESSIBLE_RULE_PREFIXES):
            raise ValueError(
                f"rule {normalized} is part of the guard trust boundary and cannot be remapped"
            )
        self._rule_levels[normalized] = normalized_level

    def add_capability(self, names: Iterable[str]) -> None:
        """Enable Core capabilities selected by this Profile.

        Args:
            names: Capability identifiers to append.

        Returns:
            None after registration.
        """
        self._capabilities.extend(str(name) for name in names if str(name))

    def add_nonblocking_path(self, patterns: Iterable[str]) -> None:
        """Register fully scanned paths excluded from the production hard gate.

        Args:
            patterns: Repository-relative glob patterns.

        Returns:
            None after registration.
        """
        self._nonblocking_paths.extend(str(item) for item in patterns if str(item))

    def set_search_strategy(self, strategy: type[SearchStrategy]) -> None:
        """Set the optional API-search reranking strategy.

        Args:
            strategy: ``SearchStrategy`` subclass owned by the Profile.

        Returns:
            None after replacing the optional strategy.
        """
        if not isinstance(strategy, type) or not issubclass(strategy, SearchStrategy):
            raise TypeError("search strategy must subclass SearchStrategy")
        self._search_strategy = strategy

    def add_report_extension(self, extension: type[ReportExtension]) -> None:
        """Register one report metadata extension.

        Args:
            extension: ``ReportExtension`` subclass to instantiate at report time.

        Returns:
            None after registration.
        """
        if not isinstance(extension, type) or not issubclass(
            extension, ReportExtension
        ):
            raise TypeError("report extension must subclass ReportExtension")
        self._report_extensions.append(extension)

    def include(self, rule_pack: RulePack) -> None:
        """Register one explicit composable rule pack.

        Args:
            rule_pack: Rule pack that will register against this Profile instance.

        Returns:
            None after the pack has registered its policy.
        """
        if not isinstance(rule_pack, RulePack):
            raise TypeError("rule pack must subclass RulePack")
        rule_pack.register(self)

    def configure_once(self) -> None:
        """Run Profile configuration at most once for this instance.

        Returns:
            None after the first configuration, or immediately when already configured.
        """
        if self._configured:
            return
        self.configure()
        self._configured = True

    def build(self) -> ProjectProfile:
        """Freeze configured authoring state into a runtime policy snapshot.

        Returns:
            Immutable ``ProjectProfile`` consumed by the scanning Core.
        """
        self.configure_once()
        configured_name = str(self.manifest["name"]).strip()
        if not configured_name:
            raise ValueError("profile name must be non-empty")
        version = str(self.manifest["version"])
        agents_file = str(self.manifest["agents_file"])
        locked_codes = dict(profile_lock_codes(self.source))
        resolved_codes: dict[str, str] = {}
        seen_codes: set[str] = set()
        for rule_cls in self._custom_rules:
            if rule_cls.code:
                code = validate_rule_code(rule_cls.code, custom=True)
            else:
                if rule_cls.key not in locked_codes:
                    raise ValueError(
                        f"custom rule {rule_cls.key!r} has no code; run the Profile authoring lock builder first"
                    )
                code = locked_codes[rule_cls.key]
            if code in seen_codes:
                raise ValueError(f"duplicate custom rule code in Profile: {code}")
            seen_codes.add(code)
            resolved_codes[rule_cls.key] = code
        return ProjectProfile(
            name=configured_name,
            version=version,
            source=self.source,
            tool_base_classes=tuple(dict.fromkeys(self._legacy["tool_base_classes"])),
            tool_registration_methods=tuple(
                dict.fromkeys(self._legacy["tool_registration_methods"])
            ),
            state_types=tuple(dict.fromkeys(self._legacy["state_types"])),
            state_writer_names=tuple(dict.fromkeys(self._legacy["state_writer_names"])),
            history_markers=tuple(dict.fromkeys(self._legacy["history_markers"])),
            trace_markers=tuple(dict.fromkeys(self._legacy["trace_markers"])),
            config_module_markers=tuple(
                dict.fromkeys(self._legacy["config_module_markers"])
            ),
            boundary_module_markers=tuple(
                dict.fromkeys(self._legacy["boundary_module_markers"])
            ),
            forced_interface_symbols=tuple(
                dict.fromkeys(self._legacy["forced_interface_symbols"])
            ),
            stable_mapping_contracts=tuple(self._legacy["stable_mapping_contracts"]),
            callable_contracts=tuple(self._legacy["callable_contracts"]),
            frozen_sse_contracts=tuple(self._legacy["frozen_sse_contracts"]),
            sse_contracts=tuple(self._legacy["sse_contracts"]),
            route_contracts=tuple(self._legacy["route_contracts"]),
            header_contracts=tuple(self._legacy["header_contracts"]),
            semantic_heuristic_exemptions=tuple(
                dict.fromkeys(self._legacy["semantic_heuristic_exemptions"])
            ),
            semantic_authorization_path=self.semantic_authorization_path,
            enable_semantic_heuristic_candidates="semantic-heuristic-candidates"
            in self._capabilities,
            nonblocking_paths=tuple(dict.fromkeys(self._nonblocking_paths)),
            test_baseline_passthrough_paths=tuple(
                dict.fromkeys(self._test_baseline_passthrough_paths)
            ),
            disabled_rules=tuple(dict.fromkeys(self._disabled_rules)),
            rule_levels=MappingProxyType(dict(sorted(self._rule_levels.items()))),
            capabilities=tuple(dict.fromkeys(self._capabilities)),
            settings=MappingProxyType(dict(self.settings)),
            commit_policy=MappingProxyType(dict(self.manifest["commit_policy"])),
            custom_rules=tuple(self._custom_rules),
            rule_codes=MappingProxyType(resolved_codes),
            search_strategy=self._search_strategy,
            report_extensions=tuple(self._report_extensions),
            agents_file=agents_file,
        )


def _release_root() -> Any:
    """Return the repository root containing this runtime package."""
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def available_profile_names(release_root: Any | None = None) -> tuple[str, ...]:
    """List Profiles physically available in the portable release.

    Args:
        release_root: Optional release root override.

    Returns:
        Sorted tuple of available Profile directory names.
    """
    from pathlib import Path

    root = Path(release_root or _release_root()) / "profiles"
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            item.name
            for item in root.iterdir()
            if item.is_dir() and (item / "profile.json").is_file()
        )
    )


def _clear_profile_bytecode_cache(module_path: Any) -> None:
    """Remove stale local bytecode for a Profile entrypoint before and after loading."""
    from pathlib import Path

    path = Path(module_path)
    cache_dir = path.parent / "__pycache__"
    if not cache_dir.is_dir():
        return
    for cached in cache_dir.glob(f"{path.stem}.*.pyc"):
        cached.unlink(missing_ok=True)
    try:
        cache_dir.rmdir()
    except OSError:
        pass


def _load_profile_extension(
    directory: Any, entrypoint: str, manifest: Mapping[str, Any]
) -> QualityGuardProfile:
    """Load and validate one executable Profile extension module.

    Args:
        directory: Profile directory containing the entrypoint module.
        entrypoint: Validated relative Python module filename.
        manifest: Normalized declarative Profile manifest.

    Returns:
        Configured authoring Profile exported by the extension module.
    """
    module_path = directory / entrypoint
    if not module_path.is_file():
        raise ValueError(f"profile entrypoint module missing: {module_path}")
    unique = f"rqg_profile_{abs(hash(str(module_path.resolve())))}"
    _clear_profile_bytecode_cache(module_path)
    spec = importlib_util.spec_from_file_location(unique, module_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import profile module: {module_path}")
    module = importlib_util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        _clear_profile_bytecode_cache(module_path)
    profile_cls = module.Profile
    if not isinstance(profile_cls, type) or not issubclass(
        profile_cls, QualityGuardProfile
    ):
        raise TypeError(
            f"profile module must export Profile(QualityGuardProfile): {entrypoint}"
        )
    profile = profile_cls(manifest=manifest, source=str(directory))
    profile.configure_once()
    return profile


def load_quality_profile(
    reference: str | None, release_root: Any | None = None
) -> QualityGuardProfile | None:
    """Load one authoring Profile from a name or explicit directory.

    ``profile.json`` remains the declarative source of truth; Python entrypoints
    are optional and only extend custom rules, contracts, or search behavior.

    Args:
        reference: Profile name/path, or None for no project Profile.
        release_root: Optional release root override.

    Returns:
        Configured authoring Profile, or None when no reference was supplied.
    """
    if not reference:
        return None
    from pathlib import Path

    root = Path(release_root or _release_root())
    candidate = Path(reference).expanduser()
    directory = (
        candidate.resolve()
        if candidate.is_dir()
        else (root / "profiles" / reference).resolve()
    )
    manifest_path = directory / "profile.json"
    if not manifest_path.is_file():
        raise ValueError(f"profile not found: {reference}")
    manifest = _prepare_profile_manifest(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    schema = manifest["schema"]
    if schema != "repository-quality-guard/profile-v1":
        raise ValueError(f"unsupported profile schema: {schema!r}")

    raw_entrypoint = manifest["entrypoint"]
    if raw_entrypoint is None:
        profile = QualityGuardProfile(manifest=manifest, source=str(directory))
        profile.configure_once()
        return profile

    entrypoint = str(raw_entrypoint).strip()
    if not entrypoint or ":" in entrypoint:
        raise ValueError(
            "profile entrypoint must name one module only; the exported class is fixed as Profile"
        )
    return _load_profile_extension(directory, entrypoint, manifest)


def _release_distribution(release_root: Any | None = None) -> str:
    """读取当前 release 的 distribution；未知时按 Portable skill 处理。"""
    from pathlib import Path

    root = Path(release_root or _release_root())
    lock = root / "runtime" / "RELEASE.lock"
    if not lock.is_file():
        return "skill"
    values: dict[str, str] = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    if "distribution" not in values:
        raise ValueError("runtime/RELEASE.lock is missing distribution")
    return values["distribution"]


def _installed_policy(release_root: Any | None = None) -> Mapping[str, Any]:
    """读取 sealed Installed Policy；安装态缺失时拒绝静默降级。"""
    from pathlib import Path

    root = Path(release_root or _release_root())
    path = root / "installed" / "POLICY.json"
    if not path.is_file():
        raise ValueError("sealed installation is missing installed/POLICY.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["schema"] != "repository-quality-guard/installed-policy-v1":
        raise ValueError("unsupported installed policy schema")
    return MappingProxyType(dict(payload))


def _installed_profile_dir(release_root: Any | None = None) -> Any:
    """Return the sealed installed-Profile payload directory."""
    from pathlib import Path

    return Path(release_root or _release_root()) / "installed" / "profile"


@dataclass(slots=True, frozen=True)
class ProfileSelection:
    """Resolved Profile identity and provenance for one command invocation.

    The immutable result distinguishes portable, explicit, automatic, and sealed
    installed policy sources so callers never infer authorization from a name.
    """

    reference: str | None
    name: str
    source: str
    reason: str


def resolve_profile_reference(
    root: Any,
    explicit: str | None = None,
    release_root: Any | None = None,
) -> ProfileSelection:
    """Resolve the authorized Profile for one target repository.

    Args:
        root: Target repository root.
        explicit: Optional Profile name/path explicitly selected by the caller.
        release_root: Optional release root for installed-policy resolution.

    Returns:
        Immutable Profile selection with identity, source, and reason.
    """
    from pathlib import Path

    repo_root = Path(root).resolve()
    distribution = _release_distribution(release_root=release_root)
    if distribution == "agents":
        policy = _installed_policy(release_root=release_root)
        name = str(policy["profile_name"])
        if not name:
            if explicit:
                raise ValueError(
                    "installed generic policy has no selectable Profile; reinstall with "
                    "deploy.py --profile to authorize one"
                )
            return ProfileSelection(
                reference=None,
                name="",
                source="sealed-installed-generic",
                reason="sealed installation is bound to generic policy",
            )
        directory = _installed_profile_dir(release_root=release_root)
        if explicit and str(explicit).strip() != name:
            raise ValueError(
                f"installed policy authorizes Profile {name!r}, not {explicit!r}; "
                "reinstall to change the installed Profile"
            )
        profile = load_quality_profile(str(directory), release_root=release_root)
        if profile is None:
            raise ValueError("sealed installation profile payload is missing")
        built = profile.build()
        if built.name != name:
            raise ValueError(
                f"installed profile identity mismatch: policy={name!r} payload={built.name!r}"
            )
        return ProfileSelection(
            reference=str(directory),
            name=built.name,
            source="sealed-installed-explicit" if explicit else "sealed-installed",
            reason=(
                "explicit --profile selected the Profile authorized by deploy.py"
                if explicit
                else "profile was installed by deploy.py and protected by the release seal"
            ),
        )

    if explicit:
        profile = load_quality_profile(explicit, release_root=release_root)
        if profile is None:
            raise ValueError(f"profile not found: {explicit}")
        built = profile.build()
        return ProfileSelection(
            reference=explicit,
            name=built.name,
            source="explicit-cli",
            reason=f"explicit --profile {explicit}",
        )
    names = set(available_profile_names(release_root=release_root))
    if repo_root.name in names:
        profile = load_quality_profile(repo_root.name, release_root=release_root)
        if profile is None:
            raise ValueError(f"profile not found: {repo_root.name}")
        built = profile.build()
        return ProfileSelection(
            reference=repo_root.name,
            name=built.name,
            source="auto-directory-name",
            reason="repository directory name exactly matches an available profile",
        )
    return ProfileSelection(
        reference=None,
        name="",
        source="generic",
        reason="no explicit profile and no exact directory-name match",
    )


def get_project_profile(
    name: str | None, release_root: Any | None = None
) -> ProjectProfile | None:
    """返回 Portable 或 sealed-installed Profile 的冻结策略快照。

    Args:
        name: Profile 名称或 sealed-installed Profile 目录。
        release_root: 可选 release 根目录，主要供安装态与测试显式绑定。

    Returns:
        冻结后的项目策略；通用策略或安装态 generic policy 返回 None。
    """
    from pathlib import Path

    if not name:
        return None
    if _release_distribution(release_root=release_root) == "agents":
        policy = _installed_policy(release_root=release_root)
        policy_name = str(policy["profile_name"])
        if not policy_name:
            return None
        directory = _installed_profile_dir(release_root=release_root)
        reference = str(name)
        if (
            reference != policy_name
            and Path(reference).resolve() != directory.resolve()
        ):
            raise ValueError(
                f"sealed installation is bound to profile {policy_name!r}, not {reference!r}"
            )
        profile = load_quality_profile(str(directory), release_root=release_root)
    else:
        profile = load_quality_profile(name, release_root=release_root)
    return profile.build() if profile is not None else None


def apply_project_profile(
    config: GuardConfig,
    name: str | None,
    selection_source: str = "",
) -> GuardConfig:
    """Merge one resolved Profile into the repository Guard configuration.

    Args:
        config: Base repository configuration.
        name: Profile reference, or None for generic policy.
        selection_source: Provenance label produced by Profile resolution.

    Returns:
        Guard configuration with frozen project policy applied.
    """
    profile = get_project_profile(name)
    if profile is None:
        from dataclasses import replace

        return replace(config, profile_source=selection_source or "generic")
    merged = config.with_project_profile(profile)
    if selection_source:
        from dataclasses import replace

        merged = replace(merged, profile_source=selection_source)
    return merged
