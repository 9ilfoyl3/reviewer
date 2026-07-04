"""ORM 模型：评估历史记录与模型配置。

- ``ReviewRecord``：一次仓库评估的持久化记录。以 ``owner/repo`` 聚合，供前端
  侧边栏按仓库分组展示历史；``report_json`` 保存完整 Health_Report，便于回看。
- ``ModelConfig``：前端可配置的 LLM 模型（简化版，仅名称 / base_url / api_key /
  model / 是否默认）。Worker 优先取默认配置，缺省回退环境变量。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReviewRecord(Base):
    """一次仓库评估的历史记录。"""

    __tablename__ = "review_records"

    # 复用 Analysis_Session 的 session_id 作为主键，天然与运行态一一对应。
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    repo: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # queued / running / completed / failed，与 Redis 会话状态机一致。
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    # 完成后的总分（0–100）；未完成为 None。
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 完整 Health_Report JSON 文本；未完成为 None。
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 多 Agent 协作过程（聚合后的 AgentView 列表）JSON 文本，供刷新/回看时还原流式过程；
    # 未完成或无过程为 None。
    agents_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 失败原因；仅 failed 时有值。
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ModelConfig(Base):
    """前端可配置的 LLM 模型（简化版）。"""

    __tablename__ = "model_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    # 明文存储（本工具为内网/自用工具，简化版不做加密）；响应不回传明文。
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
