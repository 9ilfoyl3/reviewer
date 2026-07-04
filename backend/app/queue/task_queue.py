"""Redis Stream 任务队列（任务 5.1）。

封装 Redis Stream `reviewer:tasks` 与 Consumer Group `reviewer-workers`，
作为 API 进程与 Worker 进程之间的解耦通道（详见 design.md「后端并发与队列设计」）：

    API  --XADD-->  Stream(reviewer:tasks)  --XREADGROUP-->  Worker
                                             <--XACK--------  Worker（成功）
                                             <--XAUTOCLAIM--  Worker（回收孤儿）

设计要点：
  - **至少一次 + 可回收**：Stream 的持久化 + Consumer Group PEL（Pending Entries List）
    支持崩溃恢复；未 `XACK` 的孤儿消息由 `XAUTOCLAIM` 回收重投。
  - **入队幂等/去重**：以 `(owner, repo)` 归一化哈希为去重键（Redis String
    `reviewer:dedup:{hash}` → session_id），若同一仓库已有处于 `queued`/`running`
    的活跃会话则复用其 session_id，避免重复评估（需求 1.6 后端去重保险）。

本模块只负责队列语义与去重，不负责会话状态存储（见 session_store.py，任务 5.2）
与实际抓取/流水线执行（见 worker/，任务 8.4）。

_Requirements: 1.6_
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

logger = logging.getLogger(__name__)

# ---- Redis 键与消费组常量 ----
STREAM_KEY = "reviewer:tasks"  # 任务队列 Stream
CONSUMER_GROUP = "reviewer-workers"  # 消费组（多 Worker 副本共享）
DEDUP_KEY_PREFIX = "reviewer:dedup:"  # 去重键前缀：reviewer:dedup:{hash} -> session_id

# 去重键的存活上限（秒）：作为兜底，防止会话异常终止后 dedup 键永久残留
# 阻塞后续同仓库入队。正常路径应在会话结束时显式 release_dedup。
DEDUP_TTL_SECONDS = 3600

# Redis 连接 socket 读超时（秒）。必须**大于**消费主循环阻塞读的 block_ms
# （见 consumer._CONSUME_BLOCK_MS=5000ms），否则 redis-py 会在阻塞式
# XREADGROUP 达到 socket 读超时时抛 TimeoutError（redis-py 8.x 下即便
# 未显式设置也存在约 5s 的读上限），导致 Worker 每 5s 空转报错。取 30s 兜底。
REDIS_SOCKET_TIMEOUT_SECONDS = 30.0


def dedup_key(owner: str, repo: str) -> str:
    """基于 `(owner, repo)` 归一化哈希计算去重键。

    归一化规则：去除首尾空白并统一小写（GitHub owner/repo 大小写不敏感），
    以 `\\x00` 分隔避免 ("ab", "c") 与 ("a", "bc") 冲突，取 sha256 十六进制摘要。

    Args:
        owner: 仓库 owner 标识。
        repo: 仓库名。

    Returns:
        形如 `reviewer:dedup:{sha256hex}` 的 Redis 键。
    """
    normalized = f"{owner.strip().lower()}\x00{repo.strip().lower()}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{DEDUP_KEY_PREFIX}{digest}"


@dataclass
class EnqueueResult:
    """入队结果。

    Attributes:
        session_id: 本次任务对应的会话 ID（新建或复用）。
        message_id: 新入队消息的 Stream ID；命中去重复用时为 None（未新入队）。
        deduplicated: 是否命中去重复用了已有活跃会话。
    """

    session_id: str
    message_id: str | None
    deduplicated: bool


@dataclass
class ReclaimedTask:
    """XAUTOCLAIM 回收得到的孤儿任务。

    Attributes:
        message_id: Stream 消息 ID。
        payload: 反序列化后的任务载荷。
    """

    message_id: str
    payload: dict


@dataclass
class ConsumedTask:
    """XREADGROUP 消费得到的任务。

    Attributes:
        message_id: Stream 消息 ID（用于后续 XACK）。
        payload: 反序列化后的任务载荷。
    """

    message_id: str
    payload: dict


class TaskQueue:
    """基于 Redis Stream + Consumer Group 的任务队列封装。

    典型用法：
        queue = TaskQueue(redis_url)
        await queue.ensure_group()               # 启动时确保消费组存在
        # API 侧：
        result = await queue.enqueue(session_id, owner, repo, repo_url)
        # Worker 侧：
        tasks = await queue.consume("worker-1")
        ... 处理 ...
        await queue.ack(task.message_id)
        # 周期性回收孤儿：
        orphans = await queue.reclaim_orphans("worker-1")
    """

    def __init__(
        self,
        redis_url: str,
        *,
        stream_key: str = STREAM_KEY,
        group: str = CONSUMER_GROUP,
        client: aioredis.Redis | None = None,
        socket_timeout: float | None = None,
    ) -> None:
        """初始化队列。

        Args:
            redis_url: Redis 连接地址（当未直接注入 client 时使用）。
            stream_key: 任务 Stream 键，默认 `reviewer:tasks`。
            group: Consumer Group 名，默认 `reviewer-workers`。
            client: 可选的已构造 redis.asyncio 客户端（便于测试注入 fakeredis）。
            socket_timeout: socket 读超时（秒）。**仅 Worker 进程需要设置**：
                Worker 的消费主循环使用阻塞式 XREADGROUP（block_ms 见
                consumer._CONSUME_BLOCK_MS），若不设置，redis-py 8.x 会在约 5s 的
                socket 读上限处抛 TimeoutError，导致 Worker 每 5s 空转报错。故
                Worker 应传入大于 block 窗口的值（见 REDIS_SOCKET_TIMEOUT_SECONDS）。
                API 进程不做阻塞读、且其 Redis 客户端还被 SSE 的长订阅
                （pubsub.listen 可能长时间无消息）复用，因此保持默认 None，避免
                误伤长静默的 SSE 订阅。
        """
        self._stream = stream_key
        self._group = group
        # decode_responses=True 使读写均为 str，便于 JSON 处理；注入 client 时沿用其配置。
        if client is not None:
            self._redis = client
        else:
            kwargs: dict = {"decode_responses": True}
            if socket_timeout is not None:
                kwargs["socket_timeout"] = socket_timeout
            self._redis = aioredis.from_url(redis_url, **kwargs)

    @property
    def redis(self) -> aioredis.Redis:
        """暴露底层客户端（供会话状态存储等复用同一连接）。"""
        return self._redis

    async def ensure_group(self) -> None:
        """确保 Stream 与 Consumer Group 存在（幂等）。

        使用 `mkstream=True` 在 Stream 尚不存在时一并创建。若消费组已存在，
        Redis 抛 `BUSYGROUP`，此处吞掉该错误以保证可重复调用。
        """
        try:
            # id="0" 让新组从 Stream 起始读取历史未消费消息
            await self._redis.xgroup_create(
                name=self._stream, groupname=self._group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                return  # 组已存在，幂等返回
            raise

    async def enqueue(
        self, session_id: str, owner: str, repo: str, repo_url: str
    ) -> EnqueueResult:
        """入队一个评估任务，带 `(owner, repo)` 幂等去重。

        流程：
          1. 计算去重键，用 `SET NX` 原子占位（值为本次 session_id）。
          2. 占位失败说明已有活跃会话 → 读取其 session_id 复用，不重复 XADD。
          3. 占位成功 → XADD 入队并设置去重键 TTL 兜底。

        Args:
            session_id: 调用方为本次任务预分配的会话 ID。
            owner: 仓库 owner。
            repo: 仓库名。
            repo_url: 原始仓库 URL（随载荷传给 Worker）。

        Returns:
            EnqueueResult：新入队则 deduplicated=False 且含 message_id；
            命中去重则 deduplicated=True、session_id 为已存在会话、message_id=None。
        """
        key = dedup_key(owner, repo)
        # SET NX：仅当键不存在时写入，作为分布式去重锁
        acquired = await self._redis.set(key, session_id, nx=True, ex=DEDUP_TTL_SECONDS)
        if not acquired:
            existing = await self._redis.get(key)
            if existing:
                logger.info(
                    "入队去重命中：仓库 %s/%s 已有活跃会话 %s，复用之", owner, repo, existing
                )
                return EnqueueResult(
                    session_id=existing, message_id=None, deduplicated=True
                )
            # 极少数竞态：键在 GET 前恰好过期/被删，退回到正常入队路径
            await self._redis.set(key, session_id, ex=DEDUP_TTL_SECONDS)

        payload = {
            "session_id": session_id,
            "owner": owner,
            "repo": repo,
            "repo_url": repo_url,
        }
        # Stream 字段值须为标量；用单一 data 字段承载 JSON 载荷
        message_id = await self._redis.xadd(self._stream, {"data": json.dumps(payload)})
        logger.info("任务入队：session=%s repo=%s/%s msg=%s", session_id, owner, repo, message_id)
        return EnqueueResult(
            session_id=session_id, message_id=message_id, deduplicated=False
        )

    async def release_dedup(self, owner: str, repo: str) -> None:
        """释放某仓库的去重键（会话进入终态后调用，允许后续重新评估）。"""
        await self._redis.delete(dedup_key(owner, repo))

    async def consume(
        self, consumer_name: str, *, count: int = 1, block_ms: int = 5000
    ) -> list[ConsumedTask]:
        """Worker 从消费组读取新任务（XREADGROUP）。

        使用特殊 ID `>` 读取从未投递给本组任何消费者的新消息。读取后消息进入
        本消费者的 PEL，须在处理成功后 `ack` 以移出 PEL。

        Args:
            consumer_name: 消费者名（同组内唯一标识一个 Worker 实例）。
            count: 单次最多读取的消息数。
            block_ms: 无消息时阻塞等待的毫秒数（0 表示不阻塞立即返回）。

        Returns:
            消费到的任务列表；超时无消息时返回空列表。
        """
        response = await self._redis.xreadgroup(
            groupname=self._group,
            consumername=consumer_name,
            streams={self._stream: ">"},
            count=count,
            block=block_ms,
        )
        return self._parse_stream_response(response)

    async def ack(self, message_id: str) -> int:
        """确认消息处理成功（XACK），将其移出 PEL。

        Returns:
            成功确认的消息数（通常为 1；若消息已被确认或不存在则为 0）。
        """
        return await self._redis.xack(self._stream, self._group, message_id)

    async def reclaim_orphans(
        self,
        consumer_name: str,
        *,
        min_idle_ms: int = 60000,
        count: int = 10,
        start_id: str = "0-0",
    ) -> list[ReclaimedTask]:
        """回收崩溃 Worker 遗留在 PEL 的孤儿消息（XAUTOCLAIM）。

        将空闲时间超过 `min_idle_ms` 的待确认消息转移给 `consumer_name` 重新处理，
        支撑 Worker 崩溃后的孤儿回收（design.md「超时与孤儿回收」）。

        Args:
            consumer_name: 认领这些消息的消费者名。
            min_idle_ms: 最小空闲毫秒数，只回收闲置超过此值的待确认消息。
            count: 单次尝试回收的最大消息数。
            start_id: 游标起点，默认从头 `0-0` 扫描 PEL。

        Returns:
            成功回收并可重新处理的任务列表（已自动跳过底层已删除的消息）。
        """
        result = await self._redis.xautoclaim(
            name=self._stream,
            groupname=self._group,
            consumername=consumer_name,
            min_idle_time=min_idle_ms,
            start_id=start_id,
            count=count,
        )
        # redis-py 的 xautoclaim 返回 (next_cursor, claimed_messages, deleted_ids)
        # 兼容旧版本仅返回 (next_cursor, claimed_messages)
        claimed = result[1] if len(result) >= 2 else []
        reclaimed: list[ReclaimedTask] = []
        for message_id, fields in claimed:
            # XAUTOCLAIM 可能返回底层已被删除消息的占位（fields 为 None），跳过
            if not fields:
                continue
            payload = self._decode_fields(fields)
            if payload is not None:
                reclaimed.append(ReclaimedTask(message_id=message_id, payload=payload))
        if reclaimed:
            logger.info("回收 %d 条孤儿任务给消费者 %s", len(reclaimed), consumer_name)
        return reclaimed

    async def close(self) -> None:
        """关闭底层 Redis 连接。"""
        await self._redis.aclose()

    # ---- 内部辅助 ----

    def _parse_stream_response(self, response: object) -> list[ConsumedTask]:
        """解析 XREADGROUP 返回结构为 ConsumedTask 列表。

        XREADGROUP 返回形如 [(stream_key, [(msg_id, {field: value}), ...])]。
        """
        tasks: list[ConsumedTask] = []
        if not response:
            return tasks
        for _stream_key, messages in response:
            for message_id, fields in messages:
                payload = self._decode_fields(fields)
                if payload is not None:
                    tasks.append(ConsumedTask(message_id=message_id, payload=payload))
        return tasks

    @staticmethod
    def _decode_fields(fields: dict) -> dict | None:
        """从 Stream 消息字段中还原 JSON 载荷。

        Returns:
            解析后的 dict；字段缺失或 JSON 非法时返回 None（记录告警后跳过该消息）。
        """
        raw = fields.get("data")
        if raw is None:
            logger.warning("Stream 消息缺少 data 字段，跳过：%r", fields)
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Stream 消息载荷 JSON 解析失败，跳过：%s", exc)
            return None
