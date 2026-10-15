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

    call_edges = [
        e
        for e in graph["edges"]
        if e["kind"] in {"direct_call", "may_dispatch"}
        and e["source"] in parent
        and e["target"] in parent
    ]
    direct = 0
    possible = 0
    linked = set()
    for edge in call_edges:
        if edge["kind"] == "direct_call":
            direct += 1
        else:
            possible += 1
        a, b = root(edge["source"]), root(edge["target"])
        linked.update((edge["source"], edge["target"]))
        if a != b:
            if sizes[a] < sizes[b]:
                a, b = b, a
            parent[b] = a
            sizes[a] += sizes[b]
    components = {root(symbol) for symbol in callables}
    largest = max((sizes[x] for x in components), default=0)
    relations = Counter(edge["kind"] for edge in graph["edges"])
    return (
        "\n".join(
            [
                "# 仓库图结构统计（静态，有来源约束）",
                "",
                f"函数/方法/测试符号：{len(callables)}",
                f"确定直接调用：{direct} · 类型约束可能调用：{possible}",
                f"弱连通分量：{len(components)} · 最大分量：{largest} "
                f"({largest / len(callables):.1%})"
                if callables
                else "弱连通分量：0",
                f"无已解析调用边的符号：{len(callables - linked)}",
                "图关系统计："
                + ", ".join(
                    f"{name}={value}" for name, value in sorted(relations.items())
                ),
                "",
                "注：只统计已解析调用关系；类继承、字段访问和参数传导单独计数，"
                "不强制将不相关函数并入同一连通分量；未知关系不是零真实调用。",
            ]
        )
        + "\n"
    )
