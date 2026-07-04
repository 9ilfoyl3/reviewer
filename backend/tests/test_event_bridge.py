"""API 侧 EventBridge（订阅 Pub/Sub → SSE）单元测试（任务 5.5）。

使用 fakeredis 的 asyncio 客户端替身，覆盖：
  - 事件被转换为 SSE 帧 ``event: {type}\\ndata: {json}\\n\\n`` 并按 seq 保序转发（需求 5.2）
  - 乱序/重复的较小 seq 被丢弃（防御式保序）
  - 连续无事件时发送 heartbeat 帧（需求 5.9）
  - 收到 final_report 或 error 后关闭流、停止推送后续事件（需求 5.8）
  - 非法 JSON 载荷被跳过而不中断流

_Requirements: 5.1, 5.2, 5.8, 5.9_
"""

import asyncio
import json

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from app.events.bridge import (
    EventBridge,
    _format_sse_frame,
    event_channel,
)
from app.events.types import EventType


@pytest_asyncio.fixture
async def redis_client():
    """基于 fakeredis 的 asyncio 客户端（decode_responses=True）。"""
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


def _event_json(session_id: str, event_type: str, seq: int, data: dict | None = None) -> str:
    """构造一条 Worker 侧 ProgressEvent 的 JSON 文本（模拟 model_dump_json 产出）。"""
    return json.dumps(
        {
            "type": event_type,
            "session_id": session_id,
            "agent": None,
            "seq": seq,
            "data": data or {},
            "ts": 0.0,
        }
    )


async def _collect_frames(
    bridge: EventBridge, publisher, *, timeout: float = 3.0
) -> list[str]:
    """驱动 bridge.stream() 收集所有 SSE 帧，并在订阅建立后执行 publisher。

    publisher 为一个无参 async 回调，负责向频道 PUBLISH 事件。stream 在收到
    终止事件后自行结束，从而返回收集到的帧列表。
    """
    frames: list[str] = []

    async def consume() -> None:
        async for frame in bridge.stream():
            frames.append(frame)

    task = asyncio.create_task(consume())
    # 等待 stream() 内部完成 subscribe，避免 publish 早于订阅而丢失
    await asyncio.sleep(0.1)
    await publisher()
    await asyncio.wait_for(task, timeout=timeout)
    return frames


# --------------------------------------------------------------------------- #
# 纯函数：SSE 帧格式
# --------------------------------------------------------------------------- #


def test_format_sse_frame_shape():
    """SSE 帧包含 id/event/data 三行并以空行结尾。"""
    frame = _format_sse_frame("thought", '{"a":1}', seq=3)
    assert frame == 'id: 3\nevent: thought\ndata: {"a":1}\n\n'


def test_format_sse_frame_without_seq_omits_id():
    """无 seq 时省略 id 行（如心跳）。"""
    frame = _format_sse_frame("heartbeat", "{}")
    assert frame == "event: heartbeat\ndata: {}\n\n"


def test_event_channel_naming():
    """事件频道名与 Worker 侧 PUBLISH 目标一致。"""
    assert event_channel("sid-1") == "reviewer:events:sid-1"


# --------------------------------------------------------------------------- #
# 事件转发与保序
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_forwards_events_as_sse_frames_in_order(redis_client):
    """事件按 seq 顺序转发为 SSE 帧，final_report 帧终止流（需求 5.2、5.8）。"""
    session_id = "s1"
    bridge = EventBridge(redis_client, session_id, heartbeat_interval=5.0)
    channel = event_channel(session_id)

    async def publish():
        await redis_client.publish(
            channel, _event_json(session_id, EventType.AGENT_START.value, 0)
        )
        await redis_client.publish(
            channel, _event_json(session_id, EventType.THOUGHT.value, 1, {"content": "hi"})
        )
        await redis_client.publish(
            channel, _event_json(session_id, EventType.FINAL_REPORT.value, 2)
        )

    frames = await _collect_frames(bridge, publish)

    # 过滤掉可能的心跳帧后，按序应为 agent_start / thought / final_report
    event_frames = [f for f in frames if "event: heartbeat" not in f]
    assert len(event_frames) == 3
    assert "event: agent_start" in event_frames[0]
    assert "id: 0" in event_frames[0]
    assert "event: thought" in event_frames[1]
    assert "id: 1" in event_frames[1]
    assert "event: final_report" in event_frames[2]
    assert "id: 2" in event_frames[2]
    # data 行原样透传事件 JSON
    assert '"content": "hi"' in event_frames[1]


@pytest.mark.asyncio
async def test_drops_out_of_order_and_duplicate_seq(redis_client):
    """乱序/重复的较小或相等 seq 被丢弃（防御式保序，需求 5.2）。"""
    session_id = "s2"
    bridge = EventBridge(redis_client, session_id, heartbeat_interval=5.0)
    channel = event_channel(session_id)

    async def publish():
        await redis_client.publish(
            channel, _event_json(session_id, EventType.THOUGHT.value, 5, {"content": "a"})
        )
        # seq=3 < 5，应被丢弃
        await redis_client.publish(
            channel, _event_json(session_id, EventType.THOUGHT.value, 3, {"content": "stale"})
        )
        # seq=5 重复，应被丢弃
        await redis_client.publish(
            channel, _event_json(session_id, EventType.THOUGHT.value, 5, {"content": "dup"})
        )
        await redis_client.publish(
            channel, _event_json(session_id, EventType.ERROR.value, 6)
        )

    frames = await _collect_frames(bridge, publish)
    event_frames = [f for f in frames if "event: heartbeat" not in f]

    assert len(event_frames) == 2
    assert "event: thought" in event_frames[0]
    assert '"content": "a"' in event_frames[0]
    assert "stale" not in "".join(event_frames)
    assert "dup" not in "".join(event_frames)
    assert "event: error" in event_frames[1]


# --------------------------------------------------------------------------- #
# 终止关闭（需求 5.8）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_stops_after_final_report(redis_client):
    """收到 final_report 后关闭流，之后发布的事件不再推送（需求 5.8）。"""
    session_id = "s3"
    bridge = EventBridge(redis_client, session_id, heartbeat_interval=5.0)
    channel = event_channel(session_id)

    async def publish():
        await redis_client.publish(
            channel, _event_json(session_id, EventType.FINAL_REPORT.value, 0)
        )
        # 流应已关闭；后续事件不应出现在结果中
        await redis_client.publish(
            channel, _event_json(session_id, EventType.THOUGHT.value, 1, {"content": "late"})
        )

    frames = await _collect_frames(bridge, publish)
    event_frames = [f for f in frames if "event: heartbeat" not in f]
    assert len(event_frames) == 1
    assert "event: final_report" in event_frames[0]
    assert "late" not in "".join(frames)


@pytest.mark.asyncio
async def test_stops_after_error(redis_client):
    """收到 error 后关闭流（需求 5.8、7.7）。"""
    session_id = "s4"
    bridge = EventBridge(redis_client, session_id, heartbeat_interval=5.0)
    channel = event_channel(session_id)

    async def publish():
        await redis_client.publish(
            channel,
            _event_json(session_id, EventType.ERROR.value, 0, {"message": "boom", "stage": "fetch"}),
        )
        await redis_client.publish(
            channel, _event_json(session_id, EventType.THOUGHT.value, 1)
        )

    frames = await _collect_frames(bridge, publish)
    event_frames = [f for f in frames if "event: heartbeat" not in f]
    assert len(event_frames) == 1
    assert "event: error" in event_frames[0]
    assert '"message": "boom"' in event_frames[0]


# --------------------------------------------------------------------------- #
# 心跳（需求 5.9）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_emits_heartbeat_when_idle(redis_client):
    """连续无事件超过心跳间隔时发送 heartbeat 帧（需求 5.9）。"""
    session_id = "s5"
    bridge = EventBridge(redis_client, session_id, heartbeat_interval=0.15)
    channel = event_channel(session_id)

    frames: list[str] = []

    async def consume() -> None:
        async for frame in bridge.stream():
            frames.append(frame)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    # 空闲足够长以触发至少一次心跳，再发终止事件收尾
    await asyncio.sleep(0.4)
    await redis_client.publish(
        channel, _event_json(session_id, EventType.FINAL_REPORT.value, 0)
    )
    await asyncio.wait_for(task, timeout=3.0)

    heartbeat_frames = [f for f in frames if "event: heartbeat" in f]
    assert len(heartbeat_frames) >= 1
    # 心跳帧不带 id 行
    assert all("id:" not in f for f in heartbeat_frames)


# --------------------------------------------------------------------------- #
# 容错：非法载荷跳过
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_skips_invalid_json_payload(redis_client):
    """非法 JSON 载荷被跳过而不中断流（需求 5.2 保序转发的健壮性）。"""
    session_id = "s6"
    bridge = EventBridge(redis_client, session_id, heartbeat_interval=5.0)
    channel = event_channel(session_id)

    async def publish():
        await redis_client.publish(channel, "not-json{")
        await redis_client.publish(
            channel, _event_json(session_id, EventType.FINAL_REPORT.value, 0)
        )

    frames = await _collect_frames(bridge, publish)
    event_frames = [f for f in frames if "event: heartbeat" not in f]
    assert len(event_frames) == 1
    assert "event: final_report" in event_frames[0]
