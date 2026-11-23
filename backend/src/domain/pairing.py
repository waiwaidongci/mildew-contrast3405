"""同库房同架位的霉斑巡检配对。

业务口径（与湿度参数无关）：

- 仅在「同一库房、同一架位」内配对；一张巡检单（inspection_id）最多使用一次。
- 一对由一张见霉斑单（正组）与一张未见霉斑单（负组）组成，
  两次巡检日期相隔不超过 ``max_gap`` 天（默认四天，含边界）。
- 优化顺序固定为：配对数量最多 → 全部配对的日期间隔总和最短 →
  同分按成对单号字典序取最前。结果完全确定，同批并发或调整湿度线都得到同一组配对。

每个架位是一张二分图，候选边权值为间隔天数。算法：

1. 最小费用最大流求出该架位的目标配对数 ``F`` 与最小间隔总和 ``G``；
2. 把候选边按 (正单号, 负单号) 字典序逐条贪心固定。固定一条边后，先用廉价的
   最大流判断剩余顶点能否凑满所需配对数，再用最小费用最大流确认剩余间隔总和
   恰好等于剩余预算；两者都满足才保留该边。该可行性判定保证选出的边始终可补全
   为某个最优匹配，从而在所有最优匹配中固定得到单号字典序最前的唯一一组。
整个过程只读取巡检日期、库房、架位和霉斑结论，与湿度等现场条件完全无关。
单架位候选边超过 :data:`_SHELF_EDGE_BUDGET` 时抛出 :class:`PairingLimitError`，
不静默牺牲最小间隔保证。
"""

from collections import defaultdict, deque
from datetime import date
import heapq

MAX_GAP_DAYS = 4
# 单架位候选边的安全预算：实际台账同一架位的巡检单稀疏，正常规模严格最优。
# 极端集中到同一架位且候选边超出预算时显式报错，避免静默牺牲最小间隔保证。
_SHELF_EDGE_BUDGET = 4000


class PairingLimitError(ValueError):
    """单个架位的候选配对规模超出安全预算，需要拆分数据或放宽范围。"""


def _day(value):
    return date.fromisoformat(value).toordinal()


class _MinCostFlow:
    """带节点势的 Dijkstra 最小费用最大流，边权为间隔天数。

    初始费用非负，先用 Bellman-Ford 求一组可行势，之后每次增广用 Dijkstra
    在非负约化费用上求最短路并更新势。返回 (最大流量, 最小费用总和)。
    """

    def __init__(self, node_count):
        self.node_count = node_count
        self.graph = [[] for _ in range(node_count)]

    def add_edge(self, source, target, capacity, cost):
        forward = [target, capacity, cost, len(self.graph[target])]
        backward = [source, 0, -cost, len(self.graph[source])]
        self.graph[source].append(forward)
        self.graph[target].append(backward)

    def _initial_potential(self, source):
        distance = [None] * self.node_count
        distance[source] = 0
        for _ in range(self.node_count - 1):
            changed = False
            for node in range(self.node_count):
                if distance[node] is None:
                    continue
                for target, capacity, cost, _ in self.graph[node]:
                    if capacity > 0 and (
                        distance[target] is None or distance[node] + cost < distance[target]
                    ):
                        distance[target] = distance[node] + cost
                        changed = True
            if not changed:
                break
        return [0 if value is None else value for value in distance]

    def run(self, source, sink):
        potential = self._initial_potential(source)
        total_flow = 0
        total_cost = 0
        size = self.node_count
        while True:
            distance = [None] * size
            previous_node = [-1] * size
            previous_edge = [-1] * size
            distance[source] = 0
            heap = [(0, source)]
            while heap:
                current, node = heapq.heappop(heap)
                if current != distance[node]:
                    continue
                for edge_index, edge in enumerate(self.graph[node]):
                    target, capacity, cost, _ = edge
                    if capacity <= 0:
                        continue
                    reduced = cost + potential[node] - potential[target]
                    candidate = current + reduced
                    if distance[target] is None or candidate < distance[target]:
                        distance[target] = candidate
                        previous_node[target] = node
                        previous_edge[target] = edge_index
                        heapq.heappush(heap, (candidate, target))
            if previous_node[sink] == -1:
                break
            for node in range(size):
                if distance[node] is not None:
                    potential[node] += distance[node]
            node = sink
            path_cost = 0
            while node != source:
                edge = self.graph[previous_node[node]][previous_edge[node]]
                edge[1] -= 1
                self.graph[node][edge[3]][1] += 1
                path_cost += edge[2]
                node = previous_node[node]
            total_flow += 1
            total_cost += path_cost
        return total_flow, total_cost


class _MaxFlow:
    """Dinic 最大流，用于廉价的配对数可行性预判。"""

    def __init__(self, node_count):
        self.node_count = node_count
        self.graph = [[] for _ in range(node_count)]

    def add_edge(self, source, target):
        forward = [target, 1, len(self.graph[target])]
        backward = [source, 0, len(self.graph[source])]
        self.graph[source].append(forward)
        self.graph[target].append(backward)

    def max_flow(self, source, sink):
        total = 0
        while True:
            level = [-1] * self.node_count
            level[source] = 0
            queue = deque([source])
            while queue:
                node = queue.popleft()
                for target, capacity, _ in self.graph[node]:
                    if capacity > 0 and level[target] == -1:
                        level[target] = level[node] + 1
                        queue.append(target)
            if level[sink] == -1:
                return total
            cursor = [0] * self.node_count

            def augment(node, pushed):
                if node == sink:
                    return pushed
                while cursor[node] < len(self.graph[node]):
                    target, capacity, reverse_index = self.graph[node][cursor[node]]
                    if capacity > 0 and level[target] == level[node] + 1:
                        flow = augment(target, min(pushed, capacity))
                        if flow:
                            self.graph[node][cursor[node]][1] -= flow
                            self.graph[target][reverse_index][1] += flow
                            return flow
                    cursor[node] += 1
                return 0

            while True:
                flow = augment(source, 10**9)
                if not flow:
                    break
                total += flow


def _network_from(edges, positive_ids, negative_ids):
    """按可用候选边构造源点→正组→负组→汇点的流网络。"""
    source = 0
    positive_node = {inspection_id: index + 1 for index, inspection_id in enumerate(positive_ids)}
    negative_start = 1 + len(positive_ids)
    negative_node = {inspection_id: negative_start + index
                     for index, inspection_id in enumerate(negative_ids)}
    sink = negative_start + len(negative_ids)
    network = _MinCostFlow(sink + 1)
    for inspection_id in positive_ids:
        network.add_edge(source, positive_node[inspection_id], 1, 0)
    for positive_id, negative_id, gap in edges:
        network.add_edge(positive_node[positive_id], negative_node[negative_id], 1, gap)
    for inspection_id in negative_ids:
        network.add_edge(negative_node[inspection_id], sink, 1, 0)
    return network


def _has_cardinality(edges, positive_ids, negative_ids, required):
    """廉价预判：剩余候选边在给定顶点上能否凑出 required 个配对。"""
    if required == 0:
        return True
    source = 0
    positive_node = {inspection_id: index + 1 for index, inspection_id in enumerate(positive_ids)}
    negative_start = 1 + len(positive_ids)
    negative_node = {inspection_id: negative_start + index
                     for index, inspection_id in enumerate(negative_ids)}
    sink = negative_start + len(negative_ids)
    network = _MaxFlow(sink + 1)
    for inspection_id in positive_ids:
        network.add_edge(source, positive_node[inspection_id])
    for positive_id, negative_id, _ in edges:
        network.add_edge(positive_node[positive_id], negative_node[negative_id])
    for inspection_id in negative_ids:
        network.add_edge(negative_node[inspection_id], sink)
    return network.max_flow(source, sink) >= required


def _match_shelf(records, max_gap=MAX_GAP_DAYS):
    """返回单个库房架位上的配对列表，每张单号至多使用一次。"""
    positives = sorted(
        (row for row in records if row["mildew_observed"]),
        key=lambda row: (row["inspection_date"], row["inspection_id"].casefold()),
    )
    negatives = sorted(
        (row for row in records if not row["mildew_observed"]),
        key=lambda row: (row["inspection_date"], row["inspection_id"].casefold()),
    )
    if not positives or not negatives:
        return []

    positive_ids = [row["inspection_id"] for row in positives]
    negative_ids = [row["inspection_id"] for row in negatives]
    lookup = {row["inspection_id"]: row for row in records}

    candidates = []
    for positive in positives:
        positive_day = _day(positive["inspection_date"])
        for negative in negatives:
            gap = abs(positive_day - _day(negative["inspection_date"]))
            if gap <= max_gap:
                candidates.append((positive["inspection_id"], negative["inspection_id"], gap))
    if not candidates:
        return []

    # 全局目标：最大配对数，以及该配对数下的最小间隔总和。
    if len(candidates) > _SHELF_EDGE_BUDGET:
        raise PairingLimitError(
            f"库房「{records[0]['room']}」架位「{records[0]['shelf']}」的候选配对有 "
            f"{len(candidates)} 条，超出单架位安全预算 {_SHELF_EDGE_BUDGET}；"
            "请按库房或时间段拆分数据批次后再运行。"
        )
    target_network = _network_from(candidates, positive_ids, negative_ids)
    target_flow, target_gap = target_network.run(0, target_network.node_count - 1)

    # 字典序贪心：逐条尝试固定候选边。先做配对数预判，再做最小间隔精校，
    # 两者都通过才说明固定该边后仍可补全为某个最优匹配。
    candidates.sort(key=lambda edge: (edge[0].casefold(), edge[1].casefold()))
    chosen = []
    used_positive = set()
    used_negative = set()
    chosen_gap = 0
    for positive_id, negative_id, gap in candidates:
        if positive_id in used_positive or negative_id in used_negative:
            continue
        remaining_positive = [item for item in positive_ids
                              if item not in used_positive and item != positive_id]
        remaining_negative = [item for item in negative_ids
                              if item not in used_negative and item != negative_id]
        blocked_positive = used_positive | {positive_id}
        blocked_negative = used_negative | {negative_id}
        remaining = [
            edge for edge in candidates
            if edge[0] not in blocked_positive and edge[1] not in blocked_negative
        ]
        need_flow = target_flow - len(chosen) - 1
        if not _has_cardinality(remaining, remaining_positive, remaining_negative, need_flow):
            continue
        network = _network_from(remaining, remaining_positive, remaining_negative)
        residual_flow, residual_gap = network.run(0, network.node_count - 1)
        if not (residual_flow == need_flow and residual_gap == target_gap - chosen_gap - gap):
            continue
        chosen.append((positive_id, negative_id, gap))
        used_positive.add(positive_id)
        used_negative.add(negative_id)
        chosen_gap += gap

    pairs = []
    for positive_id, negative_id, gap in chosen:
        positive = lookup[positive_id]
        negative = lookup[negative_id]
        pairs.append({
            "positive_id": positive_id,
            "negative_id": negative_id,
            "room": positive["room"],
            "shelf": positive["shelf"],
            "positive_date": positive["inspection_date"],
            "negative_date": negative["inspection_date"],
            "gap_days": gap,
        })
    pairs.sort(key=lambda item: (item["room"], item["shelf"],
                                 item["positive_id"].casefold(), item["negative_id"].casefold()))
    return pairs


def build_pairs(records, max_gap=MAX_GAP_DAYS):
    """按库房与架位分组配对，返回确定性的配对列表与配套统计。

    输入为经 :func:`src.domain.analysis.validate_rows` 校验的巡检记录。
    配对只读取巡检日期、库房、架位和霉斑结论，不读取湿度等现场条件。
    """
    grouped = defaultdict(list)
    for row in records:
        grouped[(row["room"], row["shelf"])].append(row)

    pairs = []
    shelves = []
    for (room, shelf), shelf_records in sorted(grouped.items(),
                                               key=lambda item: (item[0][0], item[0][1])):
        shelf_pairs = _match_shelf(shelf_records, max_gap)
        pairs.extend(shelf_pairs)
        positive_total = sum(1 for row in shelf_records if row["mildew_observed"])
        negative_total = len(shelf_records) - positive_total
        used = {key for pair in shelf_pairs for key in (pair["positive_id"], pair["negative_id"])}
        shelves.append({
            "room": room,
            "shelf": shelf,
            "positive_total": positive_total,
            "negative_total": negative_total,
            "pair_count": len(shelf_pairs),
            "unpaired_count": len(shelf_records) - len(used),
        })

    pairs.sort(key=lambda item: (item["room"], item["shelf"],
                                 item["positive_id"].casefold(), item["negative_id"].casefold()))
    paired_ids = {key for pair in pairs for key in (pair["positive_id"], pair["negative_id"])}
    return {
        "max_gap_days": max_gap,
        "pairs": pairs,
        "shelves": shelves,
        "paired_ids": paired_ids,
        "pair_count": len(pairs),
        "paired_record_count": len(paired_ids),
        "total_gap_days": sum(pair["gap_days"] for pair in pairs),
    }
