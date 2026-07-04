"""LLM Provider 单元测试（任务 4.3）。

使用 ``httpx.MockTransport`` 注入的替身响应，全程不发起任何真实网络调用，覆盖：
  - 超时配置：连接与响应超时上限 60s（需求 7.1）
  - 缺配置 fail-fast：base_url / api_key / model 任一缺失即抛错（需求 7.3）
  - 瞬态错误 429/500/502/503/504（及超时/连接错误）→ 指数退避重试 2 次
    （合计 3 次尝试，退避 1s → 2s，需求 7.5）
  - 非瞬态错误 400/401/403/404 → 立即停止、不重试（需求 7.6）
  - 重试耗尽后仍失败 → 抛出携带失败原因的错误供上层发 error 事件（需求 7.7）

_Requirements: 7.1, 7.3, 7.5, 7.6, 7.7_
"""

import asyncio

import httpx
import pytest

from app.llm.provider import (
    LLM_BACKOFF_MULTIPLIER,
    LLM_INITIAL_BACKOFF_SECONDS,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
    NON_TRANSIENT_STATUS_CODES,
    TRANSIENT_STATUS_CODES,
    LLMConfigError,
    LLMNonTransientError,
    LLMProvider,
    LLMTransientError,
)

BASE_URL = "https://api.example.com/v1"
API_KEY = "sk-test"
MODEL = "gpt-4o-mini"


def _make_provider(handler) -> LLMProvider:
    """构造一个使用 MockTransport 替身的 LLMProvider（不触网）。

    ``handler`` 是接收 ``httpx.Request`` 并返回 ``httpx.Response`` 的可调用对象，
    也可在其内抛出 ``httpx.TimeoutException`` 等异常以模拟连接层错误。
    """
    provider = LLMProvider(base_url=BASE_URL, api_key=API_KEY, model=MODEL)
    # 用 MockTransport 替换内部真实客户端，保留鉴权头以贴近真实请求。
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    return provider


async def _drain(provider: LLMProvider, **kwargs):
    """完整消费流式响应，返回收集到的 StreamChunk 列表。"""
    chunks = []
    async for chunk in provider.stream_with_tools(
        messages=[{"role": "user", "content": "hi"}], **kwargs
    ):
        chunks.append(chunk)
    return chunks


@pytest.fixture
def no_sleep(monkeypatch):
    """拦截 ``asyncio.sleep``，记录退避时长并跳过真实等待。

    返回记录退避时长的列表，供断言重试次数与退避序列。
    """
    calls: list[float] = []

    async def fake_sleep(delay):
        calls.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return calls


# ---------------------------------------------------------------------------
# 超时配置（需求 7.1）
# ---------------------------------------------------------------------------


def test_default_timeout_is_60s():
    """连接与响应超时上限为 60s（需求 7.1）。"""
    assert LLM_TIMEOUT_SECONDS == 60.0
    provider = LLMProvider(base_url=BASE_URL, api_key=API_KEY, model=MODEL)
    timeout = provider._client.timeout
    assert timeout.connect == 60.0
    assert timeout.read == 60.0


# ---------------------------------------------------------------------------
# 缺配置 fail-fast（需求 7.3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, missing_name",
    [
        ({"base_url": "", "api_key": API_KEY, "model": MODEL}, "LLM_BASE_URL"),
        ({"base_url": BASE_URL, "api_key": "", "model": MODEL}, "LLM_API_KEY"),
        ({"base_url": BASE_URL, "api_key": API_KEY, "model": ""}, "LLM_MODEL"),
        ({"base_url": "   ", "api_key": API_KEY, "model": MODEL}, "LLM_BASE_URL"),
    ],
)
def test_missing_config_fail_fast(kwargs, missing_name):
    """base_url/api_key/model 任一缺失或为空 → 抛 LLMConfigError 并指明缺失项。"""
    with pytest.raises(LLMConfigError) as exc_info:
        LLMProvider(**kwargs)
    assert missing_name in str(exc_info.value)


def test_missing_config_reports_all_missing_items():
    """多项同时缺失时错误信息逐项列出全部缺失配置名（需求 7.3）。"""
    with pytest.raises(LLMConfigError) as exc_info:
        LLMProvider(base_url="", api_key="", model="")
    message = str(exc_info.value)
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        assert name in message


# ---------------------------------------------------------------------------
# 瞬态错误 → 指数退避重试 2 次（需求 7.5）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(TRANSIENT_STATUS_CODES))
async def test_transient_status_retries_twice(status, no_sleep):
    """各瞬态码触发指数退避重试 2 次（合计 3 次尝试），退避序列为 1s → 2s。"""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(status, json={"error": "transient"})

    provider = _make_provider(handler)
    try:
        with pytest.raises(LLMTransientError):
            await _drain(provider)
    finally:
        await provider.close()

    # 1 次初始请求 + 2 次重试
    assert attempts["count"] == LLM_MAX_RETRIES + 1 == 3
    # 两次退避，且指数增长：1s → 2s
    assert no_sleep == [
        LLM_INITIAL_BACKOFF_SECONDS,
        LLM_INITIAL_BACKOFF_SECONDS * LLM_BACKOFF_MULTIPLIER,
    ]


@pytest.mark.asyncio
async def test_timeout_treated_as_transient_and_retried(no_sleep):
    """超时错误被视为瞬态并同样重试 2 次（需求 7.5）。"""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ConnectTimeout("connect timeout", request=request)

    provider = _make_provider(handler)
    try:
        with pytest.raises(LLMTransientError):
            await _drain(provider)
    finally:
        await provider.close()

    assert attempts["count"] == LLM_MAX_RETRIES + 1
    assert len(no_sleep) == LLM_MAX_RETRIES


@pytest.mark.asyncio
async def test_connection_error_treated_as_transient_and_retried(no_sleep):
    """连接层错误被视为瞬态并重试 2 次（需求 7.5）。"""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    provider = _make_provider(handler)
    try:
        with pytest.raises(LLMTransientError):
            await _drain(provider)
    finally:
        await provider.close()

    assert attempts["count"] == LLM_MAX_RETRIES + 1
    assert len(no_sleep) == LLM_MAX_RETRIES


@pytest.mark.asyncio
async def test_transient_then_success_recovers(no_sleep):
    """首次瞬态失败、重试后成功：不再继续重试，正常产出片段。"""
    attempts = {"count": 0}
    sse_body = (
        'data: {"choices":[{"delta":{"content":"你好"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(503, json={"error": "temporarily unavailable"})
        return httpx.Response(200, content=sse_body.encode("utf-8"))

    provider = _make_provider(handler)
    try:
        chunks = await _drain(provider)
    finally:
        await provider.close()

    # 第 1 次瞬态失败 + 第 2 次成功 = 2 次尝试，1 次退避
    assert attempts["count"] == 2
    assert len(no_sleep) == 1
    contents = "".join(c.content for c in chunks if c.response_type == "content")
    assert contents == "你好"


# ---------------------------------------------------------------------------
# 非瞬态错误 → 立即停止、不重试（需求 7.6）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(NON_TRANSIENT_STATUS_CODES))
async def test_non_transient_status_no_retry(status, no_sleep):
    """各非瞬态码立即停止、不重试（仅 1 次尝试、无退避）。"""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(status, json={"error": "client error"})

    provider = _make_provider(handler)
    try:
        with pytest.raises(LLMNonTransientError) as exc_info:
            await _drain(provider)
    finally:
        await provider.close()

    assert attempts["count"] == 1  # 无重试
    assert no_sleep == []  # 无退避
    assert str(status) in str(exc_info.value)
    assert exc_info.value.status_code == status


# ---------------------------------------------------------------------------
# 重试耗尽仍失败 → 抛出携带失败原因的错误（需求 7.7）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_exhausted_raises_error_with_reason(no_sleep):
    """瞬态错误持续存在，重试耗尽后抛出携带失败原因（含状态码）的错误。"""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(503, json={"error": "still down"})

    provider = _make_provider(handler)
    try:
        with pytest.raises(LLMTransientError) as exc_info:
            await _drain(provider)
    finally:
        await provider.close()

    # 耗尽全部尝试
    assert attempts["count"] == LLM_MAX_RETRIES + 1
    # 错误信息携带失败原因，供上层发 error 事件（需求 7.7）
    assert "503" in str(exc_info.value)
    assert exc_info.value.status_code == 503
