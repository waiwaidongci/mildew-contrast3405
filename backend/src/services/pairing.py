"""霉斑配对的架位级续跑与并发共享。

一次运行先完成全部架位的配对，再进入条件筛查。配对结果逐架位落库到
:class:`PairingProgress`：

- 同一运行的并发 worker 或中断后的重试共享同一份配对（按 ``run_id`` 唯一）；
- 已写库的架位不会被重复处理，后台中断后从下一个未完成架位续跑；
- 写库时校验租约代号（attempt），迟归的旧任务因代号不符而无法改写已存配对。
"""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.constants.enums import PairingState
from src.domain.pairing import _match_shelf
from src.models.pairing import PairingProgress
from src.utils.time import now

log = logging.getLogger(__name__)


def _shelf_order(rows):
    """固定的架位处理顺序：库房、架位字典序，保证续跑位置确定。"""
    keys = sorted({(row["room"], row["shelf"]) for row in rows})
    grouped = {key: [] for key in keys}
    for row in rows:
        grouped[(row["room"], row["shelf"])].append(row)
    return [(key, grouped[key]) for key in keys]


def _get_or_create(db, run_id, attempt):
    """取本运行的配对进度；不存在则建立。并发下唯一约束保证只建一条。"""
    progress = db.scalar(select(PairingProgress).where(PairingProgress.run_id == run_id)
                         .with_for_update())
    if progress is not None:
        return progress
    progress = PairingProgress(run_id=run_id, state=PairingState.RUNNING,
                               completed_shelves=[], pairs=[], attempt=attempt)
    db.add(progress)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        progress = db.scalar(select(PairingProgress).where(PairingProgress.run_id == run_id)
                             .with_for_update())
    return progress


def run_pairing(db, run_id, attempt, rows, max_gap=None):
    """逐架位配对并落库，返回最终的 :class:`PairingProgress`。

    调用方持有该运行的行锁。已完成的架位直接跳过；每完成一个架位就提交一次，
    使中断后的重试能够从断点继续。代号小于已存进度代号的任务视为迟归旧任务，
    原样返回、拒绝写入，从而保护新租约已保存的配对；代号更大的新租约则接管
    半成品进度并续跑。
    """
    from src.domain.pairing import MAX_GAP_DAYS
    max_gap = MAX_GAP_DAYS if max_gap is None else max_gap

    progress = _get_or_create(db, run_id, attempt)
    if progress.attempt > attempt:
        # 迟归的旧任务：进度已由更新的租约接管，保持已存配对不变。
        log.info("Run %s stale attempt %s ignored (current %s)",
                 run_id, attempt, progress.attempt)
        return progress
    # 新租约接管半成品：先把进度代号推进到当前租约。
    progress.attempt = attempt
    if progress.state == PairingState.COMPLETE:
        return progress

    completed = {tuple(item) for item in progress.completed_shelves}
    for (room, shelf), shelf_rows in _shelf_order(rows):
        if (room, shelf) in completed:
            continue
        shelf_pairs = _match_shelf(shelf_rows, max_gap)
        # 计算期间若被更新的租约接管（代号被推进），当前任务立即停手。
        if progress.attempt > attempt:
            return progress
        progress.pairs = [*progress.pairs, *shelf_pairs]
        progress.completed_shelves = [*progress.completed_shelves, [room, shelf]]
        progress.pair_count = len(progress.pairs)
        progress.total_gap_days = sum(pair["gap_days"] for pair in progress.pairs)
        progress.updated_at = now()
        db.commit()
        completed.add((room, shelf))

    if progress.attempt == attempt:
        progress.state = PairingState.COMPLETE
        progress.updated_at = now()
        db.commit()
    return progress


def paired_rows(progress, rows):
    """从全部巡检记录中取出参与配对的单据，保持原始台账顺序。"""
    paired_ids = {key for pair in progress.pairs
                  for key in (pair["positive_id"], pair["negative_id"])}
    return [row for row in rows if row["inspection_id"] in paired_ids]


def pairing_summary(progress, rows):
    """供分析结果与页面对照的配对视图（架位进度、配对明细、统计）。"""
    shelf_lookup = {}
    for pair in progress.pairs:
        key = (pair["room"], pair["shelf"])
        shelf_lookup.setdefault(key, {"room": pair["room"], "shelf": pair["shelf"],
                                      "pair_count": 0})
        shelf_lookup[key]["pair_count"] += 1

    totals = {}
    for row in rows:
        key = (row["room"], row["shelf"])
        entry = totals.setdefault(key, {"positive_total": 0, "negative_total": 0, "record_total": 0})
        entry["record_total"] += 1
        if row["mildew_observed"]:
            entry["positive_total"] += 1
        else:
            entry["negative_total"] += 1

    shelves = []
    for room, shelf in sorted(totals):
        key = (room, shelf)
        entry = totals[key]
        pair_count = shelf_lookup.get(key, {}).get("pair_count", 0)
        shelves.append({
            "room": room,
            "shelf": shelf,
            "positive_total": entry["positive_total"],
            "negative_total": entry["negative_total"],
            "pair_count": pair_count,
            "unpaired_count": entry["record_total"] - 2 * pair_count,
        })

    return {
        "state": progress.state,
        "pair_count": progress.pair_count,
        "paired_record_count": 2 * progress.pair_count,
        "total_gap_days": progress.total_gap_days,
        "completed_shelf_count": len(progress.completed_shelves),
        "shelves": shelves,
        "pairs": progress.pairs,
    }
