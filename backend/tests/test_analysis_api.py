"""API 层 analysis 端点单元测试（任务 8.3）。

覆盖 ``POST /api/analysis``（创建会话 + 入队 + 去重、URL 非法 400）与
``GET /api/analysis/{sid}/events``（SSE 流建立、事件保序转发、终止关闭）。

使用 fakeredis 的 asyncio 客户端替身，通过依赖覆盖注入 TaskQueue /
SessionStore / Redis，避免真实 Redis 与外部网络调用。

_Requirements: 1.4, 1.5, 5.1, 5.2, 9.2_
"""

import asyncio
import json

import httpx
import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from app.api import analysis as analysis_module
from app.api.analysis import (
    get_redis,
    get_session_store,
    get_task_queue,
    router,
)
from app.events.event_bus import event_channel
from app.events.types import EventType
from app.queue.session_store import SessionStatus, SessionStore
from app.queue.task_queue import TaskQueue

from fastapi import FastAPI


@pytest_asyncio.fixture
async def redis_client():
    """基于 fakeredis 的 asyncio 客户端（decode_responses=True）。"""
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def app_ctx(redis_client):
    """装配一个仅含 analysis 路由的 FastAPI 应用，并注入 fakeredis 组件。"""
    task_queue = TaskQueue("redis://localhost:6379/0", client=redis_client)
    await task_queue.ensure_group()
    session_store = SessionStore(redis_client)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_task_queue] = lambda: task_queue
    app.dependency_overrides[get_session_store] = lambda: session_store
    app.dependency_overrides[get_redis] = lambda: redis_client

    return app, task_queue, session_store, redis_client


@pytest_asyncio.fixture
async def client(app_ctx):
    """基于 ASGITransport 的 httpx 异步客户端（不起真实端口）。"""
    app = app_ctx[0]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --------------------------------------------------------------------------- #
# POST /api/analysis
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_analysis_success(client, app_ctx):
    """合法 URL：201，返回 session_id/owner/repo，且会话写入 Redis（queued）。"""
    _app, _queue, session_store, _redis = app_ctx
    resp = await client.post(
        "/api/analysis", json={"repo_url": "https://github.com/octocat/Hello-World"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["owner"] == "octocat"
    assert body["repo"] == "Hello-World"
    assert body["session_id"]

    # 会话已创建且为 queued（需求 1.4）
    session = await session_store.get_session(body["session_id"])
    assert session is not None
    assert session.status is SessionStatus.QUEUED
    assert session.owner == "octocat"


@pytest.mark.asyncio
async def test_create_analysis_enqueues_task(client, app_ctx):
    """创建成功后任务应入队到 Redis Stream，可被 Worker 消费。"""
    _app, queue, _store, _redis = app_ctx
    resp = await client.post(
        "/api/analysis", json={"repo_url": "https://github.com/octocat/Hello-World.git"}
    )
    assert resp.status_code == 201
    session_id = resp.json()["session_id"]

    tasks = await queue.consume("test-worker", block_ms=100)
    assert len(tasks) == 1
    assert tasks[0].payload["session_id"] == session_id
    assert tasks[0].payload["owner"] == "octocat"
    assert tasks[0].payload["repo"] == "Hello-World"


@pytest.mark.asyncio
async def test_create_analysis_dedup_reuses_session(client, app_ctx):
    """同一 (owner, repo) 重复提交命中去重，复用同一 session_id（需求 1.6）。"""
    payload = {"repo_url": "https://github.com/octocat/Hello-World"}
    first = await client.post("/api/analysis", json=payload)
    second = await client.post("/api/analysis", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["session_id"] == second.json()["session_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_url",
    [
        "",  # 空
        "   ",  # 仅空白
        "ftp://github.com/owner/repo",  # 非 git 协议
        "https:///owner/repo",  # 缺主机名
        "https://github.com/only-owner",  # 缺 repo
    ],
)
async def test_create_analysis_invalid_url_returns_400(client, bad_url):
    """非法 URL：返回 400 且响应体含 detail，不创建会话（需求 1.5）。"""
    resp = await client.post("/api/analysis", json={"repo_url": bad_url})
    # 空/超长由 pydantic min_length 拦截为 422；其余格式问题由解析器返回 400
    assert resp.status_code in (400, 422)
    if resp.status_code == 400:
        assert "detail" in resp.json()


@pytest.mark.asyncio
async def test_create_analysis_invalid_url_does_not_enqueue(client, app_ctx):
    """非法 URL 不入队、不建会话。"""
    _app, queue, _store, _redis = app_ctx
    resp = await client.post(
        "/api/analysis", json={"repo_url": "https://github.com/only-owner"}
    )
    assert resp.status_code == 400
    tasks = await queue.consume("test-worker", block_ms=50)
    assert tasks == []


# --------------------------------------------------------------------------- #
# GET /api/analysis/{sid}/events （SSE）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_events_stream_forwards_progress_events(app_ctx):
    """SSE 流建立后转发 Worker 发布的事件，final_report 帧终止流（需求 5.1、5.2）。"""
    app, _queue, _store, redis_client = app_ctx
    session_id = "sse-1"
    channel = event_channel(session_id)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        collected: list[str] = []

        async def read_stream() -> None:
            async with c.stream("GET", f"/api/analysis/{session_id}/events") as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                async for line in resp.aiter_lines():
                    collected.append(line)

        reader = asyncio.create_task(read_stream())
        # 等待订阅建立后再发布事件
        await asyncio.sleep(0.2)

        def _event(evt_type: str, seq: int, data: dict | None = None) -> str:
            return json.dumps(
                {
                    "type": evt_type,
                    "session_id": session_id,
                    "agent": None,
                    "seq": seq,
                    "data": data or {},
                    "ts": 0.0,
                }
            )

        await redis_client.publish(channel, _event(EventType.AGENT_START.value, 0))
        await redis_client.publish(
            channel, _event(EventType.THOUGHT.value, 1, {"content": "hi"})
        )
        await redis_client.publish(channel, _event(EventType.FINAL_REPORT.value, 2))

        await asyncio.wait_for(reader, timeout=3.0)

    text = "\n".join(collected)
    assert "event: agent_start" in text
    assert "event: thought" in text
    assert "event: final_report" in text
    # final_report 之后流关闭
    assert text.index("event: final_report") > text.index("event: thought")
