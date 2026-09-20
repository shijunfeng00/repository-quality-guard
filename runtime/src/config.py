from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields, replace
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


_PROFILE_NONBLOCKING_PATHS: dict[str, tuple[str, ...]] = {}


def is_test_path(path: str | Path, project_name: str = "") -> bool:
    """判断路径是否属于测试或当前 Profile 声明的非阻断源码域。

    通用规则识别 ``test/tests`` 目录；Profile 可额外声明精确 glob，
    但这些路径仍完整扫描，只是不进入生产硬门槛。Core 不包含项目名特判。
    """
    candidate = Path(path)
    normalized = candidate.as_posix().lstrip("./")
    for pattern in _PROFILE_NONBLOCKING_PATHS.get(project_name, ()):
        if fnmatch(normalized, pattern):
            return True
    lowered_parts = tuple(part.lower() for part in candidate.parts)
    if any(part in {"test", "tests"} for part in lowered_parts[:-1]):
        return True
    name = candidate.name.lower()
    if any(part in {"src", "lib"} for part in lowered_parts[:-1]):
        return False
    return name.startswith("test_") or name.endswith("_test.py")


def is_profile_nonblocking_path(path: str | Path, project_name: str = "") -> bool:
    """判断路径是否由当前 Profile 显式声明为非阻断源码域。"""
    normalized = Path(path).as_posix().lstrip("./")
    return any(
        fnmatch(normalized, pattern)
        for pattern in _PROFILE_NONBLOCKING_PATHS.get(project_name, ())
    )


def _register_profile_nonblocking_paths(name: str, patterns: tuple[str, ...]) -> None:
    """注册当前进程已加载 Profile 的非阻断路径分类。"""
    if name:
        _PROFILE_NONBLOCKING_PATHS[name] = tuple(patterns)


@dataclass(slots=True, frozen=True)
class GuardConfig:
    """
    保存仓库质量规则的阈值、架构标记和扫描范围。

    配置从目标仓库的 pyproject.toml 中加载。通用阈值用于基础规则，架构标记
    用于识别配置边界、Tool、State、History 和 Trace 等项目级契约。
    """

    project_name: str = ""
    profile_source: str = ""
    profile_capabilities: tuple[str, ...] = ()
    profile_nonblocking_paths: tuple[str, ...] = ()
    profile_test_baseline_passthrough_paths: tuple[str, ...] = ()
    disabled_rules: tuple[str, ...] = ()
    profile_rule_levels: tuple[tuple[str, str], ...] = ()
    profile_settings: tuple[tuple[str, Any], ...] = ()
    short_max_lines: int = 10
    low_use_max_calls: int = 1
    max_class_methods: int = 20
    max_function_lines: int = 500
    max_module_lines: int = 2000
    jobs: int = 0
    max_parameters: int = 8
    max_constructor_dependencies: int = 8
    max_nesting: int = 4
    max_branches: int = 12
    docstring_min_lines: int = 2
    single_use_chain_min_helpers: int = 3
    require_docstrings: bool = True
    require_docstring_sections: bool = True
    skip_private_docstrings: bool = False
    api_private_min_lines: int = 20
    strict_get: bool = True
    include_tests: bool = True
    enable_prompt_rules: bool = False
    prompt_constraint_threshold: int = 8
    prompt_fragment_threshold: int = 10
    case_literal_threshold: int = 12
    duplicate_prompt_min_chars: int = 160
    private_helper_ratio: int = 2
    static_method_ratio: float = 0.7
    no_self_method_ratio: float = 0.7
    single_method_class_max_methods: int = 3
    single_use_field_threshold: int = 3
    utility_module_definition_threshold: int = 8
    # 框架专项规则默认关闭。目标仓库必须显式声明其 Tool、State、History/Trace 契约。
    # 避免检查器把某个基准项目的命名习惯推广成通用事实。
    forced_interface_symbols: tuple[str, ...] = ()
    tool_decorators: tuple[str, ...] = ()
    tool_base_classes: tuple[str, ...] = ()
    tool_registration_methods: tuple[str, ...] = ()
    state_types: tuple[str, ...] = ()
    state_writer_names: tuple[str, ...] = ()
    history_markers: tuple[str, ...] = ()
    trace_markers: tuple[str, ...] = ()
    config_module_markers: tuple[str, ...] = ("config", "settings", "nacos")
    boundary_module_markers: tuple[str, ...] = (
        "api",
        "cli",
        "command",
        "config",
        "entrypoint",
        "http",
        "main",
        "request",
        "schema",
        "script",
        "server",
        "settings",
    )
    wrapper_class_suffixes: tuple[str, ...] = (
        "Adapter",
        "Builder",
        "Factory",
        "Manager",
    )
    exclude: tuple[str, ...] = (
        ".git/**",
        ".agents/**",
        ".venv/**",
        "venv/**",
        "build/**",
        "dist/**",
        "site-packages/**",
        "node_modules/**",
        "migrations/**",
        "generated/**",
    )
    ignored_names: tuple[str, ...] = ("main",)

    @classmethod
    def load(cls, root: Path, explicit_path: Path | None = None) -> GuardConfig:
        """
        从 pyproject.toml 加载质量检查配置。

        Args:
            root: 目标仓库根目录。
            explicit_path: 显式指定的配置文件路径；为空时读取仓库根目录配置。

        Returns:
            已完成类型归一化的不可变配置对象。
        """
        config_path = explicit_path or root / "pyproject.toml"
        if not config_path.is_file():
            return cls()
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
        tool = raw.get("tool")
        if tool is None:
            tool = {}
        section = tool.get("repo_quality_guard")
        if section is None:
            section = {}
        if not isinstance(section, dict):
            raise ValueError("[tool.repo_quality_guard] 必须是 TOML 表")
        internal_profile_fields = {
            "project_name",
            "profile_source",
            "profile_capabilities",
            "profile_nonblocking_paths",
            "profile_test_baseline_passthrough_paths",
            "disabled_rules",
            "profile_rule_levels",
            "profile_settings",
        }
        allowed = {item.name for item in fields(cls)} - internal_profile_fields
        unknown = sorted(set(section) - allowed)
        if unknown:
            raise ValueError(f"未知质量检查配置项: {', '.join(unknown)}")
        tuple_fields = {
            "boundary_module_markers",
            "config_module_markers",
            "exclude",
            "forced_interface_symbols",
            "history_markers",
            "ignored_names",
            "profile_capabilities",
            "profile_nonblocking_paths",
            "profile_test_baseline_passthrough_paths",
            "disabled_rules",
            "state_types",
            "state_writer_names",
            "tool_base_classes",
            "tool_decorators",
            "tool_registration_methods",
            "trace_markers",
            "wrapper_class_suffixes",
        }
        normalized: dict[str, Any] = {}
        for key, value in section.items():
            if key in tuple_fields:
                if not isinstance(value, list) or not all(
                    isinstance(item, str) for item in value
                ):
                    raise ValueError(f"{key} 必须是字符串数组")
                normalized[key] = tuple(value)
            else:
                normalized[key] = value
        return cls(**normalized)

    def with_project_profile(self, profile: Any) -> GuardConfig:
        """
        合并显式选择的项目架构档案。

        Args:
            profile: 提供架构标记元组的项目档案对象。

        Returns:
            保留仓库自定义项并追加档案标记的新配置。
        """

        def merged(current: tuple[str, ...], extra: tuple[str, ...]) -> tuple[str, ...]:
            """
            合并架构标记并保持首次出现顺序。

            Args:
                current: 仓库配置中已有的标记。
                extra: 项目档案追加的标记。

            Returns:
                去重且顺序稳定的标记元组。
            """
            return tuple(dict.fromkeys((*current, *extra)))

        settings = tuple(sorted(dict(profile.settings).items(), key=lambda item: item[0]))
        _register_profile_nonblocking_paths(profile.name, profile.nonblocking_paths)
        return replace(
            self,
            project_name=profile.name,
            profile_source=profile.source,
            profile_capabilities=tuple(profile.capabilities),
            profile_nonblocking_paths=tuple(profile.nonblocking_paths),
            profile_test_baseline_passthrough_paths=tuple(profile.test_baseline_passthrough_paths),
            disabled_rules=tuple(profile.disabled_rules),
            profile_rule_levels=tuple(sorted(dict(profile.rule_levels).items())),
            profile_settings=settings,
            forced_interface_symbols=merged(
                self.forced_interface_symbols, profile.forced_interface_symbols
            ),
            tool_base_classes=merged(self.tool_base_classes, profile.tool_base_classes),
            tool_registration_methods=merged(
                self.tool_registration_methods, profile.tool_registration_methods
            ),
            state_types=merged(self.state_types, profile.state_types),
            state_writer_names=merged(
                self.state_writer_names, profile.state_writer_names
            ),
            history_markers=merged(self.history_markers, profile.history_markers),
            trace_markers=merged(self.trace_markers, profile.trace_markers),
            config_module_markers=merged(
                self.config_module_markers, profile.config_module_markers
            ),
            boundary_module_markers=merged(
                self.boundary_module_markers, profile.boundary_module_markers
            ),
        )

    def with_overrides(
        self,
        *,
        strict_get: bool | None = None,
        include_tests: bool | None = None,
        extra_excludes: tuple[str, ...] = (),
    ) -> GuardConfig:
        """
        应用命令行层面的运行时覆盖项。

        Args:
            strict_get: 是否把所有 .get() 都视为映射候选。
            include_tests: 是否扫描测试目录。
            extra_excludes: 命令行追加的排除 glob。

        Returns:
            包含覆盖结果的新配置对象。
        """
        return replace(
            self,
            strict_get=self.strict_get if strict_get is None else strict_get,
            include_tests=self.include_tests
            if include_tests is None
            else include_tests,
            exclude=self.exclude + extra_excludes,
        )
