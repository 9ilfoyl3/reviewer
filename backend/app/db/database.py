"""异步 SQLAlchemy 引擎与会话工厂（PostgreSQL）。

统一在此构造 async engine 与 sessionmaker，供 API 进程与 Worker 进程共享同一
套 ORM 模型与连接约定。连接地址来自配置 ``DATABASE_URL``（见 ``app.config``）。

设计要点：
- 使用 ``asyncpg`` 驱动（``postgresql+asyncpg://``）。
- 引擎与会话工厂惰性单例，首次使用时按当前配置构造。
- ``init_models()`` 在启动时创建缺失的表（轻量自建表，避免额外迁移工具依赖）。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """ORM 声明式基类。"""


@lru_cache()
def get_engine() -> AsyncEngine:
    """获取全局 async 引擎单例（按 DATABASE_URL 构造）。"""
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )
    logger.info("PostgreSQL 引擎已创建：%s", _mask_dsn(settings.database_url))
    return engine


@lru_cache()
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取全局会话工厂单例。"""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def init_models() -> None:
    """创建缺失的数据表（启动期调用，幂等）。"""
    # 确保模型已注册到 Base.metadata
    from . import models  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 轻量迁移：为既有 review_records 表补齐新增列（create_all 不改已存在的表）。
        # PostgreSQL 支持 ADD COLUMN IF NOT EXISTS，幂等且不影响已有数据。
        from sqlalchemy import text

        await conn.execute(
            text("ALTER TABLE review_records ADD COLUMN IF NOT EXISTS agents_json TEXT")
        )
    logger.info("PostgreSQL 表结构已就绪（review_records / model_configs）")


def _mask_dsn(dsn: str) -> str:
    """隐藏连接串中的口令，便于安全日志输出。"""
    try:
        if "@" in dsn and "//" in dsn:
            scheme, rest = dsn.split("//", 1)
            creds, host = rest.split("@", 1)
            if ":" in creds:
                user = creds.split(":", 1)[0]
                creds = f"{user}:***"
            return f"{scheme}//{creds}@{host}"
    except ValueError:
        pass
    return dsn
