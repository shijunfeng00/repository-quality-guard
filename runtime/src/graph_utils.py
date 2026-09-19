"""与语言无关的小型有向图算法。"""

from __future__ import annotations


def strongly_connected_components(graph: dict[str, set[str]]) -> list[set[str]]:
    """
    使用 Tarjan 算法计算有向图强连通分量。

    Args:
        graph: 节点到出边节点集合的有向图；出边目标应同时存在于图中。

    Returns:
        图中全部强连通分量。
    """
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def _visit(node: str) -> None:
        """深度优先访问单个节点并更新 low-link。"""
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(graph[node]):
            if target not in indices:
                _visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: set[str] = set()
        while stack:
            target = stack.pop()
            on_stack.remove(target)
            component.add(target)
            if target == node:
                break
        components.append(component)

    for node in sorted(graph):
        if node not in indices:
            _visit(node)
    return components
