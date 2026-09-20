from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import util as importlib_util
import json
import re
from types import MappingProxyType
from typing import Any

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
    }
)


def _validate_rule_code(code: str, *, custom: bool = False) -> str:
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
) -> Mapping[str, Any]:
    """Complete optional JSON Profile fields once at the input boundary.

    Args:
        manifest: Parsed ``profile.json`` mapping, or None for a generic Profile.

    Returns:
        Read-only manifest whose optional policy fields are always present.

    Raises:
        ValueError: A structured field has an incompatible JSON type.
    """
    raw = dict(manifest or {})
    normalized: dict[str, Any] = dict(_PROFILE_MANIFEST_DEFAULTS)
    normalized.update(raw)

    rules = normalized["rules"]
    if not isinstance(rules, Mapping):
        raise ValueError("profile rules must be an object")
    normalized_rules = {"disable": (), "levels": {}}
    normalized_rules.update(rules)
    if not isinstance(normalized_rules["levels"], Mapping):
        raise ValueError("profile rules.levels must be an object")
    normalized["rules"] = MappingProxyType(normalized_rules)

    settings = normalized["settings"]
    if not isinstance(settings, Mapping):
        raise ValueError("profile settings must be an object")
    normalized["settings"] = MappingProxyType(dict(settings))
    return MappingProxyType(normalized)


def _profile_lock_codes(source: str) -> Mapping[str, str]:
    """Read immutable rule-key -> code assignments without mutating the Profile."""
    from pathlib import Path

    if not source:
        return MappingProxyType({})
    path = Path(source) / "PROFILE.lock"
    if not path.is_file():
        return MappingProxyType({})
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != PROFILE_LOCK_SCHEMA:
        raise ValueError(f"unsupported PROFILE.lock schema: {payload.get('schema')!r}")
    raw = payload.get("rule_codes") or {}
    if not isinstance(raw, dict):
        raise ValueError("PROFILE.lock rule_codes must be an object")
    result: dict[str, str] = {}
    for key, code in raw.items():
        result[str(key)] = _validate_rule_code(str(code), custom=True)
    return MappingProxyType(result)


@dataclass(slots=True, frozen=True)
class RuleContext:
    """只读暴露给 Profile 自定义规则的仓库事实视图。"""

    root: Any
    config: GuardConfig
    analysis: Any
    base_analysis: Any = None
    interface_diff: Any = None
    diff_base: str | None = None
    staged: bool = False

    def source(self, path: str) -> str:
        """返回统一分析快照中的源码；不存在时抛出 KeyError。"""
        unit = self.analysis.unit(path) if self.analysis is not None else None
        if unit is None:
            raise KeyError(path)
        return unit.source

    def python_ast(self, path: str) -> Any:
        """返回统一分析快照中的 Python AST；不存在时返回 None。"""
        unit = self.analysis.unit(path) if self.analysis is not None else None
        return unit.tree if unit is not None else None

    @property
    def settings(self) -> Mapping[str, Any]:
        """返回 Profile settings 的只读映射。"""
        raw = getattr(self.config, "profile_settings", {}) or {}
        return MappingProxyType(dict(raw))


class QualityRule(ABC):
    """Profile 可追加的标准质量规则接口。"""

    key: str = ""
    code: str | None = None
    title: str = ""
    description: str = ""
    severity: str = "warning"
    suppressible: bool = True
    scope: str = "repository"

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        """基于只读 RuleContext 产出标准 Finding。"""
        raise NotImplementedError


class SearchStrategy(ABC):
    """Profile 对默认接口检索结果进行可选重排的扩展点。"""

    def rerank(self, query: str, rows: Sequence[Any], *, root: Any) -> Sequence[Any]:
        """默认保持 BM25 结果顺序。"""
        return rows


class ReportExtension(ABC):
    """Profile 可选报告扩展接口；不得控制进程退出码或 release seal。"""

    def metadata(self) -> Mapping[str, str]:
        """返回可附加到报告元数据的稳定键值。"""
        return {}


@dataclass(slots=True, frozen=True)
class ProjectProfile:
    """QualityGuardProfile configure 后冻结的运行策略快照。"""

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
    custom_rules: tuple[type[QualityRule], ...] = ()
    rule_codes: Mapping[str, str] = MappingProxyType({})
    search_strategy: type[SearchStrategy] | None = None
    report_extensions: tuple[type[ReportExtension], ...] = ()
    agents_file: str = ""


class RulePack(ABC):
    """Composable OOP rule pack; avoids Profile multiple inheritance and reflection."""

    @abstractmethod
    def register(self, profile: "QualityGuardProfile") -> None:
        """Register rules/capabilities against one explicit Profile instance."""
        raise NotImplementedError


class QualityGuardProfile:
    """公开的 OOP Profile 基类；子类只在 configure() 中注册策略。"""

    name = ""
    version = "1"
    agents_file = ""

    def __init__(
        self, *, manifest: Mapping[str, Any] | None = None, source: str = ""
    ) -> None:
        self.manifest = _prepare_profile_manifest(manifest)
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
        """Apply normalized declarative policy; subclasses extend via ``super()``."""
        rules = self.manifest["rules"]
        for code in _string_sequence(rules["disable"]):
            self.disable_rule(code)
        for code, level in rules["levels"].items():
            self.set_rule_level(str(code), str(level))

        self.add_capability(*_string_sequence(self.manifest["capabilities"]))
        self.add_nonblocking_path(*_string_sequence(self.manifest["nonblocking_paths"]))
        self._test_baseline_passthrough_paths.extend(
            _string_sequence(self.manifest["test_baseline_passthrough_paths"])
        )

    def add_values(self, field: str, *values: Any) -> None:
        """向兼容 contract/config 字段追加值。"""
        if field not in self._legacy:
            raise ValueError(f"unknown profile field: {field}")
        self._legacy[field].extend(values)

    def add_rule(self, rule: type[QualityRule]) -> None:
        """注册自定义规则类。"""
        if not isinstance(rule, type) or not issubclass(rule, QualityRule):
            raise TypeError("profile rule must subclass QualityRule")
        if not rule.key:
            raise ValueError("profile rule key must be non-empty")
        self._custom_rules.append(rule)

    def disable_rule(self, code: str) -> None:
        """声明该 Profile 不适用的可抑制规则；信任边界规则不可关闭。"""
        normalized = _validate_rule_code(code)
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
        normalized = _validate_rule_code(code)
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

    def add_capability(self, *names: str) -> None:
        """启用由通用 Core 实现、Profile 选择的能力。"""
        self._capabilities.extend(str(name) for name in names if str(name))

    def add_nonblocking_path(self, *patterns: str) -> None:
        """声明完整扫描但不进入生产硬门槛的路径 glob。"""
        self._nonblocking_paths.extend(str(item) for item in patterns if str(item))

    def set_search_strategy(self, strategy: type[SearchStrategy]) -> None:
        """设置可选检索重排策略。"""
        if not isinstance(strategy, type) or not issubclass(strategy, SearchStrategy):
            raise TypeError("search strategy must subclass SearchStrategy")
        self._search_strategy = strategy

    def add_report_extension(self, extension: type[ReportExtension]) -> None:
        """注册报告扩展。"""
        if not isinstance(extension, type) or not issubclass(
            extension, ReportExtension
        ):
            raise TypeError("report extension must subclass ReportExtension")
        self._report_extensions.append(extension)

    def include(self, rule_pack: RulePack) -> None:
        """组合一个显式 RulePack，而不是依赖 Profile 多重继承或动态反射。"""
        if not isinstance(rule_pack, RulePack):
            raise TypeError("rule pack must subclass RulePack")
        rule_pack.register(self)

    def _configure_once(self) -> None:
        if self._configured:
            return
        self.configure()
        self._configured = True

    def build(self) -> ProjectProfile:
        """执行 configure 并冻结为供 Core 使用的策略快照。"""
        self._configure_once()
        configured_name = str(self.manifest.get("name") or self.name).strip()
        if not configured_name:
            raise ValueError("profile name must be non-empty")
        version = str(self.manifest.get("version") or self.version or "")
        agents_file = str(self.manifest.get("agents_file") or self.agents_file or "")
        locked_codes = dict(_profile_lock_codes(self.source))
        resolved_codes: dict[str, str] = {}
        seen_codes: set[str] = set()
        for rule_cls in self._custom_rules:
            code = (
                _validate_rule_code(rule_cls.code, custom=True)
                if rule_cls.code
                else locked_codes.get(rule_cls.key)
            )
            if not code:
                raise ValueError(
                    f"custom rule {rule_cls.key!r} has no code; run the Profile authoring lock builder first"
                )
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
            custom_rules=tuple(self._custom_rules),
            rule_codes=MappingProxyType(resolved_codes),
            search_strategy=self._search_strategy,
            report_extensions=tuple(self._report_extensions),
            agents_file=agents_file,
        )


def _release_root() -> Any:
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def _profile_dir(reference: str, *, release_root: Any | None = None) -> Any:
    from pathlib import Path

    root = Path(release_root or _release_root())
    candidate = Path(reference).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    return (root / "profiles" / reference).resolve()


def available_profile_names(*, release_root: Any | None = None) -> tuple[str, ...]:
    """列出当前 Portable release 内实际存在的 Profile 名称。"""
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


def load_quality_profile(
    reference: str | None, *, release_root: Any | None = None
) -> QualityGuardProfile | None:
    """从名字或显式目录加载 Profile。

    ``profile.json`` 是声明式策略的唯一真相源；只有需要自定义 AST 规则、
    contract 或搜索扩展时才提供 ``entrypoint``。纯规则等级/开关 Profile
    不需要 Python extension。
    """
    if not reference:
        return None
    directory = _profile_dir(reference, release_root=release_root)
    manifest_path = directory / "profile.json"
    if not manifest_path.is_file():
        raise ValueError(f"profile not found: {reference}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") not in {None, "repository-quality-guard/profile-v1"}:
        raise ValueError(f"unsupported profile schema: {manifest.get('schema')!r}")

    raw_entrypoint = manifest.get("entrypoint")
    if raw_entrypoint is None:
        profile = QualityGuardProfile(manifest=manifest, source=str(directory))
        profile._configure_once()
        return profile

    entrypoint = str(raw_entrypoint).strip()
    if not entrypoint or ":" in entrypoint:
        raise ValueError(
            "profile entrypoint must name one module only; the exported class is fixed as Profile"
        )
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
    profile._configure_once()
    return profile


def _release_distribution(*, release_root: Any | None = None) -> str:
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
    return values.get("distribution", "skill")


def _installed_policy(*, release_root: Any | None = None) -> Mapping[str, Any]:
    """读取 sealed Installed Policy；安装态缺失时拒绝静默降级。"""
    from pathlib import Path

    root = Path(release_root or _release_root())
    path = root / "installed" / "POLICY.json"
    if not path.is_file():
        raise ValueError("sealed installation is missing installed/POLICY.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "repository-quality-guard/installed-policy-v1":
        raise ValueError("unsupported installed policy schema")
    return MappingProxyType(dict(payload))


def _installed_profile_dir(*, release_root: Any | None = None) -> Any:
    from pathlib import Path

    return Path(release_root or _release_root()) / "installed" / "profile"


@dataclass(slots=True, frozen=True)
class ProfileSelection:
    """一次 Portable/Installed Profile 解析结果。"""

    reference: str | None
    name: str
    source: str
    reason: str


def resolve_profile_reference(
    root: Any,
    explicit: str | None = None,
    *,
    release_root: Any | None = None,
) -> ProfileSelection:
    """解析 Profile：Installed 只读冻结策略；Portable 才允许显式/自动选择。"""
    from pathlib import Path

    repo_root = Path(root).resolve()
    distribution = _release_distribution(release_root=release_root)
    if distribution == "agents":
        if explicit:
            raise ValueError(
                "--profile is unavailable for a sealed installation; reinstall to change policy"
            )
        policy = _installed_policy(release_root=release_root)
        name = str(policy.get("profile_name") or "")
        if not name:
            return ProfileSelection(
                reference=None,
                name="",
                source="sealed-installed-generic",
                reason="sealed installation is bound to generic policy",
            )
        directory = _installed_profile_dir(release_root=release_root)
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
            source="sealed-installed",
            reason="profile was frozen by deploy.py and protected by the release seal",
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
    *,
    selection_source: str = "",
) -> GuardConfig:
    """把动态 Profile 策略合并到仓库 GuardConfig。"""
    profile = get_project_profile(name)
    if profile is None:
        from dataclasses import replace

        return replace(config, profile_source=selection_source or "generic")
    merged = config.with_project_profile(profile)
    if selection_source:
        from dataclasses import replace

        merged = replace(merged, profile_source=selection_source)
    return merged


# 仅为旧内部 import 提供动态快照；CLI 不再把它作为 choices 暴露给模型。
PROJECT_PROFILE_NAMES = available_profile_names()
