"""Read-only graph connectivity projection from the cached normalized topology."""

from __future__ import annotations

from collections import Counter


def graph_statistics(graph: dict) -> str:
    """Summarize source-bound connectivity without inventing missing calls.

    Args:
        graph: Cached normalized graph, including symbols and typed edges.

    Returns:
        Auditable Markdown with function-only weak-component counts, not a
        runtime reachability or algorithm-correctness claim.
    """
    callables = {
        s["id"] for s in graph["symbols"] if s["kind"] in {"function", "method", "test"}
    }
    parent = {symbol: symbol for symbol in callables}
    sizes = {symbol: 1 for symbol in callables}

    def root(node):
        """Locate a connected-component representative with path compression.

        Args:
            node: One known callable symbol identity in this static graph.

        Returns:
            The canonical identity representing its currently merged component.
        """
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    by_kind = {
        name: [
            edge for edge in graph["edges"]
            if edge["kind"] == name
            and edge["source"] in parent and edge["target"] in parent
        ]
        for name in ("direct_call", "may_dispatch", "may_callback", "callback_registration")
    }
    call_edges = by_kind["direct_call"] + by_kind["may_dispatch"] + by_kind["may_callback"]
    linked = set()
    for edge in call_edges:
        a, b = root(edge["source"]), root(edge["target"])
        linked.update((edge["source"], edge["target"]))
        if a != b:
            if sizes[a] < sizes[b]:
                a, b = b, a
            parent[b] = a
            sizes[a] += sizes[b]
    components = {root(symbol) for symbol in callables}
    largest = max((sizes[x] for x in components), default=0)
    # The augmented view is a *reference/registration* graph, not an
    # executable call graph. Never silently count registration as a call.
    callback_count = len(by_kind["callback_registration"])
    for edge in by_kind["callback_registration"]:
        a, b = root(edge["source"]), root(edge["target"])
        if a != b:
            if sizes[a] < sizes[b]:
                a, b = b, a
            parent[b] = a
            sizes[a] += sizes[b]
    augmented_components = {root(symbol) for symbol in callables}
    augmented_largest = max((sizes[x] for x in augmented_components), default=0)
    augmented_linked = linked | {
        end for edge in by_kind["callback_registration"]
        for end in (edge["source"], edge["target"])
    }
    direct = len(by_kind["direct_call"])
    possible = len(by_kind["may_dispatch"])
    scheduled = len(by_kind["may_callback"])
    relations = Counter(edge["kind"] for edge in graph["edges"])
    return (
        "\n".join(
            [
                "# 仓库图结构统计（静态，有来源约束）",
                "",
                f"函数/方法/测试符号：{len(callables)}",
                f"确定直接调用：{direct} · 类型约束可能调用：{possible} · 调度可能回调：{scheduled}",
                f"弱连通分量：{len(components)} · 最大分量：{largest} "
                f"({largest / len(callables):.1%})"
                if callables
                else "弱连通分量：0",
                f"无已解析调用边的符号：{len(callables - linked)}",
                f"包含注册/引用候选后：注册边 {callback_count} · 弱连通分量 {len(augmented_components)} "
                f"· 最大分量 {augmented_largest} ({augmented_largest / len(callables):.1%}) "
                f"· 未连接 {len(callables - augmented_linked)}"
                if callables else "包含注册/引用候选后：空图",
                "图关系统计："
                + ", ".join(
                    f"{name}={value}" for name, value in sorted(relations.items())
                ),
                "",
                "注：只统计已解析调用关系；类继承、字段访问和参数传导单独计数，"
                "不强制将不相关函数并入同一连通分量；注册边只说明源码函数作为参数被引用，"
                "不保证回调会运行；第二组连通性不能作为调用图完整率。",
            ]
        )
        + "\n"
    )
