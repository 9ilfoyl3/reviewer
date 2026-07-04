"""API 侧 EventBridge：订阅 Redis Pub/Sub → 转发为 SSE（任务 5.5）。

SSE 连接活在 API 进程，而 Agent 流水线跑在 Worker 进程，进程内 EventBus
无法直达浏览器。Worker 侧 ``ReviewEventBus`` 将 Progress_Event
``model_dump_json()`` 后 ``PUBLISH`` 到频道 ``reviewer:events:{session_id}``；
本模块在 API 侧用 ``redis.pubsub()`` ``SUBSCRIBE`` 同一频道，把收到的事件
按 ``seq`` 保序转换为 SSE 帧 ``event: {type}\\ndata: {json}\\n\\n`` yield 给
``GET /api/analysis/{sid}/events`` 的响应流。

    Worker  --PUBLISH reviewer:events:{sid}-->  Redis Pub/Sub
                                                     |
    API     <--SUBSCRIBE--------------------------- /
             --SSE(event/data)-->  浏览器 EventSource

设计要点（详见 design.md「EventBus / SSE 跨进程流式推送设计」）：
  - **保序**：单一 Worker 发布者在单一频道上的消息由 Redis 保持 FIFO 投递顺序，
    到达顺序即 ``seq`` 顺序；本模块再以 ``last_seq`` 做防御式去重/乱序丢弃，
    保证前端按序渲染（需求 5.2）。
  - **心跳保活**：用 ``asyncio.wait_for(queue.get(), timeout=15)`` 拉取事件，
    连续 15 秒无事件则发一条 ``heartbeat`` 帧（需求 5.9）。
  - **终止关闭**：收到 ``final_report`` 或 ``error`` 事件后，转发该帧即关闭流并
    停止推送任何后续事件（需求 5.8）。
  - **建流时限**：订阅在建立 SSE 时完成，事件 1 秒内转发（需求 5.1、5.2）。

_Requirements: 5.1, 5.2, 5.8, 5.9_
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

import redis.asyncio as aioredis

from app.events.types import EventType

logger = logging.getLogger(__name__)

# 事件频道前缀：完整频道为 reviewer:events:{session_id}
# 与 Worker 侧 ReviewEventBus 的 PUBLISH 目标一致（design.md 跨进程桥接）。
EVENT_CHANNEL_PREFIX = "reviewer:events:"

# 心跳间隔（秒）：连续无事件超过该时长则发一条 heartbeat 帧（需求 5.9）。
HEARTBEAT_INTERVAL_SECONDS = 15.0

# 收到以下类型事件后关闭 SSE 流并停止推送后续事件（需求 5.8）。
_TERMINAL_TYPES = frozenset({EventType.FINAL_REPORT.value, EventType.ERROR.value})


def event_channel(session_id: str) -> str:
    """计算某会话的 Redis Pub/Sub 事件频道名。

    Args:
        session_id: Analysis_Session 标识。

    Returns:
        形如 ``reviewer:events:{session_id}`` 的频道名。
    """
    return f"{EVENT_CHANNEL_PREFIX}{session_id}"


def _format_sse_frame(event_type: str, data: str, seq: int | None = None) -> str:
    """构造一条 SSE 帧文本 ``[id: {seq}\\n]event: {type}\\ndata: {json}\\n\\n``。

    ``id`` 行携带 ``seq``，供前端断线重连时通过 ``Last-Event-ID`` 续传（需求 8.6
    的重连续传依赖 seq）。``data`` 为事件的原始 JSON 文本，原样透传不二次编码。

    Args:
        event_type: 事件类型（作为 SSE ``event:`` 字段）。
        data: 事件数据的 JSON 文本（作为 SSE ``data:`` 字段）。
        seq: 单调递增序号，写入 SSE ``id:`` 字段；为 None 时省略 id 行（如心跳）。

    Returns:
        以空行结尾的完整 SSE 帧字符串。
    """
    lines = []
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {data}")
    # SSE 规范以空行分隔事件，故以 "\n\n" 结尾
    return "\n".join(lines) + "\n\n"


def _heartbeat_frame() -> str:
    """构造一条心跳 SSE 帧（需求 5.9）。

    心跳由 API 侧生成、不来自 Worker，故不带 seq；data 携带产生时间戳便于调试。
    """
    payload = json.dumps({"ts": time.time()})
    return _format_sse_frame(EventType.HEARTBEAT.value, payload)


class EventBridge:
    """订阅某会话的 Redis Pub/Sub 事件频道并生成 SSE 帧序列（任务 5.5）。

    典型用法（在 SSE 端点中）：

        bridge = EventBridge(redis, session_id)
        return StreamingResponse(bridge.stream(), media_type="text/event-stream")

    ``stream()`` 是一个异步生成器：内部启动一个后台读取协程，把 Pub/Sub 消息
    按到达顺序放入 ``asyncio.Queue``；主循环以 15s 超时从队列取事件，有事件则
    保序转发为 SSE 帧，超时则发心跳；收到终止事件（final_report/error）后转发
    该帧并结束生成器，随后释放订阅资源。
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        session_id: str,
        *,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        """构造桥接器。

        Args:
            redis: 已连接的 ``redis.asyncio.Redis`` 客户端。应以
                ``decode_responses=True`` 创建，使 Pub/Sub 消息为 str。
            session_id: 要订阅的 Analysis_Session。
            heartbeat_interval: 无事件时发送心跳的间隔秒数（默认 15，需求 5.9）。
        """
        self._redis = redis
        self._session_id = session_id
        self._channel = event_channel(session_id)
        self._heartbeat_interval = heartbeat_interval

    async def stream(self) -> AsyncIterator[str]:
        """生成该会话的 SSE 帧序列。

        Yields:
            SSE 帧字符串（``event:``/``data:`` 形式，或心跳帧）。

        行为：
          - 事件按 ``seq`` 保序转发（乱序/重复的较小 seq 被丢弃，需求 5.2）。
          - 连续 ``heartbeat_interval`` 秒无事件发送一条心跳帧（需求 5.9）。
          - 收到 final_report 或 error 帧后转发并终止，之后不再推送（需求 5.8）。
        """
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)

        # 后台读取协程把 Pub/Sub 消息投递到本队列，主循环从队列消费以便用
        # wait_for 实现心跳超时（需求 5.9）。
        queue: asyncio.Queue[str] = asyncio.Queue()
        reader = asyncio.create_task(self._read_into_queue(pubsub, queue))

        # 已转发事件的最大 seq，用于防御式保序（丢弃乱序/重复的更小 seq）。
        last_seq = -1
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        queue.get(), timeout=self._heartbeat_interval
                    )
                except asyncio.TimeoutError:
                    # 15 秒无事件：发送心跳保活（需求 5.9）
                    yield _heartbeat_frame()
                    continue

                frame = self._build_frame(raw, last_seq)
                if frame is None:
                    # 载荷非法或乱序重复，跳过不推送
                    continue

                sse_frame, seq, is_terminal = frame
                if seq is not None:
                    last_seq = seq
                yield sse_frame

                if is_terminal:
                    # 收到 final_report / error：转发后关闭流，停止推送后续事件（需求 5.8）
                    logger.debug(
                        "会话 %s 收到终止事件，关闭 SSE 流", self._session_id
                    )
                    break
        finally:
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                # 后台任务取消或清理异常不应影响关闭流程
                pass
            await self._cleanup(pubsub)

    async def _read_into_queue(
        self, pubsub: aioredis.client.PubSub, queue: asyncio.Queue[str]
    ) -> None:
        """后台协程：持续读取 Pub/Sub 消息并按到达顺序放入队列。

        Redis 对单频道单发布者保持 FIFO 投递顺序，故到达顺序即为发布（seq）顺序。
        仅转发 ``message`` 类型消息的数据部分（订阅确认等控制消息忽略）。
        """
        async for message in pubsub.listen():
            if message is None:
                continue
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if data is None:
                continue
            await queue.put(data if isinstance(data, str) else data.decode("utf-8"))

    def _build_frame(
        self, raw: str, last_seq: int
    ) -> tuple[str, int | None, bool] | None:
        """将一条原始事件 JSON 文本构造为 SSE 帧。

        Args:
            raw: Worker 侧 ``ProgressEvent.model_dump_json()`` 产出的 JSON 文本。
            last_seq: 已转发事件的最大 seq，用于保序丢弃。

        Returns:
            ``(sse_frame, seq, is_terminal)``；当载荷非法或 seq 乱序/重复
            （``seq <= last_seq``）时返回 None 表示跳过。
        """
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("会话 %s 事件 JSON 解析失败，跳过：%s", self._session_id, exc)
            return None

        if not isinstance(event, dict):
            logger.warning("会话 %s 事件载荷非对象，跳过：%r", self._session_id, event)
            return None

        event_type = event.get("type")
        if not event_type:
            logger.warning("会话 %s 事件缺少 type 字段，跳过", self._session_id)
            return None

        seq = event.get("seq")
        if isinstance(seq, int):
            # 防御式保序：丢弃乱序或重复的较小/相等 seq（需求 5.2）
            if seq <= last_seq:
                logger.debug(
                    "会话 %s 丢弃乱序/重复事件 seq=%s（last=%s）",
                    self._session_id,
                    seq,
                    last_seq,
                )
                return None
        else:
            seq = None

        is_terminal = event_type in _TERMINAL_TYPES
        # data 字段原样透传整条事件 JSON，不二次编码
        return _format_sse_frame(event_type, raw, seq), seq, is_terminal

    async def _cleanup(self, pubsub: aioredis.client.PubSub) -> None:
        """取消订阅并关闭 Pub/Sub 资源（幂等，异常仅告警）。"""
        try:
            await pubsub.unsubscribe(self._channel)
        except Exception as exc:  # noqa: BLE001
            logger.debug("会话 %s 取消订阅失败（忽略）：%s", self._session_id, exc)
        try:
            await pubsub.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("会话 %s 关闭 pubsub 失败（忽略）：%s", self._session_id, exc)
