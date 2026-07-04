"""Worker 进程入口（任务 8.4）。

本模块是 Reviewer 的 **Worker 进程入口**。依据设计文档「职责边界」，Worker 进程
从 Redis Stream 消费体检任务，执行 GitHub 抓取 + 多 Agent 流水线，把 Progress_Event
发布到 Redis Pub/Sub，并更新会话状态机。Worker **无状态、可水平扩展多副本**——同一
Consumer Group 下启动多个进程，Redis 自动分配消息，无需改代码。

进程装配（``main`` 内）：
  - 执行 fail-fast 配置校验（缺必需 LLM 配置则终止启动，需求 7.3、9.4）。
  - 初始化共享组件：``TaskQueue``（确保消费组存在）、``SessionStore``、
    ``ReviewEventBus``、``LLMProvider``、有界连接池的 GitHub ``httpx.AsyncClient``。
  - 构造 ``AnalysisRunner`` 与 ``ReviewConsumer``，运行消费主循环。
  - 注册 SIGINT/SIGTERM 信号处理，收到后请求消费者优雅停止并释放全部连接。

运行：``python -m app.worker_main``（可同时启动多份实现水平扩展）。

_Requirements: 5.7, 7.7_
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket

import httpx

from app.logging_config import setup_logging

# 在业务模块 import 之前配置日志（Worker 进程标识）。
setup_logging(service_name="worker")

from app.config import get_settings  # noqa: E402
from app.db.database import init_models  # noqa: E402
from app.db.model_config_repo import ModelConfigRepository  # noqa: E402
from app.db.review_repo import ReviewRepository  # noqa: E402
from app.events.event_bus import ReviewEventBus  # noqa: E402
from app.github.client import GITHUB_API_BASE  # noqa: E402
from app.llm.provider import LLMProvider  # noqa: E402
from app.queue.session_store import SessionStore  # noqa: E402
from app.queue.task_queue import REDIS_SOCKET_TIMEOUT_SECONDS, TaskQueue  # noqa: E402
from app.worker.consumer import ReviewConsumer  # noqa: E402
from app.worker.runner import AnalysisRunner  # noqa: E402

logger = logging.getLogger(__name__)

# GitHub 抓取的有界连接池上限：限制单 Worker 对 GitHub 的并发连接总数，
# 避免多任务并发时打爆下游连接（design.md「有界外部并发」）。
_GITHUB_MAX_CONNECTIONS = 20


def _build_consumer_name() -> str:
    """构造消费者名：``{hostname}-{pid}``，保证多副本在消费组内唯一。"""
    return f"{socket.gethostname()}-{os.getpid()}"


async def run_worker() -> None:
    """装配并运行 Worker 进程，直至收到停止信号后优雅退出（需求 5.7、7.7）。"""
    settings = get_settings()  # 触发 fail-fast 校验（缺必需 LLM 配置则抛错终止）

    # 确保 PostgreSQL 表结构存在（体检历史 + 模型配置）。
    await init_models()

    # 任务队列并确保消费组存在（幂等）。
    # Worker 消费主循环用阻塞式 XREADGROUP（block_ms=_CONSUME_BLOCK_MS），必须设置
    # 大于该阻塞窗口的 socket_timeout，否则 redis-py 8.x 会在约 5s socket 读上限处
    # 抛 TimeoutError，使 Worker 每 5s 空转报错、无法正常长阻塞等待任务。
    task_queue = TaskQueue(
        settings.redis_url, socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS
    )
    await task_queue.ensure_group()
    # 会话状态存储复用队列的 Redis 连接。
    session_store = SessionStore(task_queue.redis)
    # 事件总线独立连接，发布 Progress_Event 到 Pub/Sub。
    event_bus = ReviewEventBus.from_url(settings.redis_url)
    # LLM_Provider（初始化即校验配置；缺失则抛错终止 Worker）。
    llm = LLMProvider.from_settings(settings)
    # GitHub 抓取的有界连接池 httpx 客户端（供所有并发任务复用）。
    http_client = httpx.AsyncClient(
        base_url=GITHUB_API_BASE,
        limits=httpx.Limits(max_connections=_GITHUB_MAX_CONNECTIONS),
    )

    runner = AnalysisRunner(
        session_store=session_store,
        event_bus=event_bus,
        llm=llm,
        settings=settings,
        http_client=http_client,
        task_queue=task_queue,
        review_repo=ReviewRepository(),
        model_config_repo=ModelConfigRepository(),
    )
    consumer_name = _build_consumer_name()
    consumer = ReviewConsumer(
        task_queue=task_queue,
        session_store=session_store,
        runner=runner,
        consumer_name=consumer_name,
        max_concurrent=settings.review_max_concurrent,
    )

    # 注册信号处理：收到 SIGINT/SIGTERM 请求消费者优雅停止。
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, consumer.stop)
        except NotImplementedError:  # pragma: no cover - 某些平台（Windows）不支持
            pass

    logger.info(
        "Worker 进程启动：consumer=%s Redis=%s 并发上限=%d",
        consumer_name,
        settings.redis_url,
        settings.review_max_concurrent,
    )
    try:
        await consumer.run_forever()
    finally:
        # 释放全部外部连接。
        await http_client.aclose()
        await llm.close()
        await event_bus.close()
        await task_queue.close()
        logger.info("Worker 进程已关闭：全部连接已释放")


def main() -> None:
    """同步入口：``python -m app.worker_main``。"""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
