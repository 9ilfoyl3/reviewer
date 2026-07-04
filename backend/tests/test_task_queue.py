"""Redis Stream 任务队列单元测试（任务 5.1）。

使用 fakeredis 的 async 客户端替身，覆盖：
  - 消费组创建幂等（ensure_group 可重复调用）
  - XADD 入队 → XREADGROUP 消费 → XACK 确认闭环
  - `(owner, repo)` 归一化去重：大小写/空白不敏感，命中复用 session_id
  - 去重键释放后可重新入队
  - XAUTOCLAIM 孤儿回收：未 ACK 消息被另一消费者认领

_Requirements: 1.6_
"""

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from app.queue.task_queue import (
    CONSUMER_GROUP,
    STREAM_KEY,
    ConsumedTask,
    TaskQueue,
    dedup_key,
)


@pytest_asyncio.fixture
async def queue():
    """提供一个基于 fakeredis 的 TaskQueue，并预创建消费组。"""
    client = fake_aioredis.FakeRedis(decode_responses=True)
    q = TaskQueue("redis://localhost:6379/0", client=client)
    await q.ensure_group()
    yield q
    await q.close()


# ---- dedup_key 归一化 ----


def test_dedup_key_normalizes_case_and_whitespace():
    """去重键对大小写与首尾空白归一化：等价仓库得到相同键。"""
    assert dedup_key("Owner", "Repo") == dedup_key("owner", "repo")
    assert dedup_key("  owner ", " repo  ") == dedup_key("owner", "repo")


def test_dedup_key_distinguishes_different_repos():
    """不同仓库得到不同键，且避免 ('ab','c') 与 ('a','bc') 冲突。"""
    assert dedup_key("owner", "repo1") != dedup_key("owner", "repo2")
    assert dedup_key("ab", "c") != dedup_key("a", "bc")


def test_dedup_key_has_prefix():
    """去重键带约定前缀。"""
    assert dedup_key("o", "r").startswith("reviewer:dedup:")


# ---- 消费组创建幂等 ----


@pytest.mark.asyncio
async def test_ensure_group_idempotent(queue):
    """ensure_group 可重复调用而不报错（BUSYGROUP 被吞掉）。"""
    await queue.ensure_group()
    await queue.ensure_group()


# ---- 入队 / 消费 / 确认闭环 ----


@pytest.mark.asyncio
async def test_enqueue_then_consume_and_ack(queue):
    """XADD 入队后可被 XREADGROUP 消费，载荷完整，且能成功 XACK。"""
    result = await queue.enqueue("sess-1", "owner", "repo", "https://github.com/owner/repo")
    assert result.deduplicated is False
    assert result.session_id == "sess-1"
    assert result.message_id is not None

    tasks = await queue.consume("worker-1", block_ms=100)
    assert len(tasks) == 1
    task = tasks[0]
    assert isinstance(task, ConsumedTask)
    assert task.payload == {
        "session_id": "sess-1",
        "owner": "owner",
        "repo": "repo",
        "repo_url": "https://github.com/owner/repo",
    }

    acked = await queue.ack(task.message_id)
    assert acked == 1


@pytest.mark.asyncio
async def test_consume_returns_empty_when_no_tasks(queue):
    """无任务时消费在阻塞超时后返回空列表。"""
    tasks = await queue.consume("worker-1", block_ms=50)
    assert tasks == []


# ---- 幂等去重 ----


@pytest.mark.asyncio
async def test_enqueue_deduplicates_active_session(queue):
    """同一仓库重复入队命中去重，复用首个 session_id 且不新增消息。"""
    first = await queue.enqueue("sess-1", "owner", "repo", "https://github.com/owner/repo")
    second = await queue.enqueue("sess-2", "owner", "repo", "https://github.com/owner/repo")

    assert first.deduplicated is False
    assert second.deduplicated is True
    assert second.session_id == "sess-1"  # 复用首个会话
    assert second.message_id is None

    # 只入队了一条消息
    tasks = await queue.consume("worker-1", block_ms=100)
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_enqueue_dedup_is_case_insensitive(queue):
    """去重对 owner/repo 大小写不敏感。"""
    first = await queue.enqueue("sess-1", "Owner", "Repo", "https://github.com/Owner/Repo")
    second = await queue.enqueue("sess-2", "owner", "repo", "https://github.com/owner/repo")
    assert second.deduplicated is True
    assert second.session_id == first.session_id


@pytest.mark.asyncio
async def test_release_dedup_allows_reenqueue(queue):
    """释放去重键后同仓库可重新入队为新会话。"""
    first = await queue.enqueue("sess-1", "owner", "repo", "https://github.com/owner/repo")
    await queue.release_dedup("owner", "repo")
    second = await queue.enqueue("sess-2", "owner", "repo", "https://github.com/owner/repo")

    assert second.deduplicated is False
    assert second.session_id == "sess-2"
    assert second.message_id is not None
    assert second.message_id != first.message_id


@pytest.mark.asyncio
async def test_different_repos_not_deduplicated(queue):
    """不同仓库互不去重，各自独立入队。"""
    a = await queue.enqueue("sess-a", "owner", "repo-a", "https://github.com/owner/repo-a")
    b = await queue.enqueue("sess-b", "owner", "repo-b", "https://github.com/owner/repo-b")
    assert a.deduplicated is False
    assert b.deduplicated is False
    tasks = await queue.consume("worker-1", count=10, block_ms=100)
    assert len(tasks) == 2


# ---- 孤儿回收 ----


@pytest.mark.asyncio
async def test_reclaim_orphans_claims_unacked_message(queue):
    """未 ACK 的消息可被另一消费者通过 XAUTOCLAIM 回收。"""
    await queue.enqueue("sess-1", "owner", "repo", "https://github.com/owner/repo")

    # worker-1 消费但不 ACK，模拟崩溃
    consumed = await queue.consume("worker-1", block_ms=100)
    assert len(consumed) == 1

    # min_idle_ms=0 使消息立即可回收；worker-2 认领
    reclaimed = await queue.reclaim_orphans("worker-2", min_idle_ms=0)
    assert len(reclaimed) == 1
    assert reclaimed[0].message_id == consumed[0].message_id
    assert reclaimed[0].payload["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_reclaim_orphans_empty_when_nothing_pending(queue):
    """PEL 为空时回收返回空列表。"""
    reclaimed = await queue.reclaim_orphans("worker-2", min_idle_ms=0)
    assert reclaimed == []


@pytest.mark.asyncio
async def test_acked_message_not_reclaimed(queue):
    """已 ACK 的消息不再被回收。"""
    await queue.enqueue("sess-1", "owner", "repo", "https://github.com/owner/repo")
    consumed = await queue.consume("worker-1", block_ms=100)
    await queue.ack(consumed[0].message_id)

    reclaimed = await queue.reclaim_orphans("worker-2", min_idle_ms=0)
    assert reclaimed == []


# ---- 常量 sanity ----


def test_queue_constants():
    """Stream 键与消费组名与设计一致。"""
    assert STREAM_KEY == "reviewer:tasks"
    assert CONSUMER_GROUP == "reviewer-workers"
