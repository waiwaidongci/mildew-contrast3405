"""对已配对巡检分组，计算条件增长倍数并保留命中证据与成对核对表。"""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from itertools import combinations

from .spec import SPEC


_CONDITION_LABELS = {
    "high_humidity": ("低于高湿切分点", "高湿"),
    "vent_blocked": ("风口未遮挡", "风口遮挡"),
    "maintenance_pending": ("无待结报修", "设施报修待结"),
}
_FIELDS = SPEC["fields"]
_PATTERN_COLUMNS = (
    ("pattern_id", "模式编号"), ("conditions", "伴随条件"), ("size", "条件数"),
    ("positive_count", "正组命中"), ("negative_count", "负组命中"),
    ("positive_support", "正组原始支持度"), ("negative_support", "负组原始支持度"),
    ("positive_smoothed", "正组平滑支持度"), ("negative_smoothed", "负组平滑支持度"),
    ("growth", "增长倍数"),
)
_EVIDENCE_COLUMNS = (("pattern_ids", "命中模式编号"),) + tuple(
    (field["key"], field["label"]) for field in _FIELDS
)
_PAIR_COLUMNS = (
    ("pair_no", "配对序号"), ("room", "库房"), ("shelf", "架位"),
    ("positive_id", "有霉斑巡检单号"), ("positive_date", "有霉斑巡检日期"),
    ("negative_id", "未见霉斑巡检单号"), ("negative_date", "未见霉斑巡检日期"),
    ("gap_days", "间隔天数"),
)
_SHELF_COLUMNS = (
    ("room", "库房"), ("shelf", "架位"),
    ("positive_total", "见霉斑单数"), ("negative_total", "未见霉斑单数"),
    ("pair_count", "已成对数量"), ("unpaired_count", "未配对余数"),
)


def _bounded(value, field):
    allowed = (int,) if field["type"] == "int" else (int, float)
    if type(value) not in allowed or not field["min"] <= value <= field["max"]:
        raise ValueError(f"{field['label']}须为 {field['min']} 至 {field['max']} 范围内的有效数值")
    return value


def validate_rows(rows):
    """校验导入与计算共用的巡检口径，返回独立记录。"""
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("请提供至少一条巡检记录")
    validated = []
    seen = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index} 条巡检记录格式有误")
        record = {}
        for field in _FIELDS:
            key, kind = field["key"], field["type"]
            value = row.get(key)
            try:
                if kind == "string":
                    if not isinstance(value, str) or not 1 <= len(value.strip()) <= field["max_length"]:
                        raise ValueError(f"{field['label']}为空或长度超限")
                    value = value.strip()
                elif kind == "date":
                    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                        raise ValueError
                elif kind == "bool":
                    if type(value) is not bool:
                        raise ValueError(f"{field['label']}须明确填写是或否")
                else:
                    value = _bounded(value, field)
            except ValueError as error:
                detail = "巡检日期须为有效的 YYYY-MM-DD 日期" if kind == "date" else str(error)
                raise ValueError(f"第 {index} 条巡检记录 · {detail}") from None
            record[key] = value
        inspection_id = record["inspection_id"]
        if inspection_id.casefold() in seen:
            raise ValueError(f"第 {index} 条巡检记录 · 巡检单号「{inspection_id}」重复")
        seen.add(inspection_id.casefold())
        validated.append(record)
    return validated


def _row_patterns(row, humidity_threshold):
    conditions = (
        ("high_humidity", row["humidity"] >= humidity_threshold),
        ("vent_blocked", row["vent_blocked"]),
        ("maintenance_pending", row["maintenance_pending"]),
    )
    return tuple((condition,) for condition in conditions) + tuple(combinations(conditions, 2))


def _score_patterns(hits, positives, negatives, params):
    alpha = Fraction(str(params["smoothing"]))
    minimum_growth = Fraction(str(params["min_growth"]))
    eligible = []
    for pattern, counts in hits.items():
        positive, negative = counts[True], counts[False]
        if positive < params["min_positive_count"] or positive + negative < params["min_total_count"]:
            continue
        positive_smoothed = (positive + alpha) / (positives + 2 * alpha)
        negative_smoothed = (negative + alpha) / (negatives + 2 * alpha)
        growth = positive_smoothed / negative_smoothed
        if growth >= minimum_growth:
            eligible.append((pattern, growth, positive, negative, positive_smoothed, negative_smoothed))
    eligible.sort(key=lambda item: (-item[1], -item[2], len(item[0]), item[0]))
    return eligible


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def _pair_tables(pairing):
    """构造可供页面逐对核对单号的配对表与架位进度表。"""
    pair_rows = [
        {
            "pair_no": index,
            "room": pair["room"],
            "shelf": pair["shelf"],
            "positive_id": pair["positive_id"],
            "positive_date": pair["positive_date"],
            "negative_id": pair["negative_id"],
            "negative_date": pair["negative_date"],
            "gap_days": pair["gap_days"],
        }
        for index, pair in enumerate(pairing["pairs"], start=1)
    ]
    shelf_rows = pairing.get("shelves", [])
    return [
        _table("霉斑巡检成对核对", _PAIR_COLUMNS, pair_rows),
        _table("架位配对进度", _SHELF_COLUMNS, shelf_rows),
    ]


def analyze(rows, normalized_params, pairing=None):
    """对已完成配对的巡检计算可重跑的单项及成对条件排名。

    ``rows`` 为全部有效巡检；``pairing`` 为配对服务产出的配对视图。筛查只在
    成对巡检（``paired_ids``）内比较有霉斑与未见霉斑两组，未配对单据不参与。
    """
    records = validate_rows(rows)
    params = {
        field["key"]: _bounded(normalized_params.get(field["key"], field["default"]), field)
        for field in SPEC["params"]
    }
    if pairing is None:
        raise ValueError("配对尚未完成，不能开始条件筛查")
    paired_ids = {key for pair in pairing["pairs"]
                  for key in (pair["positive_id"], pair["negative_id"])}
    paired_records = [row for row in records if row["inspection_id"] in paired_ids]

    total = len(paired_records)
    positives = sum(row["mildew_observed"] for row in paired_records)
    negatives = total - positives
    threshold = params["humidity_threshold"]
    patterns = []
    evidence = []
    if positives and negatives:
        hits = defaultdict(Counter)
        patterns_by_id = {}
        for row in paired_records:
            matching = _row_patterns(row, threshold)
            patterns_by_id[row["inspection_id"]] = matching
            for pattern in matching:
                hits[pattern][row["mildew_observed"]] += 1

        pattern_ids = {}
        for rank, score in enumerate(_score_patterns(hits, positives, negatives, params), start=1):
            pattern, growth, positive, negative, positive_smoothed, negative_smoothed = score
            pattern_id = f"P{rank:02d}"
            pattern_ids[pattern] = pattern_id
            patterns.append({
                "pattern_id": pattern_id,
                "conditions": "且".join(_CONDITION_LABELS[field][value] for field, value in pattern),
                "size": len(pattern),
                "positive_count": positive,
                "negative_count": negative,
                "positive_support": positive / positives,
                "negative_support": negative / negatives,
                "positive_smoothed": float(positive_smoothed),
                "negative_smoothed": float(negative_smoothed),
                "growth": float(growth),
            })
        for row in sorted(paired_records, key=lambda row: (row["inspection_date"], row["inspection_id"])):
            matching = sorted(pattern_ids[pattern] for pattern in patterns_by_id[row["inspection_id"]]
                              if pattern in pattern_ids)
            if matching:
                evidence.append({**row, "pattern_ids": "、".join(matching)})

    if not positives or not negatives:
        missing_group = "见霉斑" if not positives else "未见霉斑"
        headline = f"成对巡检中缺少{missing_group}的记录，暂无法比较两组。"
    elif patterns:
        headline = f"基于 {total} 张成对巡检筛出 {len(patterns)} 个伴随模式，{patterns[0]['conditions']}的增长倍数最高。"
    else:
        headline = "成对巡检中没有模式同时达到命中数与增长倍数门槛。"

    pair_tables = _pair_tables(pairing)
    return {
        "state": "warning" if not positives or not negatives or not patterns else "ok",
        "headline": headline,
        "note": (
            f"全部架位配对完成后才开始筛查：共 {pairing['pair_count']} 对、{total} 张成对巡检，"
            f"未配对单据不参与两组比较。湿度达到 {threshold:g}% 归入高湿，平滑量 {params['smoothing']:g}。"
            f"门槛为正组命中至少 {params['min_positive_count']} 条、两组合计至少 {params['min_total_count']} 条、"
            f"增长至少 {params['min_growth']:g} 倍。配对不看湿度，结果反映成对记录的关联，无法据此判定成因。"
        ),
        "metrics": [
            {"label": "配对数量", "value": pairing["pair_count"], "detail": "同库房同架位、间隔四天内的一正一负"},
            {"label": "成对见霉斑巡检", "value": positives, "detail": "正组支持度的分母"},
            {"label": "成对未见霉斑巡检", "value": negatives, "detail": "负组支持度的分母"},
            {"label": "入选模式", "value": len(patterns),
             "detail": f"单项 {sum(item['size'] == 1 for item in patterns)} 个，其余为成对条件"},
            {"label": "命中巡检", "value": len(evidence), "detail": "至少符合一个入选模式，按巡检单号计数"},
        ],
        "tables": [
            pair_tables[0],
            pair_tables[1],
            _table("伴随模式排名", _PATTERN_COLUMNS, patterns),
            _table("巡检证据明细", _EVIDENCE_COLUMNS, evidence),
        ],
        "charts": [{"title": "增长倍数最高的模式", "kind": "bar", "series": [
            {"label": item["pattern_id"], "value": item["growth"]} for item in patterns[:8]
        ]}] if patterns else [],
        "sections": [{"title": "按成对巡检复核", "text": (
            "先在「霉斑巡检成对核对」表按配对序号核对每对的有霉斑与未见霉斑单号、库房架位和日期间隔，"
            "确认一张单只出现一次；再用模式编号在巡检证据或下载的 CSV 中查找命中记录。"
            "同一条巡检可命中多个模式。复核时结合同架位同期记录、连续湿度读数和设施报修单。"
        )}],
        "pairing": {
            "pair_count": pairing["pair_count"],
            "paired_record_count": pairing.get("paired_record_count", 2 * pairing["pair_count"]),
            "total_gap_days": pairing.get("total_gap_days",
                                          sum(pair["gap_days"] for pair in pairing["pairs"])),
            "completed_shelf_count": pairing.get("completed_shelf_count",
                                                 len(pairing.get("shelves", []))),
        },
    }
