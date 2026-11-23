from datetime import datetime
from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import PairingState
from src.utils.time import now


class PairingProgress(Base):
    """一次运行的霉斑配对进度，支撑后台中断后的架位级续跑。

    - ``run_id`` 唯一：同一次运行的并发或重试 worker 共享同一份配对；
    - ``completed_shelves`` 记录已配对完的 (库房, 架位) 键，续跑时跳过；
    - ``pairs`` 为已经定稿的配对，逐架位追加，迟归的旧任务无权改写。
    """

    __tablename__ = "pairing_progress"
    __table_args__ = (UniqueConstraint("run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    state: Mapped[str] = mapped_column(String(20), default=PairingState.RUNNING, index=True)
    completed_shelves: Mapped[list] = mapped_column(JSON, default=list)
    pairs: Mapped[list] = mapped_column(JSON, default=list)
    pair_count: Mapped[int] = mapped_column(default=0)
    total_gap_days: Mapped[int] = mapped_column(default=0)
    attempt: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now)
