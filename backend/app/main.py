"""FastAPI 应用入口（API 进程，任务 8.3）。

本模块是 Reviewer 的 **API 进程入口**。依据设计文档「职责边界」，API 进程
**只负责**：(1) 接收并校验 URL、解析 owner/repo；(2) 创建 Analysis_Session
状态写入 Redis；(3) 把任务入队；(4) 为 SSE 连接订阅 Redis Pub/Sub 并转发事件。
**不执行任何 GitHub 抓取或 Agent 推理**——那些跑在独立的 Worker 进程
（见 ``app.worker_main``），因此单个体检不会阻塞 API。

生命周期（lifespan）内初始化共享组件并存入 ``app.state``，供路由依赖注入：
  - ``task_queue``：Redis Stream 任务队列（入队 + 去重），启动时确保消费组存在。
  - ``session_store``：会话状态存储（复用 task_queue 的 Redis 连接）。
  - ``redis``：Redis 客户端（供 SSE 端点的 EventBridge 订阅 Pub/Sub）。

启动时执行 fail-fast 配置校验（需求 7.3、9.4）：缺必需 LLM 配置则终止启动。

_Requirements: 1.4, 1.5, 5.1, 5.2, 9.2_
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.logging_config import setup_logging

# 在业务模块 import 之前配置日志（API 进程标识）
setup_logging(service_name="api")

from app.api.analysis import router as analysis_router  # noqa: E402
from app.api.history import router as history_router  # noqa: E402
from app.api.model_config import router as model_config_router  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.database import init_models  # noqa: E402
from app.queue.session_store import SessionStore  # noqa: E402
from app.queue.task_queue import TaskQueue  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：初始化 Redis 队列 / 会话存储，关闭时释放连接。

    先执行 fail-fast 配置校验（缺必需 LLM 配置则抛错终止启动，需求 7.3、9.4），
    再初始化 API 进程所需的共享组件并存入 ``app.state``。
    """
    settings = get_settings()  # 触发 fail-fast 校验（必需项缺失则抛 RuntimeError）

    # 初始化 PostgreSQL 表结构（体检历史 + 模型配置持久化）。
    await init_models()

    # 任务队列（入队 + 去重）并确保消费组存在，供 Worker 消费
    task_queue = TaskQueue(settings.redis_url)
    await task_queue.ensure_group()
    app.state.task_queue = task_queue

    # 会话状态存储复用队列的 Redis 连接（同一 decode_responses=True 连接）
    app.state.session_store = SessionStore(task_queue.redis)

    # SSE 端点的 EventBridge 直接使用该 Redis 客户端订阅 Pub/Sub
    app.state.redis = task_queue.redis

    logger.info("API 进程已就绪：Redis=%s", settings.redis_url)
    try:
        yield
    finally:
        # 关闭底层 Redis 连接（session_store / redis 复用同一连接，关一次即可）
        await task_queue.close()
        logger.info("API 进程已关闭：Redis 连接已释放")


app = FastAPI(
    title="Reviewer API",
    description="多 Agent 协作的 GitHub 仓库体检工具（API 进程）",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS：前端（Vite dev server）跨域访问 API 与 SSE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 analysis 路由（POST /api/analysis 与 GET /api/analysis/{sid}/events）
app.include_router(analysis_router)
# 体检历史（侧边栏 / 回看）与模型配置（前端可配置模型）
app.include_router(history_router)
app.include_router(model_config_router)


@app.get("/")
async def root() -> dict[str, str]:
    """根路径健康检查。"""
    return {"message": "Reviewer API is running"}
