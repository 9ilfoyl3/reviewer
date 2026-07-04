"""Worker 侧 ReviewEventBus 单元测试（任务 5.4）。

使用 fakeredis 的 async 客户端替身，覆盖：
  - event_channel 频道命名规则
  - emit 将事件序列化后 PUBLISH 到 reviewer:events:{sid} 频道
  - 订阅者能收到与原事件字段一致的载荷（跨进程往返可解析）
  - 事件按发射顺序到达订阅者（seq 保序语义在传输层不被破坏）
  - 不同会话路由到各自频道，互不串扰
  - PUBLISH 返回订阅者数量，无订阅者时为 0
  - 构造校验：缺 redis_url 且缺 client 时报错

_Requirements: 5.2_
"""

import asyncio

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from app.events.event_bus import CHANNEL_PREFIX, ReviewEventBus, event_channel
from app.events.types import EventType, ProgressEvent


def _make_event(
    *,
    session_id: str = "sess-1",
    seq: int = 0,
    type_: EventType = EventType.THOUGHT,
    agent: str | None = "Code_Auditor",
    data: dict | None = None,
) -> ProgressEvent:
    """构造一条用于测试的 Progress_Event。"""
    return ProgressEvent(
        type=type_,
        session_id=session_id,
        agent=agent,
        seq=seq,
        data=data if data is not None else {"content": "hi", "iteration": 1},
        ts=1234.5,
    )


@pytest_asyncio.fixture
async def bus_and_client():
    """提供一个基于 fakeredis 的 ReviewEventBus 及同源客户端（用于订阅断言）。"""
    client = fake_aioredis.FakeRedis(decode_responses=True)
    bus = ReviewEventBus(client=client)
    yield bus, client
    await bus.close()


async def _subscribe(client, channel: str):
    """订阅频道并返回 pubsub 对象（已消费掉 subscribe 确认消息）。"""
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    # 消费订阅确认消息（type == "subscribe"）
    await pubsub.get_message(timeout=1)
    return pubsub


async def _next_data_message(pubsub, timeout: float = 1.0) -> str | None:
    """读取下一条 message 类型消息的 data 字段（跳过非 message 消息）。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        msg = await pubsub.get_message(
            ignore_subscribe_messages=True, timeout=timeout
        )
        if msg is not None and msg.get("type") == "message":
            return msg["data"]
    return None


# ---- 频道命名 ----


def test_event_channel_format():
    """事件频道名带约定前缀且含 session_id。"""
    assert event_channel("abc") == "reviewer:events:abc"
    assert event_channel("abc").startswith(CHANNEL_PREFIX)


# ---- 构造校验 ----


def test_init_requires_url_or_client():
    """既无 redis_url 也无 client 时构造应报错。"""
    with pytest.raises(ValueError):
        ReviewEventBus()


# ---- emit 发布与订阅往返 ----


@pytest.mark.asyncio
async def test_emit_publishes_serialized_event(bus_and_client):
    """emit 将事件序列化后发布到正确频道，订阅者收到可还原的载荷。"""
    bus, client = bus_and_client
    event = _make_event(session_id="sess-1", seq=3)

    pubsub = await _subscribe(client, event_channel("sess-1"))
    try:
        await bus.emit(event)
        data = await _next_data_message(pubsub)
        assert data is not None
        # 跨进程往返：订阅者收到的 JSON 能解析回等价事件
        received = ProgressEvent.model_validate_json(data)
        assert received == event
    finally:
        await pubsub.aclose()


@pytest.mark.asyncio
async def test_emit_returns_subscriber_count(bus_and_client):
    """PUBLISH 返回订阅者数量：有订阅者为 1。"""
    bus, client = bus_and_client
    pubsub = await _subscribe(client, event_channel("sess-1"))
    try:
        receivers = await bus.emit(_make_event(session_id="sess-1"))
        assert receivers == 1
    finally:
        await pubsub.aclose()


@pytest.mark.asyncio
async def test_emit_returns_zero_without_subscribers(bus_and_client):
    """无订阅者时 PUBLISH 返回 0，属正常情况不报错。"""
    bus, _client = bus_and_client
    receivers = await bus.emit(_make_event(session_id="no-subscriber"))
    assert receivers == 0


# ---- 保序 ----


@pytest.mark.asyncio
async def test_emit_preserves_order(bus_and_client):
    """多条事件按发射顺序到达订阅者，seq 递增不乱序。"""
    bus, client = bus_and_client
    pubsub = await _subscribe(client, event_channel("sess-1"))
    try:
        for seq in range(5):
            await bus.emit(_make_event(session_id="sess-1", seq=seq))

        received_seqs = []
        for _ in range(5):
            data = await _next_data_message(pubsub)
            assert data is not None
            received_seqs.append(ProgressEvent.model_validate_json(data).seq)
        assert received_seqs == [0, 1, 2, 3, 4]
    finally:
        await pubsub.aclose()


# ---- 频道隔离 ----


@pytest.mark.asyncio
async def test_emit_routes_by_session_id(bus_and_client):
    """事件按 session_id 路由到各自频道，跨会话不串扰。"""
    bus, client = bus_and_client
    pubsub_a = await _subscribe(client, event_channel("sess-a"))
    pubsub_b = await _subscribe(client, event_channel("sess-b"))
    try:
        await bus.emit(_make_event(session_id="sess-a", seq=1))

        data_a = await _next_data_message(pubsub_a)
        assert data_a is not None
        assert ProgressEvent.model_validate_json(data_a).session_id == "sess-a"

        # sess-b 频道不应收到 sess-a 的事件
        data_b = await _next_data_message(pubsub_b, timeout=0.3)
        assert data_b is None
    finally:
        await pubsub_a.aclose()
        await pubsub_b.aclose()


# ---- final_report / error 等其它事件类型也可发布 ----


@pytest.mark.asyncio
async def test_emit_error_event(bus_and_client):
    """error 类型事件同样可发布并还原。"""
    bus, client = bus_and_client
    event = _make_event(
        session_id="sess-1",
        seq=9,
        type_=EventType.ERROR,
        agent=None,
        data={"message": "boom", "stage": "fetch"},
    )
    pubsub = await _subscribe(client, event_channel("sess-1"))
    try:
        await bus.emit(event)
        data = await _next_data_message(pubsub)
        assert data is not None
        received = ProgressEvent.model_validate_json(data)
        assert received.type == EventType.ERROR
        assert received.data == {"message": "boom", "stage": "fetch"}
    finally:
        await pubsub.aclose()
