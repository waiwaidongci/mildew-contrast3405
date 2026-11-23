"""配对续跑服务的标准库验证：用内存假对象模拟数据库会话与进度行。

运行：python -I tests/test_pairing_service.py
"""

import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "src"))

# 纯标准库环境下桩掉 SQLAlchemy 与项目内依赖，只验证配对服务自身的控制逻辑。
_sqlalchemy = types.ModuleType("sqlalchemy")


class _SelectStub:
    def where(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self


_sqlalchemy.select = lambda *args, **kwargs: _SelectStub()
sys.modules.setdefault("sqlalchemy", _sqlalchemy)
_sqlalchemy_exc = types.ModuleType("sqlalchemy.exc")


class _IntegrityError(Exception):
    pass


_sqlalchemy_exc.IntegrityError = _IntegrityError
sys.modules.setdefault("sqlalchemy.exc", _sqlalchemy_exc)

_models_jobs = types.ModuleType("src.models.jobs")


class _Run:  # 服务内仅用于类型引用，不实例化。
    pass


_models_jobs.Run = _Run
sys.modules.setdefault("src.models.jobs", _models_jobs)
_models_pairing = types.ModuleType("src.models.pairing")


class _PairingProgress:
    run_id = None

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


_models_pairing.PairingProgress = _PairingProgress
sys.modules.setdefault("src.models.pairing", _models_pairing)
_models_source = types.ModuleType("src.models.source")
sys.modules.setdefault("src.models.source", _models_source)

from services import pairing as pairing_service  # noqa: E402


class FakeProgress:
    def __init__(self, run_id, attempt):
        self.run_id = run_id
        self.state = "running"
        self.completed_shelves = []
        self.pairs = []
        self.pair_count = 0
        self.total_gap_days = 0
        self.attempt = attempt
        self.updated_at = None


class FakeSession:
    """最小会话：行锁由调用语义保证，记录提交次数模拟逐架位落库。"""

    def __init__(self):
        self.rows = {}
        self.commits = 0

    def scalar(self, *_args, **_kwargs):
        return self.rows.get("progress")

    def add(self, progress):
        self.rows["progress"] = progress

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def get_progress(self):
        return self.rows.get("progress")


def _rows():
    return [
        {"inspection_id": "P1", "inspection_date": "2026-11-16", "room": "R", "shelf": "A",
         "mildew_observed": True},
        {"inspection_id": "N1", "inspection_date": "2026-11-18", "room": "R", "shelf": "A",
         "mildew_observed": False},
        {"inspection_id": "P2", "inspection_date": "2026-11-17", "room": "R", "shelf": "B",
         "mildew_observed": True},
        {"inspection_id": "N2", "inspection_date": "2026-11-19", "room": "R", "shelf": "B",
         "mildew_observed": False},
        {"inspection_id": "P3", "inspection_date": "2026-11-17", "room": "R", "shelf": "C",
         "mildew_observed": True},
        {"inspection_id": "N3", "inspection_date": "2026-11-20", "room": "R", "shelf": "C",
         "mildew_observed": False},
    ]


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"  ok - {name}")


def test_full_run_completes_all_shelves():
    print("test_full_run_completes_all_shelves")
    db = FakeSession()
    progress = pairing_service.run_pairing(db, run_id=1, attempt=1, rows=_rows())
    check("三个架位全部完成", progress.state == "complete")
    check("配对数为 3", progress.pair_count == 3)
    check("逐架位落库至少提交三次", db.commits >= 3)
    check("完成架位键齐全", {tuple(x) for x in progress.completed_shelves}
          == {("R", "A"), ("R", "B"), ("R", "C")})


def test_resume_skips_completed_shelves():
    print("test_resume_resumes_from_completed_shelves")
    db = FakeSession()
    # 第一次只跑到两个架位（模拟中断）：手工预置半成品进度。
    partial = pairing_service.run_pairing(db, run_id=2, attempt=1, rows=_rows())
    partial.state = "running"
    partial.completed_shelves = [x for x in partial.completed_shelves if tuple(x) != ("R", "C")]
    partial.pairs = [p for p in partial.pairs if (p["room"], p["shelf"]) != ("R", "C")]
    partial.pair_count = len(partial.pairs)
    partial.total_gap_days = sum(p["gap_days"] for p in partial.pairs)
    before_pairs = list(partial.pairs)

    # 新租约（attempt=2）续跑：已完成架位不重算，结果与一次性运行一致。
    resumed = pairing_service.run_pairing(db, run_id=2, attempt=2, rows=_rows())
    check("续跑后状态为完成", resumed.state == "complete")
    check("已存配对未被改写", resumed.pairs[:len(before_pairs)] == before_pairs)
    check("补齐到 3 对", resumed.pair_count == 3)
    fresh = pairing_service.run_pairing(FakeSession(), run_id=9, attempt=1, rows=_rows())
    check("续跑结果与完整运行逐对一致",
          [(p["positive_id"], p["negative_id"]) for p in resumed.pairs]
          == [(p["positive_id"], p["negative_id"]) for p in fresh.pairs])


def test_stale_attempt_cannot_overwrite():
    print("test_stale_attempt_cannot_overwrite")
    db = FakeSession()
    progress = pairing_service.run_pairing(db, run_id=3, attempt=1, rows=_rows())
    # 新租约已把进度代号推进到 2，并改写了部分配对视图。
    progress.attempt = 2
    snapshot_pairs = list(progress.pairs)
    stale = pairing_service.run_pairing(db, run_id=3, attempt=1, rows=_rows())
    check("迟归旧任务读到新代号后原样返回", stale.attempt == 2)
    check("迟归旧任务未改写已存配对", stale.pairs == snapshot_pairs)
    check("迟归旧任务未把状态改回完成之外的值", stale.state == "complete")


def test_summary_and_paired_rows():
    print("test_summary_and_paired_rows")
    db = FakeSession()
    rows = _rows()
    progress = pairing_service.run_pairing(db, run_id=4, attempt=1, rows=rows)
    summary = pairing_service.pairing_summary(progress, rows)
    check("汇总含每个架位", len(summary["shelves"]) == 3)
    check("架位未配对余数为零", all(item["unpaired_count"] == 0 for item in summary["shelves"]))
    paired = pairing_service.paired_rows(progress, rows)
    check("成对记录数等于两倍配对数", len(paired) == 2 * progress.pair_count)
    check("正负组各半", sum(r["mildew_observed"] for r in paired) == progress.pair_count)


if __name__ == "__main__":
    test_full_run_completes_all_shelves()
    test_resume_skips_completed_shelves()
    test_stale_attempt_cannot_overwrite()
    test_summary_and_paired_rows()
    print("all pairing service tests passed")
