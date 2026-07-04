"""SessionStore（Redis Hash 会话状态存储）单元测试（任务 5.2）。

使用 fakeredis 的 asyncio 客户端替代真实 Redis，验证会话创建、读取、
状态流转（queued→running→completed/failed）与超时巡检置 failed。
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from app.queue.session_store import (
    InvalidStateTransitionError,
    SessionNotFoundError,
    SessionStatus,
    SessionStore,
)


@pytest_asyncio.fixture
async def store():
    """基于 fakeredis 的 SessionStore（decode_responses=True）。"""
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield SessionStore(client)
    await client.aclose()


async def _create(store: SessionStore, session_id="s1"):
    return await store.create_session(
        session_id=session_id,
        repo_url="https://github.com/owner/repo",
        owner="owner",
        repo="repo",
    )


@pytest.mark.asyncio
async def test_create_session_initial_queued(store):
    """创建会话初始状态为 queued，字段正确持久化。"""
    session = await _create(store)
    assert session.status is SessionStatus.QUEUED
    assert session.error is None

    loaded = await store.get_session("s1")
    assert loaded is not None
    assert loaded.session_id == "s1"
    assert loaded.owner == "owner"
    assert loaded.repo == "repo"
    assert loaded.status is SessionStatus.QUEUED


@pytest.mark.asyncio
async def test_get_missing_session_returns_none(store):
    """不存在的会话返回 None。"""
    assert await store.get_session("missing") is None


@pytest.mark.asyncio
async def test_full_happy_path_transition(store):
    """queued -> running -> completed 正常流转。"""
    await _create(store)
    running = await store.mark_running("s1")
    assert running.status is SessionStatus.RUNNING

    completed = await store.mark_completed("s1")
    assert completed.status is SessionStatus.COMPLETED

    loaded = await store.get_session("s1")
    assert loaded.status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_mark_failed_records_error(store):
    """置 failed 时记录失败原因。"""
    await _create(store)
    await store.mark_running("s1")
    failed = await store.mark_failed("s1", "抓取超时")
    assert failed.status is SessionStatus.FAILED
    assert failed.error == "抓取超时"

    loaded = await store.get_session("s1")
    assert loaded.status is SessionStatus.FAILED
    assert loaded.error == "抓取超时"


@pytest.mark.asyncio
async def test_queued_can_fail_directly(store):
    """queued 可直接置 failed（入队后超时未消费的孤儿回收）。"""
    await _create(store)
    failed = await store.mark_failed("s1", "孤儿超时")
    assert failed.status is SessionStatus.FAILED


@pytest.mark.asyncio
async def test_invalid_transition_from_terminal(store):
    """终态不可再流转。"""
    await _create(store)
    await store.mark_running("s1")
    await store.mark_completed("s1")
    with pytest.raises(InvalidStateTransitionError):
        await store.mark_running("s1")


@pytest.mark.asyncio
async def test_invalid_transition_queued_to_completed(store):
    """queued 不能直接跳到 completed。"""
    await _create(store)
    with pytest.raises(InvalidStateTransitionError):
        await store.update_status("s1", SessionStatus.COMPLETED)


@pytest.mark.asyncio
async def test_update_status_missing_session_raises(store):
    """对不存在的会话流转抛 SessionNotFoundError。"""
    with pytest.raises(SessionNotFoundError):
        await store.mark_running("nope")


@pytest.mark.asyncio
async def test_idempotent_same_status(store):
    """重复置为当前状态是幂等的（无异常）。"""
    await _create(store)
    await store.mark_running("s1")
    again = await store.update_status("s1", SessionStatus.RUNNING)
    assert again.status is SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_scan_timeouts_marks_stale_running_failed(store):
    """超时巡检将停留过久的 running 会话置 failed。"""
    await _create(store, "stale")
    await store.mark_running("stale")

    # 手动将 updated_at 回拨到很久以前，模拟超时。
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=600)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    await store._redis.hset("reviewer:session:stale", "updated_at", old_ts)

    timed_out = await store.scan_timeouts(timeout_seconds=300)
    assert "stale" in timed_out

    loaded = await store.get_session("stale")
    assert loaded.status is SessionStatus.FAILED
    assert loaded.error is not None


@pytest.mark.asyncio
async def test_scan_timeouts_ignores_fresh_running(store):
    """未超时的 running 会话不被巡检置 failed。"""
    await _create(store, "fresh")
    await store.mark_running("fresh")

    timed_out = await store.scan_timeouts(timeout_seconds=300)
    assert "fresh" not in timed_out

    loaded = await store.get_session("fresh")
    assert loaded.status is SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_scan_timeouts_ignores_non_running(store):
    """queued/completed/failed 会话不受超时巡检影响。"""
    await _create(store, "q")  # 保持 queued
    await _create(store, "done")
    await store.mark_running("done")
    await store.mark_completed("done")

    # 即便回拨时间，非 running 状态也不应被置 failed。
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=600)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    await store._redis.hset("reviewer:session:q", "updated_at", old_ts)
    await store._redis.hset("reviewer:session:done", "updated_at", old_ts)

    timed_out = await store.scan_timeouts(timeout_seconds=300)
    assert timed_out == []
    assert (await store.get_session("q")).status is SessionStatus.QUEUED
    assert (await store.get_session("done")).status is SessionStatus.COMPLETED
