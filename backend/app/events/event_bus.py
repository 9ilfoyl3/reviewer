"""Worker 侧 ReviewEventBus：将 Progress_Event 发布到 Redis Pub/Sub（任务 5.4）。

SSE 连接活在 API 进程，而多 Agent 流水线跑在 Worker 进程，进程内事件总线
无法直达浏览器。本模块提供跨进程事件桥接的 **发布端**：

    Worker: Agent 流水线 --emit(event)--> PUBLISH reviewer:events:{sid} --> Redis
    API   : EventBridge SUBSCRIBE reviewer:events:{sid} --> SSE --> 浏览器

设计要点（详见 design.md「EventBus / SSE 跨进程流式推送设计 → 跨进程桥接机制」）：
  - ``ReviewEventBus.emit(event)`` 将事件 ``model_dump_json()`` 后 ``PUBLISH``
    到 ``reviewer:events:{session_id}`` 频道。
  - 保持与进程内 EventBus 一致的 ``emit`` 接口，业务代码（Agent、Pipeline）
    无感知跨进程——只管 ``await bus.emit(event)``，底层是内存队列还是 Redis
    Pub/Sub 对其透明。
  - 事件的 ``seq`` 单调递增序号由上层构造事件时赋值，本总线只负责传输，
    保证「按发射顺序传输」的语义在传输层不被破坏。

_Requirements: 5.2_
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from app.events.types import ProgressEvent

logger = logging.getLogger(__name__)

# 事件频道前缀：每个会话独立一个 Pub/Sub 频道 reviewer:events:{session_id}
CHANNEL_PREFIX = "reviewer:events:"


def event_channel(session_id: str) -> str:
    """计算某会话的 Pub/Sub 事件频道名。

    Args:
        session_id: 归属 Analysis_Session 的会话 ID。

    Returns:
        形如 ``reviewer:events:{session_id}`` 的频道名。API 侧 EventBridge
        订阅同名频道即可收到本会话的全部 Progress_Event。
    """
    return f"{CHANNEL_PREFIX}{session_id}"


class ReviewEventBus:
    """Worker 侧事件总线：发布 Progress_Event 到 Redis Pub/Sub（任务 5.4）。

    与 artoo 进程内 EventBus 接口一致（``emit`` 协程），业务代码无需感知事件
    是发往内存订阅者还是跨进程 Redis 频道。

    典型用法（Worker 进程内）：

        bus = ReviewEventBus.from_url(settings.redis_url)
        await bus.emit(ProgressEvent(type=EventType.AGENT_START, ...))
        ...
        await bus.close()

    注：事件的 ``session_id`` 字段决定发布频道；因此同一个总线实例可安全地
    服务于同一 Worker 内多个并发会话的事件发射（各自路由到自己的频道）。
    """

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        client: aioredis.Redis | None = None,
    ) -> None:
        """构造事件总线。

        Args:
            redis_url: Redis 连接地址（当未直接注入 client 时使用）。
            client: 可选的已构造 redis.asyncio 客户端（便于测试注入 fakeredis，
                或与其它组件复用同一连接）。

        Raises:
            ValueError: 既未提供 redis_url 也未注入 client。
        """
        if client is None:
            if not redis_url:
                raise ValueError("ReviewEventBus 需要 redis_url 或已构造的 client 之一")
            # decode_responses=True 使连接读写均为 str，便于 JSON 载荷处理，
            # 与 TaskQueue / SessionStore 的连接约定保持一致。
            client = aioredis.from_url(redis_url, decode_responses=True)
        self._redis = client

    @classmethod
    def from_url(cls, redis_url: str) -> "ReviewEventBus":
        """从 Redis URL 构造事件总线（便于在 Worker 进程入口使用）。"""
        return cls(redis_url)

    @property
    def redis(self) -> aioredis.Redis:
        """暴露底层客户端（供需要复用同一连接的组件使用）。"""
        return self._redis

    async def emit(self, event: ProgressEvent) -> int:
        """发射一条 Progress_Event：序列化后发布到会话的 Pub/Sub 频道。

        将事件 ``model_dump_json()`` 为 UTF-8 JSON 文本后 ``PUBLISH`` 到
        ``reviewer:events:{event.session_id}``。与进程内 EventBus 的 ``emit``
        接口一致，业务代码无感知跨进程（需求 5.2）。

        Args:
            event: 待发布的进度事件。其 ``session_id`` 决定发布频道，``seq``
                由上层保证单调递增以支持前端按序渲染。

        Returns:
            收到该消息的订阅者数量（Redis PUBLISH 返回值）。为 0 表示当前尚无
            API 侧订阅者（例如 SSE 尚未建立）；这是正常情况，不视为错误。
        """
        channel = event_channel(event.session_id)
        payload = event.model_dump_json()
        receivers = await self._redis.publish(channel, payload)
        logger.debug(
            "发射事件 type=%s session=%s seq=%s → 频道 %s（订阅者 %d）",
            event.type.value,
            event.session_id,
            event.seq,
            channel,
            receivers,
        )
        return receivers

    async def close(self) -> None:
        """关闭底层 Redis 连接。"""
        await self._redis.aclose()
