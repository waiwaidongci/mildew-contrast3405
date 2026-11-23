"""配对算法的纯标准库验证：逐架位穷举对照 + 业务规则用例。

运行：python -I tests/test_pairing.py
"""

import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from domain.pairing import MAX_GAP_DAYS, PairingLimitError, build_pairs  # noqa: E402


def record(inspection_id, day, mildew, room="东一库", shelf="A-01"):
    return {
        "inspection_id": inspection_id,
        "inspection_date": f"2026-11-{day:02d}",
        "room": room,
        "shelf": shelf,
        "humidity": 50,
        "vent_blocked": False,
        "maintenance_pending": False,
        "mildew_observed": mildew,
    }


def _gap(positive, negative):
    return abs(
        date.fromisoformat(positive["inspection_date"]).toordinal()
        - date.fromisoformat(negative["inspection_date"]).toordinal()
    )


def brute_shelf(records, max_gap=MAX_GAP_DAYS):
    """穷举单个架位的所有可行匹配，按三层目标取字典序最小最优解。"""
    positives = [row for row in records if row["mildew_observed"]]
    negatives = [row for row in records if not row["mildew_observed"]]
    feasible = []
    for positive in positives:
        for negative in negatives:
            gap = _gap(positive, negative)
            if gap <= max_gap:
                feasible.append((positive["inspection_id"], negative["inspection_id"], gap))

    best_key = None
    best = None

    def search(index, used_positive, used_negative, picked, gap_sum):
        nonlocal best_key, best
        if index == len(feasible):
            signature = tuple(
                sorted((a.casefold(), b.casefold()) for a, b, _ in picked)
            )
            # 对数最多（取大）→ 总间隔最短（取小）→ 单号字典序最前（取小）。
            key = (-len(picked), gap_sum, signature)
            if best_key is None or key < best_key:
                best_key = key
                best = (len(picked), gap_sum, list(signature))
            return
        positive_id, negative_id, gap = feasible[index]
        search(index + 1, used_positive, used_negative, picked, gap_sum)
        if positive_id not in used_positive and negative_id not in used_negative:
            search(index + 1, used_positive | {positive_id},
                   used_negative | {negative_id},
                   picked + [(positive_id, negative_id, gap)], gap_sum + gap)

    search(0, set(), set(), [], 0)
    return best


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"  ok - {name}")


def test_business_rules():
    print("test_business_rules")
    rows = [
        # 四天边界：16 与 20 相隔 4 天可配；16 与 21 相隔 5 天不可配。
        record("B-P1", 16, True), record("B-N4", 20, False),
        record("B-N5", 21, False),
        # 不同架位不互通。
        record("C-P1", 17, True, shelf="B-02"),
        record("C-N1", 17, False, shelf="B-03"),
        # 同架位同间隔，单号字典序决定取舍。
        record("D-P2", 18, True, shelf="C-01"),
        record("D-P1", 18, True, shelf="C-01"),
        record("D-N1", 19, False, shelf="C-01"),
        record("D-N2", 19, False, shelf="C-01"),
        # 缺一组的架位不产生配对。
        record("E-P1", 19, True, shelf="D-01"),
    ]
    result = build_pairs(rows)
    boundary = next(p for p in result["pairs"] if p["positive_id"] == "B-P1")
    check("相隔四天（含边界）可配对", boundary["negative_id"] == "B-N4" and boundary["gap_days"] == 4)
    check("超过四天不配对", all(p["positive_id"] != "B-P1" or p["negative_id"] != "B-N5"
                                for p in result["pairs"]))
    check("不同架位不配对", not any(p["positive_id"] == "C-P1" for p in result["pairs"]))
    c_pairs = [p for p in result["pairs"] if p["shelf"] == "C-01"]
    check("同分按单号排序取最前",
          [(p["positive_id"], p["negative_id"]) for p in c_pairs] ==
          [("D-P1", "D-N1"), ("D-P2", "D-N2")])
    used_ids = [key for pair in result["pairs"]
                for key in (pair["positive_id"], pair["negative_id"])]
    check("单张单号只使用一次", len(used_ids) == len(set(used_ids)))
    check("缺负组的架位无配对", all(p["shelf"] != "D-01" for p in result["pairs"]))
    check("配对统计自洽",
          result["paired_record_count"] == 2 * result["pair_count"]
          and result["total_gap_days"] == sum(p["gap_days"] for p in result["pairs"]))


def test_randomized_against_brute_force():
    print("test_randomized_against_brute_force")
    total_shelves = 0
    for seed in (20261123, 7, 99, 4242, 1, 555, 31337, 2024, 88, 13):
        rng = random.Random(seed)
        for _ in range(300):
            rows = []
            for index in range(rng.randint(2, 11)):
                rows.append(record(
                    f"ID-{index:02d}",
                    rng.randint(16, 24),
                    rng.random() < 0.5,
                    room=rng.choice(("东一库", "东二库", "北一库")),
                    shelf=f"S-{rng.randint(1, 3)}",
                ))
            seen = set()
            unique_rows = []
            for row in rows:
                if row["inspection_id"] in seen:
                    continue
                seen.add(row["inspection_id"])
                unique_rows.append(row)

            result = build_pairs(unique_rows)
            got_by_shelf = defaultdict(list)
            for pair in result["pairs"]:
                got_by_shelf[(pair["room"], pair["shelf"])].append(
                    (pair["positive_id"].casefold(), pair["negative_id"].casefold(), pair["gap_days"])
                )
            grouped = defaultdict(list)
            for row in unique_rows:
                grouped[(row["room"], row["shelf"])].append(row)
            for key, shelf_rows in grouped.items():
                total_shelves += 1
                want_count, want_gap, want_signature = brute_shelf(shelf_rows)
                got = sorted((a, b) for a, b, _ in got_by_shelf.get(key, []))
                got_gap = sum(g for _, _, g in got_by_shelf.get(key, []))
                assert len(got) == want_count, (seed, key, got, want_signature)
                assert got_gap == want_gap, (seed, key, got, want_gap)
                assert got == want_signature, (seed, key, got, want_signature)
    check(f"{total_shelves} 个随机架位与穷举最优完全一致", True)


def test_humidity_independent_and_deterministic():
    print("test_humidity_independent_and_deterministic")
    base = [record("H-P1", 16, True), record("H-N1", 18, False)]
    first = build_pairs(base)
    wet = [{**row, "humidity": 99 if row["mildew_observed"] else 30} for row in base]
    check("改湿度不改变配对", build_pairs(wet)["pairs"] == first["pairs"])
    check("重复运行结果完全一致", build_pairs(base)["pairs"] == first["pairs"])


def test_shelf_progress_view():
    print("test_shelf_progress_view")
    rows = [
        record("S-P1", 16, True, shelf="A-01"), record("S-N1", 17, False, shelf="A-01"),
        record("S-N2", 18, False, shelf="A-01"),
        record("S-P2", 16, True, shelf="B-01"),
    ]
    result = build_pairs(rows)
    by_shelf = {(item["room"], item["shelf"]): item for item in result["shelves"]}
    a = by_shelf[("东一库", "A-01")]
    check("架位视角给出未配对余数", a["pair_count"] == 1 and a["unpaired_count"] == 1)
    b = by_shelf[("东一库", "B-01")]
    check("无法配对的架位保留正负组计数", b["positive_total"] == 1 and b["pair_count"] == 0)


def test_shelf_limit_guard():
    print("test_shelf_limit_guard")
    import domain.pairing as pairing
    # 强制极小预算，构造候选边超预算的单架位。
    original = pairing._SHELF_EDGE_BUDGET
    pairing._SHELF_EDGE_BUDGET = 2
    try:
        rows = [
            record("L-P1", 16, True, shelf="Z-01"), record("L-P2", 17, True, shelf="Z-01"),
            record("L-N1", 18, False, shelf="Z-01"), record("L-N2", 19, False, shelf="Z-01"),
        ]
        raised = False
        try:
            build_pairs(rows)
        except PairingLimitError as error:
            raised = True
            assert "Z-01" in str(error)
        check("超预算时显式报错而非静默降级", raised)
    finally:
        pairing._SHELF_EDGE_BUDGET = original


if __name__ == "__main__":
    test_business_rules()
    test_randomized_against_brute_force()
    test_humidity_independent_and_deterministic()
    test_shelf_progress_view()
    test_shelf_limit_guard()
    print("all pairing tests passed")
