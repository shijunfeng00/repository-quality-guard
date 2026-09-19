from __future__ import annotations

from dataclasses import dataclass, replace

from .model import InterfaceSymbol


@dataclass(slots=True, frozen=True)
class ImportAlias:
    """
    保存模块级 ``from ... import ...`` 形成的重导出候选。

    候选同时记录本地别名是否被模块实现消费，用于区分普通内部 import 和
    真正的公开重导出。
    """

    path: str
    module: str
    imported_name: str
    local_name: str
    level: int
    line: int
    used_in_module: bool


def resolve_import_aliases(
    symbols: dict[str, InterfaceSymbol],
    aliases: list[ImportAlias],
    paths: tuple[str, ...],
) -> dict[str, InterfaceSymbol]:
    """
    把明确重导出的仓库内导入解析为当前模块接口别名。

    Args:
        symbols: 已提取的接口符号索引。
        aliases: 模块级显式导入候选。
        paths: 当前 Git 快照包含的 Python 文件路径。

    Returns:
        包含原始声明和明确重导出别名的新接口符号索引。
    """
    resolved = dict(symbols)
    module_paths = {_module_name(path): path for path in paths}
    unresolved = list(aliases)
    while unresolved:
        next_round: list[ImportAlias] = []
        changed = False
        for alias in unresolved:
            if not _is_reexport(alias, resolved):
                continue
            target = _resolve_alias_target(alias, resolved, module_paths)
            if target is None:
                next_round.append(alias)
                continue
            if any(
                item.path == alias.path
                and item.qualname == alias.local_name
                and item.kind in {"class", "function", "global_variable"}
                for item in resolved.values()
            ):
                continue
            exported = replace(
                target,
                path=alias.path,
                qualname=alias.local_name,
                line=alias.line,
                variant=0,
                exposure="reexport",
            )
            resolved[exported.key] = exported
            changed = True
        if not changed:
            break
        unresolved = next_round
    return resolved


def filter_interface_symbols(
    symbols: dict[str, InterfaceSymbol],
    sources: dict[str, str],
    aliases: list[ImportAlias],
    *,
    forced_symbols: set[str],
    include_private: bool,
    private_min_lines: int,
) -> dict[str, InterfaceSymbol]:
    """
    只保留当前模式下应视为调用方契约的接口符号。

    Args:
        symbols: 尚未过滤的完整接口符号索引。
        sources: 当前 Git 快照的 Python 源码集合。
        forced_symbols: 项目档案强制视为接口的 ``path:qualname`` 集合。
        include_private: 是否直接保留全部私有符号。
        private_min_lines: 私有函数或方法进入接口报告的最小代码行数。
        aliases: 接口提取阶段已经收集的 import 别名事实。

    Returns:
        只包含目标接口范围的符号索引。
    """
    if include_private:
        return {key: replace(symbol, exposure="all") for key, symbol in symbols.items()}
    exports_by_path = {
        symbol.path: set(symbol.exports)
        for symbol in symbols.values()
        if symbol.kind == "global_variable" and symbol.qualname == "__all__"
    }
    external_private_symbols = _collect_external_private_symbols(sources, aliases)
    result: dict[str, InterfaceSymbol] = {}
    for key, symbol in symbols.items():
        exposure = _symbol_exposure(
            symbol,
            exports_by_path,
            external_private_symbols,
            forced_symbols,
            private_min_lines,
        )
        if exposure:
            result[key] = replace(symbol, exposure=exposure)
    return result


def _symbol_exposure(
    symbol: InterfaceSymbol,
    exports_by_path: dict[str, set[str]],
    external_private_symbols: set[tuple[str, str]],
    forced_symbols: set[str],
    private_min_lines: int,
) -> str:
    """返回符号进入公开接口报告的原因。"""
    if symbol.kind == "file":
        return "file"
    parts = symbol.qualname.split(".")
    leaf = parts[-1]
    top = parts[0]
    forced = f"{symbol.path}:{symbol.qualname}" in forced_symbols
    path_exports = exports_by_path[symbol.path] if symbol.path in exports_by_path else set()
    explicit_export = top in path_exports
    external_private = (symbol.path, top) in external_private_symbols
    if (
        top.startswith("_")
        and top != "__all__"
        and not explicit_export
        and not forced
        and not external_private
    ):
        return ""
    if symbol.kind in {"class", "function", "global_variable"}:
        return _module_symbol_exposure(
            symbol,
            leaf,
            forced,
            explicit_export,
            external_private_symbols,
            private_min_lines,
        )
    return _member_symbol_exposure(
        symbol,
        leaf,
        forced,
        external_private_symbols,
        private_min_lines,
    )


def _module_symbol_exposure(
    symbol: InterfaceSymbol,
    leaf: str,
    forced: bool,
    explicit_export: bool,
    external_private_symbols: set[tuple[str, str]],
    private_min_lines: int,
) -> str:
    """判断模块级类、函数或变量是否属于公开契约。"""
    if forced:
        return "forced"
    if explicit_export:
        return "explicit_export"
    if not leaf.startswith("_") or leaf == "__all__":
        return "public"
    if (symbol.path, leaf) in external_private_symbols:
        return "external_private"
    if symbol.kind == "function" and symbol.code_lines >= private_min_lines:
        return "significant_private"
    return ""


def _member_symbol_exposure(
    symbol: InterfaceSymbol,
    leaf: str,
    forced: bool,
    external_private_symbols: set[tuple[str, str]],
    private_min_lines: int,
) -> str:
    """判断类成员是否属于公开、协议或重要私有契约。"""
    candidates = (
        ("forced", forced),
        ("protocol", _is_dunder(leaf)),
        ("public", not leaf.startswith("_")),
        ("external_private", (symbol.path, leaf) in external_private_symbols),
    )
    exposure = next((name for name, active in candidates if active), "")
    if exposure or symbol.kind != "method":
        return exposure
    return _private_method_exposure(symbol, private_min_lines)


def _private_method_exposure(symbol: InterfaceSymbol, private_min_lines: int) -> str:
    """判断私有方法是否因注册装饰器或体量进入接口报告。"""
    benign = {"abstractmethod", "classmethod", "override", "property", "staticmethod"}
    for decorator in symbol.decorators:
        normalized = decorator.removeprefix("@").split("(", 1)[0]
        if normalized.endswith((".setter", ".deleter")):
            continue
        if normalized.rsplit(".", 1)[-1] not in benign:
            return "registered_private"
    if symbol.code_lines >= private_min_lines:
        return "significant_private"
    return ""


def _collect_external_private_symbols(
    sources: dict[str, str],
    aliases: list[ImportAlias],
) -> set[tuple[str, str]]:
    """
    从既有 import 别名事实收集跨模块暴露的私有模块符号。

    Args:
        sources: 当前 Git 快照的 Python 源码集合。
        aliases: 接口提取器已收集的 import 别名事实。

    Returns:
        目标文件路径与私有符号名组成的集合。
    """
    module_paths = {_module_name(path): path for path in sources}
    references: set[tuple[str, str]] = set()
    for alias in aliases:
        if not alias.imported_name.startswith("_") or _is_dunder(alias.imported_name):
            continue
        module = _resolve_import_module(alias)
        if module not in module_paths:
            continue
        target_path = module_paths[module]
        if target_path != alias.path:
            references.add((target_path, alias.imported_name))
    return references


def _is_reexport(alias: ImportAlias, symbols: dict[str, InterfaceSymbol]) -> bool:
    """判断 import 候选是否明确承担重导出职责。"""
    if alias.path == "__init__.py" or alias.path.endswith("/__init__.py"):
        return True
    exports = next(
        (
            set(item.exports)
            for item in symbols.values()
            if item.path == alias.path
            and item.kind == "global_variable"
            and item.qualname == "__all__"
        ),
        set(),
    )
    return alias.local_name in exports or not alias.used_in_module


def _resolve_alias_target(
    alias: ImportAlias,
    symbols: dict[str, InterfaceSymbol],
    module_paths: dict[str, str],
) -> InterfaceSymbol | None:
    """解析重导出候选指向的仓库内原始符号。"""
    target_path = module_paths.get(_resolve_import_module(alias))
    if target_path is None:
        return None
    return next(
        (
            item
            for item in symbols.values()
            if item.path == target_path
            and item.qualname == alias.imported_name
            and item.kind in {"class", "function", "global_variable"}
            and item.variant == 0
        ),
        None,
    )


def _module_name(path: str) -> str:
    """把仓库相对 Python 路径转换为导入模块名。"""
    module = path[:-3].replace("/", ".")
    if module == "__init__":
        return ""
    return module[: -len(".__init__")] if module.endswith(".__init__") else module


def _resolve_import_module(alias: ImportAlias) -> str:
    """解析相对或绝对导入候选对应的模块名。"""
    if alias.level == 0:
        return alias.module
    current_module = _module_name(alias.path)
    package = (
        current_module
        if alias.path.endswith("/__init__.py") or alias.path == "__init__.py"
        else current_module.rpartition(".")[0]
    )
    parts = [item for item in package.split(".") if item]
    remove = alias.level - 1
    if remove > len(parts):
        return ""
    base = parts[: len(parts) - remove]
    if alias.module:
        base.extend(alias.module.split("."))
    return ".".join(base)


def _is_dunder(name: str) -> bool:
    """判断名称是否为 Python 双下划线协议名称。"""
    return name.startswith("__") and name.endswith("__")
