"""API 层 analysis 路由：创建评估会话与 SSE 事件流（任务 8.3）。

本模块实现 API 进程对外暴露的两个端点，对应设计文档「API 层」职责边界——
**只负责建会话 + 入队 + SSE 订阅转发，不执行任何 GitHub 抓取或 Agent 推理**：

- ``POST /api/analysis``：校验 Repository_URL 并解析 owner/repo（需求 1.4、1.5），
  为本次评估创建 Analysis_Session 状态写入 Redis（queued），把任务入队到
  Redis Stream，返回 ``session_id`` 供前端订阅事件流。以 ``(owner, repo)`` 去重，
  命中活跃会话时复用其 session_id（需求 1.6 后端去重保险）。
- ``GET /api/analysis/{sid}/events``：为该会话建立一条 SSE 流（需求 5.1，2 秒内
  建立），经 :class:`~app.events.bridge.EventBridge` 订阅 Redis Pub/Sub 频道
  ``reviewer:events:{sid}`` 并把 Progress_Event 保序转发给浏览器（需求 5.2）。

跨进程数据流（详见 design.md「端到端时序图」）：

    浏览器 --POST--> API(校验/解析/建会话/入队) --XADD--> Redis Stream --> Worker
    浏览器 --SSE---> API(EventBridge 订阅) <--PUBLISH-- Redis Pub/Sub <-- Worker

依赖（TaskQueue / SessionStore / Redis 客户端）由 :mod:`app.main` 在应用生命周期
内初始化并存入 ``app.state``，本路由通过依赖注入获取，保持数据流向清晰。

_Requirements: 1.4, 1.5, 5.1, 5.2, 9.2_
"""

from __future__ import annotations

import logging
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.schemas import CreateAnalysisRequest, CreateAnalysisResponse, ErrorResponse
from app.api.url_parse import RepoUrlParseError, parse_repo_url
from app.db.review_repo import ReviewRepository
from app.events.bridge import EventBridge
from app.queue.session_store import SessionStore
from app.queue.task_queue import TaskQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


# --------------------------------------------------------------------------- #
# 依赖注入：从 app.state 获取生命周期内初始化的共享组件
# --------------------------------------------------------------------------- #


def get_task_queue(request: Request) -> TaskQueue:
    """获取应用生命周期内初始化的 TaskQueue（入队 + 去重）。"""
    return request.app.state.task_queue


def get_session_store(request: Request) -> SessionStore:
    """获取应用生命周期内初始化的 SessionStore（会话状态存储）。"""
    return request.app.state.session_store


def get_redis(request: Request) -> aioredis.Redis:
    """获取应用生命周期内初始化的 Redis 客户端（供 EventBridge 订阅 Pub/Sub）。"""
    return request.app.state.redis


# --------------------------------------------------------------------------- #
# POST /api/analysis：创建会话 + 入队
# --------------------------------------------------------------------------- #


@router.post(
    "",
    response_model=CreateAnalysisResponse,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse}},
    summary="创建仓库评估会话",
)
async def create_analysis(
    payload: CreateAnalysisRequest,
    task_queue: TaskQueue = Depends(get_task_queue),
    session_store: SessionStore = Depends(get_session_store),
) -> CreateAnalysisResponse:
    """创建一次仓库评估 Analysis_Session（需求 1.4、1.5、1.6）。

    流程：
      1. 校验 URL 并解析 owner/repo；解析失败返回 HTTP 400 且不创建会话（需求 1.5）。
      2. 预分配 session_id，带 ``(owner, repo)`` 去重入队：命中活跃会话则复用其
         session_id、不重复建会话/入队（需求 1.6）。
      3. 未命中去重时创建会话状态（queued）写入 Redis。
      4. 返回 session_id 与解析出的 owner/repo。

    Args:
        payload: 请求体，携带待评估的 Repository_URL。
        task_queue: 任务队列（入队 + 去重）。
        session_store: 会话状态存储。

    Returns:
        CreateAnalysisResponse：session_id 与 owner/repo。

    Raises:
        HTTPException: URL 校验/解析失败时以 HTTP 400 返回具体原因（需求 1.5），
            响应体形如 ``{"detail": ...}``，与 :class:`ErrorResponse` 一致。
    """
    # 1) 校验 + 解析 owner/repo（需求 1.4、1.5）
    try:
        owner, repo = parse_repo_url(payload.repo_url)
    except RepoUrlParseError as exc:
        # 解析失败：返回 HTTP 400 与原因，且不创建会话（需求 1.5）
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    repo_url = payload.repo_url.strip()

    # 2) 预分配 session_id 并入队（带 (owner, repo) 去重，需求 1.6）
    candidate_id = uuid.uuid4().hex
    result = await task_queue.enqueue(candidate_id, owner, repo, repo_url)

    # 3) 命中去重则复用已有活跃会话，不重复建会话；否则创建 queued 会话状态
    if result.deduplicated:
        logger.info(
            "创建评估去重命中：仓库 %s/%s 复用活跃会话 %s",
            owner,
            repo,
            result.session_id,
        )
    else:
        await session_store.create_session(
            session_id=result.session_id,
            repo_url=repo_url,
            owner=owner,
            repo=repo,
        )
        # 持久化一条评估历史记录（queued）供侧边栏展示；失败不阻断主流程。
        try:
            await ReviewRepository().create(
                session_id=result.session_id,
                repo_url=repo_url,
                owner=owner,
                repo=repo,
            )
        except Exception:  # noqa: BLE001 - 历史落库失败不影响评估本身
            logger.exception("创建评估历史记录失败：会话 %s", result.session_id)
        logger.info(
            "创建评估会话 %s：仓库 %s/%s 已入队",
            result.session_id,
            owner,
            repo,
        )

    return CreateAnalysisResponse(session_id=result.session_id, owner=owner, repo=repo)


# --------------------------------------------------------------------------- #
# GET /api/analysis/{sid}/events：SSE 事件流
# --------------------------------------------------------------------------- #


@router.get(
    "/{sid}/events",
    summary="订阅评估进度 SSE 事件流",
)
async def analysis_events(
    sid: str,
    redis: aioredis.Redis = Depends(get_redis),
) -> StreamingResponse:
    """建立会话 ``sid`` 的 SSE 流并转发 Progress_Event（需求 5.1、5.2）。

    经 :class:`EventBridge` 订阅 Redis Pub/Sub 频道 ``reviewer:events:{sid}``，
    把 Worker 发布的事件按 ``seq`` 保序转换为 SSE 帧推送给浏览器；无事件时按
    心跳间隔发送 heartbeat 保活，收到 final_report/error 后关闭流（由 EventBridge
    负责，需求 5.8、5.9）。订阅在建流时立即完成，满足 2 秒内建立 SSE 流（需求 5.1）。

    Args:
        sid: 目标 Analysis_Session 标识。
        redis: Redis 客户端（用于 Pub/Sub 订阅）。

    Returns:
        media_type 为 ``text/event-stream`` 的流式响应。
    """
    bridge = EventBridge(redis, sid)
    # SSE 响应头：禁用缓存与代理缓冲，保证事件即时逐帧到达浏览器
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        bridge.stream(),
        media_type="text/event-stream",
        headers=headers,
    )
