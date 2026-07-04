"""模型配置仓储（ModelConfig 的读写）。

供 API 进程（前端 CRUD）与 Worker 进程（取默认配置驱动推理）共用。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from .database import get_session_factory
from .models import ModelConfig

logger = logging.getLogger(__name__)


class ModelConfigRepository:
    """ModelConfig 的持久化仓储。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._sf = session_factory or get_session_factory()

    async def list_configs(self) -> list[ModelConfig]:
        async with self._sf() as session:
            result = await session.execute(
                select(ModelConfig).order_by(ModelConfig.created_at.desc())
            )
            return list(result.scalars().all())

    async def get(self, config_id: str) -> ModelConfig | None:
        async with self._sf() as session:
            return await session.get(ModelConfig, config_id)

    async def get_default(self) -> ModelConfig | None:
        """取默认模型配置；无默认时回退到最近创建的一条。"""
        async with self._sf() as session:
            result = await session.execute(
                select(ModelConfig).where(ModelConfig.is_default == True)  # noqa: E712
            )
            default = result.scalars().first()
            if default is not None:
                return default
            result = await session.execute(
                select(ModelConfig).order_by(ModelConfig.created_at.desc()).limit(1)
            )
            return result.scalars().first()

    async def create(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None,
        is_default: bool,
    ) -> ModelConfig:
        async with self._sf() as session:
            if is_default:
                await self._clear_defaults(session)
            config = ModelConfig(
                id=str(uuid.uuid4()),
                name=name,
                base_url=base_url,
                model=model,
                api_key=api_key or None,
                is_default=is_default,
            )
            session.add(config)
            await session.commit()
            await session.refresh(config)
            return config

    async def update(
        self,
        config_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        is_default: bool | None = None,
    ) -> ModelConfig | None:
        async with self._sf() as session:
            config = await session.get(ModelConfig, config_id)
            if config is None:
                return None
            if is_default:
                await self._clear_defaults(session, exclude_id=config_id)
            if name is not None:
                config.name = name
            if base_url is not None:
                config.base_url = base_url
            if model is not None:
                config.model = model
            # api_key 仅在显式传入非空时更新，避免编辑时清空已存密钥。
            if api_key:
                config.api_key = api_key
            if is_default is not None:
                config.is_default = is_default
            await session.commit()
            await session.refresh(config)
            return config

    async def delete(self, config_id: str) -> bool:
        async with self._sf() as session:
            config = await session.get(ModelConfig, config_id)
            if config is None:
                return False
            await session.delete(config)
            await session.commit()
            return True

    @staticmethod
    async def _clear_defaults(session: AsyncSession, exclude_id: str | None = None) -> None:
        result = await session.execute(
            select(ModelConfig).where(ModelConfig.is_default == True)  # noqa: E712
        )
        for c in result.scalars().all():
            if exclude_id is not None and c.id == exclude_id:
                continue
            c.is_default = False
