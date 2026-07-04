"""评估历史记录仓储（ReviewRecord 的读写）。

供 API 进程（创建记录、列表/详情查询）与 Worker 进程（更新状态/报告/失败）共用。
所有方法自持有会话生命周期（``async with session_factory()``），保持数据流向清晰。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from .database import get_session_factory
from .models import ReviewRecord

logger = logging.getLogger(__name__)


class ReviewRepository:
    """ReviewRecord 的持久化仓储。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._sf = session_factory or get_session_factory()

    async def create(
        self, *, session_id: str, repo_url: str, owner: str, repo: str
    ) -> None:
        """创建一条 queued 历史记录（幂等：已存在则忽略）。"""
        async with self._sf() as session:
            existing = await session.get(ReviewRecord, session_id)
            if existing is not None:
                return
            session.add(
                ReviewRecord(
                    id=session_id,
                    repo_url=repo_url,
                    owner=owner,
                    repo=repo,
                    status="queued",
                )
            )
            await session.commit()
        logger.debug("已创建评估历史记录 %s（%s/%s）", session_id, owner, repo)

    async def mark_running(self, session_id: str) -> None:
        await self._update_status(session_id, "running")

    async def mark_completed(
        self,
        session_id: str,
        *,
        score: int,
        report_json: str,
        agents_json: str | None = None,
    ) -> None:
        """标记完成并写入总分、完整报告与多 Agent 过程。"""
        async with self._sf() as session:
            record = await session.get(ReviewRecord, session_id)
            if record is None:
                return
            record.status = "completed"
            record.score = score
            record.report_json = report_json
            if agents_json is not None:
                record.agents_json = agents_json
            record.error = None
            await session.commit()

    async def mark_failed(
        self, session_id: str, error: str, *, agents_json: str | None = None
    ) -> None:
        async with self._sf() as session:
            record = await session.get(ReviewRecord, session_id)
            if record is None:
                return
            record.status = "failed"
            record.error = error
            if agents_json is not None:
                record.agents_json = agents_json
            await session.commit()

    async def _update_status(self, session_id: str, status: str) -> None:
        async with self._sf() as session:
            record = await session.get(ReviewRecord, session_id)
            if record is None:
                return
            record.status = status
            await session.commit()

    async def list_records(self, limit: int = 200) -> list[ReviewRecord]:
        """按更新时间倒序返回历史记录（供侧边栏分组）。"""
        async with self._sf() as session:
            result = await session.execute(
                select(ReviewRecord)
                .order_by(ReviewRecord.updated_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get(self, session_id: str) -> ReviewRecord | None:
        async with self._sf() as session:
            return await session.get(ReviewRecord, session_id)

    async def delete(self, session_id: str) -> bool:
        async with self._sf() as session:
            record = await session.get(ReviewRecord, session_id)
            if record is None:
                return False
            await session.delete(record)
            await session.commit()
            return True
