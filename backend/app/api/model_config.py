"""模型配置管理接口（简化版，PostgreSQL 持久化）。

对应前端「模型配置」页面，提供 LLM 模型的增删改查与连通性测试。相比 artoo 的
配置项做了精简：仅保留 ``name`` / ``base_url`` / ``model`` / ``api_key`` /
``is_default``。Worker 优先使用默认配置驱动推理，缺省回退环境变量。

- ``GET    /api/model-configs``        列表（不回传 api_key 明文）
- ``POST   /api/model-configs``        新建
- ``PUT    /api/model-configs/{id}``   更新（api_key 留空则不覆盖）
- ``DELETE /api/model-configs/{id}``   删除
- ``POST   /api/model-configs/test``   连通性测试（发一条消息验证配置）
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.db.model_config_repo import ModelConfigRepository
from app.db.models import ModelConfig
from app.llm.provider import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/model-configs", tags=["model-config"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class ModelConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    base_url: str = Field(..., min_length=1, max_length=1024)
    model: str = Field(..., min_length=1, max_length=255)
    api_key: str | None = None
    is_default: bool = False


class ModelConfigUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=255)
    # 留空表示不修改已存密钥。
    api_key: str | None = None
    is_default: bool | None = None


class ModelConfigResponse(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    api_key_set: bool
    is_default: bool
    created_at: str


class TestRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None
    # 编辑已有配置且未重填密钥时，用该 id 从库中补全 api_key。
    config_id: str | None = None


class TestResponse(BaseModel):
    success: bool
    message: str
    reply: str | None = None


def _to_response(c: ModelConfig) -> ModelConfigResponse:
    return ModelConfigResponse(
        id=c.id,
        name=c.name,
        base_url=c.base_url,
        model=c.model,
        api_key_set=bool(c.api_key),
        is_default=c.is_default,
        created_at=c.created_at.isoformat() if c.created_at else "",
    )


def _repo() -> ModelConfigRepository:
    return ModelConfigRepository()


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("", response_model=list[ModelConfigResponse], summary="模型配置列表")
async def list_model_configs() -> list[ModelConfigResponse]:
    configs = await _repo().list_configs()
    return [_to_response(c) for c in configs]


@router.post(
    "",
    response_model=ModelConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="新建模型配置",
)
async def create_model_config(body: ModelConfigCreate) -> ModelConfigResponse:
    config = await _repo().create(
        name=body.name,
        base_url=body.base_url,
        model=body.model,
        api_key=body.api_key,
        is_default=body.is_default,
    )
    return _to_response(config)


@router.put("/{config_id}", response_model=ModelConfigResponse, summary="更新模型配置")
async def update_model_config(
    config_id: str, body: ModelConfigUpdate
) -> ModelConfigResponse:
    config = await _repo().update(
        config_id,
        name=body.name,
        base_url=body.base_url,
        model=body.model,
        api_key=body.api_key,
        is_default=body.is_default,
    )
    if config is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return _to_response(config)


@router.delete(
    "/{config_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除模型配置"
)
async def delete_model_config(config_id: str) -> None:
    ok = await _repo().delete(config_id)
    if not ok:
        raise HTTPException(status_code=404, detail="模型配置不存在")


@router.post("/test", response_model=TestResponse, summary="测试模型连通性")
async def test_model_config(body: TestRequest) -> TestResponse:
    api_key = body.api_key or ""
    # 编辑场景：未重填密钥则从库补全。
    if not api_key and body.config_id:
        existing = await _repo().get(body.config_id)
        if existing and existing.api_key:
            api_key = existing.api_key

    try:
        provider = LLMProvider(base_url=body.base_url, api_key=api_key, model=body.model)
    except LLMProviderError as exc:
        return TestResponse(success=False, message=f"配置无效：{exc}")

    try:
        messages = [{"role": "user", "content": "你好，请回复：测试成功"}]
        reply_parts: list[str] = []
        async for chunk in provider.stream_with_tools(messages, tools=None):
            if chunk.content:
                reply_parts.append(chunk.content)
        reply = "".join(reply_parts).strip()
        return TestResponse(success=True, message="连接成功", reply=reply[:200] or None)
    except Exception as exc:  # noqa: BLE001 - 测试端点统一兜底为失败反馈
        return TestResponse(success=False, message=f"连接失败：{exc}")
    finally:
        await provider.close()
