"""ReviewConsumer —— 队列消费 + 并发信号量 + 单任务失败隔离（任务 8.4）。

对应设计文档「后端并发与队列设计 → 并发控制」与需求 5.7、7.7。

Worker 进程的消费主循环：

    while running:
        tasks = XREADGROUP(reviewer:tasks)        # 阻塞式拉取新任务
        for task in tasks:
            spawn 协程: async with semaphore:       # 限制单进程并发体检数
                try: run_analysis(task); XACK
                except: 仅置该 session failed + 发 error（失败隔离，需求 5.7）

设计要点：

- **并发控制**：``asyncio.Semaphore(REVIEW_MAX_CONCURRENT)`` 限制单 Worker 同时
  执行的体检数，避免无限接单打爆下游 LLM/GitHub 连接与内存（design.md 并发控制）。
- **失败隔离**：每个任务在独立协程 + ``try/except`` 中运行，任一任务异常只影响
  该 session（置 failed + 发 error 事件），绝不波及同进程其它在途任务（需求 5.7）。
- **至少一次语义**：仅在任务处理**完成后**（无论成功或已降级为 failed）才 ``XACK``，
  使 Worker 崩溃时未完成消息保留在 PEL，由 ``XAUTOCLAIM`` 孤儿回收重投。
- **水平扩展**：同一 Consumer Group 下可启动多个 Worker 副本，Redis 自动分配消息，
  无需改代码（见 ``worker_main`` 进程入口）。

_Requirements: 5.7, 7.7_
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from redis.exceptions import RedisError

from ..queue.session_store import SessionStore
from ..queue.task_queue import ConsumedTask, ReclaimedTask, TaskQueue
from .runner import AnalysisRunner

logger = logging.getLogger(__name__)

# 消费主循环拉取任务的阻塞等待时长（毫秒）：无任务时阻塞至多该时长后返回再轮询，
# 使停止信号能被及时响应。
_CONSUME_BLOCK_MS = 5000
# 孤儿回收巡检间隔（秒）：每隔该时长尝试 XAUTOCLAIM 回收崩溃 Worker 遗留的 PEL 消息。
_RECLAIM_INTERVAL_SECONDS = 60.0
# 孤儿消息最小空闲时长（毫秒）：仅回收闲置超过该值的待确认消息。
_RECLAIM_MIN_IDLE_MS = 60000
# Redis 暂时性错误（超时/连接断开）后重试拉取前的退避时长（秒）：
# 避免 Redis 短暂不可用时空转刷屏，同时保证停止信号能被及时响应。
_CONSUME_ERROR_BACKOFF_SECONDS = 1.0


class ReviewConsumer:
    """队列消费者：XREADGROUP 消费 + 并发信号量 + 单任务失败隔离（需求 5.7）。

    典型用法（Worker 进程内）：

        consumer = ReviewConsumer(
            task_queue=task_queue,
            session_store=session_store,
            runner=runner,
            consumer_name="worker-1",
            max_concurrent=settings.review_max_concurrent,
        )
        await consumer.run_forever()   # 收到停止信号后优雅退出
    """

    def __init__(
        self,
        *,
        task_queue: TaskQueue,
        session_store: SessionStore,
        runner: AnalysisRunner,
        consumer_name: str,
        max_concurrent: int = 4,
    ) -> None:
        """初始化消费者。

        Args:
            task_queue: Redis Stream 任务队列（消费 + 确认 + 孤儿回收）。
            session_store: 会话状态存储（失败隔离时兜底置 failed）。
            runner: 单任务执行编排器（GitHub 抓取 → 流水线）。
            consumer_name: 本 Worker 在消费组内的唯一消费者名。
            max_concurrent: 单 Worker 最大并发体检数（信号量容量）。
        """
        self._queue = task_queue
        self._session_store = session_store
        self._runner = runner
        self._consumer_name = consumer_name
        # 单进程最大并发体检数（need >=1）。
        self._max_concurrent = max(1, int(max_concurrent))
        self._sem = asyncio.Semaphore(self._max_concurrent)
        self._stopping = asyncio.Event()
        # 在途任务协程集合，用于优雅停止时等待其完成。
        self._inflight: set[asyncio.Task] = set()

    def stop(self) -> None:
        """请求停止消费主循环（收到进程终止信号时调用）。"""
        self._stopping.set()

    async def run_forever(self) -> None:
        """消费主循环：持续拉取并调度任务，直至收到停止信号（需求 5.7）。

        每轮 ``XREADGROUP`` 拉取新任务后，为每条任务派生一个受信号量约束的独立
        协程处理（失败隔离）；同时周期性触发孤儿回收。收到停止信号后不再拉取新
        任务，并等待在途任务完成后返回。
        """
        logger.info(
            "ReviewConsumer 启动：consumer=%s 并发上限=%d",
            self._consumer_name,
            self._max_concurrent,
        )
        last_reclaim = asyncio.get_event_loop().time()
        try:
            while not self._stopping.is_set():
                # 周期性孤儿回收（XAUTOCLAIM）。
                now = asyncio.get_event_loop().time()
                if now - last_reclaim >= _RECLAIM_INTERVAL_SECONDS:
                    await self._reclaim_orphans()
                    last_reclaim = now

                try:
                    tasks = await self._queue.consume(
                        self._consumer_name, count=1, block_ms=_CONSUME_BLOCK_MS
                    )
                except RedisError:
                    # Redis 暂时性错误（如停止/重启时的读取超时、连接断开）不应终止
                    # 整个 Worker：记录后短暂退避重试，主循环继续（需求 7.7 容错）。
                    # 未 ACK 的在途消息保留在 PEL，由 XAUTOCLAIM 孤儿回收兜底。
                    logger.warning(
                        "从队列拉取任务失败（Redis 暂时不可用），%.1fs 后重试",
                        _CONSUME_ERROR_BACKOFF_SECONDS,
                        exc_info=True,
                    )
                    # 用可被 stop() 打断的等待做退避，保证停止信号及时响应。
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(
                            self._stopping.wait(),
                            timeout=_CONSUME_ERROR_BACKOFF_SECONDS,
                        )
                    continue
                for task in tasks:
                    self._schedule(task)
        finally:
            # 停止拉取后等待在途任务完成，保证不丢任务、不留悬挂协程。
            await self._drain()
            logger.info("ReviewConsumer 已停止：consumer=%s", self._consumer_name)

    async def _reclaim_orphans(self) -> None:
        """回收崩溃 Worker 遗留在 PEL 的孤儿任务并调度重处理。"""
        try:
            orphans = await self._queue.reclaim_orphans(
                self._consumer_name, min_idle_ms=_RECLAIM_MIN_IDLE_MS
            )
        except Exception:  # noqa: BLE001 - 回收失败不应终止主循环
            logger.exception("孤儿任务回收失败（consumer=%s）", self._consumer_name)
            return
        for orphan in orphans:
            self._schedule(orphan)

    def _schedule(self, task: ConsumedTask | ReclaimedTask) -> None:
        """为一条任务派生受信号量约束的处理协程（失败隔离，需求 5.7）。"""
        coro = self._handle(task)
        t = asyncio.create_task(coro)
        self._inflight.add(t)
        t.add_done_callback(self._inflight.discard)

    async def _handle(self, task: ConsumedTask | ReclaimedTask) -> None:
        """处理单条任务：信号量限并发 + try/except 失败隔离（需求 5.7、7.7）。

        - ``async with self._sem`` 限制单进程并发体检数。
        - 单任务异常只影响该 session：runner 内部已发 error + 置 failed；此处
          ``try/except`` 作为兜底，捕获 runner 未处理的意外异常，同样仅置该
          session failed，绝不向上传播影响其它在途任务。
        - 无论成功或失败，最终都 ``XACK`` 该消息（任务已终结，无需重投）。
        """
        message_id = task.message_id
        payload = task.payload
        session_id = str(payload.get("session_id", ""))
        async with self._sem:
            if self._stopping.is_set():
                # 停止过程中不再启动新体检；不 XACK，留待孤儿回收重投。
                logger.info("停止中，跳过任务 %s（session=%s）", message_id, session_id)
                return
            try:
                await self._runner.run(payload)
            except Exception as exc:  # noqa: BLE001 - 单任务失败隔离（需求 5.7）
                logger.exception(
                    "任务执行发生未处理异常，隔离至会话 %s（不影响其它任务）",
                    session_id,
                )
                await self._isolate_failure(session_id, exc)
            finally:
                # 任务已终结（成功或失败降级），确认消息移出 PEL（至少一次语义）。
                await self._ack_safely(message_id)

    async def _isolate_failure(self, session_id: str, exc: Exception) -> None:
        """兜底失败隔离：将该 session 置 failed（runner 未处理的意外异常路径）。"""
        if not session_id:
            return
        try:
            await self._session_store.mark_failed(
                session_id, f"任务执行异常：{exc}"
            )
        except Exception:  # noqa: BLE001 - 状态流转失败仅记录，不再向上抛
            logger.exception("兜底将会话 %s 置 failed 失败", session_id)

    async def _ack_safely(self, message_id: str) -> None:
        """确认消息（XACK）；确认失败仅记录，交由孤儿回收兜底。"""
        try:
            await self._queue.ack(message_id)
        except Exception:  # noqa: BLE001 - ACK 失败有 PEL + XAUTOCLAIM 兜底
            logger.exception("确认消息 %s 失败", message_id)

    async def _drain(self) -> None:
        """等待所有在途任务协程完成（优雅停止）。"""
        if not self._inflight:
            return
        logger.info("等待 %d 个在途任务完成…", len(self._inflight))
        pending = list(self._inflight)
        with contextlib.suppress(Exception):
            await asyncio.gather(*pending, return_exceptions=True)
