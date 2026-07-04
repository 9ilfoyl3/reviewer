"""评估历史接口（PostgreSQL 持久化）。

供前端侧边栏展示「每个仓库一段独立历史」，以及回看某次评估的完整报告。

- ``GET    /api/history``        返回按仓库分组的历史（每组含多次评估记录）
- ``GET    /api/history/{id}``   返回某次评估的详情（含完整 Health_Report）
- ``DELETE /api/history/{id}``   删除某条历史记录
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.db.models import ReviewRecord
from app.db.review_repo import ReviewRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/history", tags=["history"])


class HistoryItem(BaseModel):
    """单次评估的摘要（列表用）。"""

    id: str
    repo_url: str
    owner: str
    repo: str
    status: str
    score: int | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class HistoryGroup(BaseModel):
    """按仓库聚合的一组历史。"""

    owner: str
    repo: str
    repo_url: str
    records: list[HistoryItem]


class HistoryDetail(HistoryItem):
    """单次评估详情（含完整报告与多 Agent 协作过程）。"""

    report: dict | None = None
    agents: list | None = None


def _to_item(r: ReviewRecord) -> HistoryItem:
    return HistoryItem(
        id=r.id,
        repo_url=r.repo_url,
        owner=r.owner,
        repo=r.repo,
        status=r.status,
        score=r.score,
        error=r.error,
        created_at=r.created_at.isoformat() if r.created_at else "",
        updated_at=r.updated_at.isoformat() if r.updated_at else "",
    )


def _repo() -> ReviewRepository:
    return ReviewRepository()


@router.get("", response_model=list[HistoryGroup], summary="按仓库分组的评估历史")
async def list_history() -> list[HistoryGroup]:
    records = await _repo().list_records()
    # 按 (owner, repo) 聚合，保持「最近活跃仓库在前」的顺序（records 已按更新时间倒序）。
    groups: dict[tuple[str, str], HistoryGroup] = {}
    order: list[tuple[str, str]] = []
    for r in records:
        key = (r.owner, r.repo)
        if key not in groups:
            groups[key] = HistoryGroup(
                owner=r.owner, repo=r.repo, repo_url=r.repo_url, records=[]
            )
            order.append(key)
        groups[key].records.append(_to_item(r))
    return [groups[k] for k in order]


@router.get("/{record_id}", response_model=HistoryDetail, summary="评估详情")
async def get_history(record_id: str) -> HistoryDetail:
    record = await _repo().get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="评估记录不存在")
    report: dict | None = None
    if record.report_json:
        try:
            report = json.loads(record.report_json)
        except json.JSONDecodeError:
            logger.warning("评估记录 %s 的 report_json 解析失败", record_id)
    agents: list | None = None
    if record.agents_json:
        try:
            agents = json.loads(record.agents_json)
        except json.JSONDecodeError:
            logger.warning("评估记录 %s 的 agents_json 解析失败", record_id)
    item = _to_item(record)
    return HistoryDetail(**item.model_dump(), report=report, agents=agents)


@router.delete(
    "/{record_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除评估记录"
)
async def delete_history(record_id: str) -> None:
    ok = await _repo().delete(record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="评估记录不存在")
