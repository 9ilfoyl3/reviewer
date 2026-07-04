"""LLM Provider 模型调用层（任务 4.1）。

`LLMProvider` 通过 HTTP 调用 OpenAI 兼容的 `/v1/chat/completions` 接口驱动
各 Agent 的推理（需求 7），沿用 artoo 的流式 + function-calling 模式。

本模块聚焦任务 4.1 的职责：
  - 基于 ``httpx.AsyncClient`` 调用 OpenAI 兼容 ``/v1/chat/completions``，
    连接与响应超时上限 60s（需求 7.1）。
  - ``stream_with_tools(messages, tools, temperature)`` 流式接收，逐片段产出
    ``StreamChunk``（需求 7.4）。
  - 从配置读取 ``base_url`` / ``api_key`` / ``model``，初始化时校验缺失则
    中止推理相关初始化（需求 7.2、7.3）。

重试分流策略（任务 4.2，需求 7.5–7.7）：
  - 瞬态错误 429/500/502/503/504（及超时 / 连接错误）→ 指数退避重试，
    初始退避 1s、每次翻倍，最多重试 2 次（需求 7.5）。
  - 非瞬态错误 400/401/403/404 → 立即停止、不重试（需求 7.6）。
  - 重试耗尽仍失败 → 抛出携带失败原因的 ``LLMTransientError``，供上层
    （Worker/Agent 流水线）据以发射 error 类型 Progress_Event（需求 7.7）。

单次 HTTP 请求被抽取为 ``_post_stream``，重试分流逻辑在其外层的
``_post_stream_with_retry`` 中实现；``stream_with_tools`` 经由后者消费流，
使重试对上层解析逻辑透明。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# 单次请求连接与响应超时上限（需求 7.1）
LLM_TIMEOUT_SECONDS = 60.0

# 瞬态错误码：指数退避重试（需求 7.5）
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# 非瞬态错误码：立即停止、不重试（需求 7.6）
NON_TRANSIENT_STATUS_CODES = frozenset({400, 401, 403, 404})

# 重试分流策略参数（需求 7.5）：
#   - 瞬态错误最多重试 2 次（初始尝试之外的额外尝试次数）。
#   - 初始退避间隔 1s，每次重试后翻倍（1s → 2s）。
LLM_MAX_RETRIES = 2
LLM_INITIAL_BACKOFF_SECONDS = 1.0
LLM_BACKOFF_MULTIPLIER = 2.0


class LLMConfigError(RuntimeError):
    """LLM_Provider 初始化配置缺失时抛出（需求 7.3）。

    base_url / api_key / model 任一缺失或为空则中止推理相关初始化。
    """


class LLMProviderError(RuntimeError):
    """LLM_Provider 请求失败的基类错误。"""


class LLMTransientError(LLMProviderError):
    """瞬态错误（HTTP 429/500/502/503/504 或连接错误）。

    任务 4.2 将据此进行指数退避重试（需求 7.5）。
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LLMNonTransientError(LLMProviderError):
    """非瞬态错误（HTTP 400/401/403/404）。

    任务 4.2 将据此立即停止、不重试（需求 7.6）。
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LLMToolCall(BaseModel):
    """一次工具调用（function calling）的归一化结构。"""

    id: str
    function_name: str
    arguments: str  # JSON 字符串形式的参数


class StreamChunk(BaseModel):
    """流式响应的单个片段。

    对应 OpenAI 兼容流式返回中的一次 ``delta``，被上层 ReAct 循环逐片段消费：
    - ``content`` 增量文本作为 ``thought`` 事件逐 token 发射（需求 7.4）。
    - ``tool_calls`` 在流结束时携带累积组装好的完整工具调用。
    - ``finish_reason`` 标识本次生成的结束原因（stop / tool_calls / ...）。
    - ``response_type`` 区分片段类型（content / tool_call），便于上层分流处理。
    """

    content: str = ""
    tool_calls: list[LLMToolCall] | None = None
    finish_reason: str = ""
    response_type: str = "content"  # "content" | "tool_call"


class LLMProvider:
    """OpenAI 兼容的 LLM 客户端（流式 + function calling）。

    通过 ``httpx.AsyncClient`` 调用 ``{base_url}/chat/completions``，连接与响应
    超时上限 60s（需求 7.1）。初始化时校验 base_url / api_key / model，任一缺失
    则抛 ``LLMConfigError`` 中止推理相关初始化（需求 7.2、7.3）。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = LLM_TIMEOUT_SECONDS,
    ):
        """初始化 LLM 客户端并做 fail-fast 配置校验。

        Args:
            base_url: OpenAI 兼容基础地址（如 ``https://host/v1``），
                      代码会自动拼接 ``/chat/completions``。
            api_key: API 密钥。
            model: 模型名称。
            timeout: 连接与响应超时上限（秒），默认 60s（需求 7.1）。

        Raises:
            LLMConfigError: base_url / api_key / model 任一缺失或为空时抛出，
                            逐项打印缺失配置项名称（需求 7.3）。
        """
        missing = [
            name
            for name, value in (
                ("LLM_BASE_URL", base_url),
                ("LLM_API_KEY", api_key),
                ("LLM_MODEL", model),
            )
            if not value or not str(value).strip()
        ]
        if missing:
            for name in missing:
                logger.error("LLM 配置缺失：%s，无法初始化 Agent 推理功能", name)
            raise LLMConfigError(
                "LLM_Provider 初始化失败：缺少配置项 "
                + "、".join(missing)
                + "（需求 7.3，中止推理相关初始化）。"
            )

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

        headers = {"Authorization": f"Bearer {api_key}"}
        # 连接与响应超时统一为 60s（需求 7.1）
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=timeout),
            headers=headers,
        )
        logger.info(
            "LLMProvider 初始化完成: base_url=%s, model=%s, timeout=%.1fs",
            self.base_url,
            self.model,
            timeout,
        )

    @classmethod
    def from_settings(cls, settings) -> "LLMProvider":
        """从应用配置构造 LLMProvider（需求 7.2）。

        Args:
            settings: 具备 ``llm_base_url`` / ``llm_api_key`` / ``llm_model`` 属性
                      的配置对象（见 ``app.config.Settings``）。
        """
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )

    def _build_payload(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        temperature: float,
        stream: bool,
    ) -> dict:
        """构造 OpenAI 兼容请求体。"""
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        return payload

    async def stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[StreamChunk]:
        """流式调用并逐片段产出 ``StreamChunk``（需求 7.4）。

        以 OpenAI 兼容 ``stream=true`` + ``tools`` 参数发起请求，解析 SSE 中的
        content 增量与 tool_call delta，逐片段 yield ``StreamChunk``：
        - content 增量 → ``response_type="content"``，供上层作为 thought 逐段发射。
        - 流结束时累积组装完整 tool_calls → ``response_type="tool_call"``。

        Args:
            messages: 对话消息列表。
            tools: OpenAI 格式的工具定义列表，可为空。
            temperature: 采样温度。

        Yields:
            StreamChunk 片段。

        Raises:
            LLMTransientError: 瞬态错误（429/5xx 或连接错误），供 4.2 重试。
            LLMNonTransientError: 非瞬态错误（4xx），供 4.2 立即停止。
        """
        payload = self._build_payload(
            messages, tools, temperature, stream=True
        )
        # 按 index 累积 tool_call delta
        tool_call_map: dict[int, dict] = {}

        async for line in self._post_stream_with_retry(payload):
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                # 跳过无法解析的 SSE 行，保持流健壮
                continue

            choices = chunk.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            finish_reason = choices[0].get("finish_reason") or ""

            if not isinstance(delta, dict):
                delta = {}

            # 累积 tool_calls delta
            delta_tool_calls = delta.get("tool_calls")
            if delta_tool_calls:
                for tc_delta in delta_tool_calls:
                    idx = tc_delta.get("index", 0)
                    entry = tool_call_map.setdefault(
                        idx, {"id": "", "function_name": "", "arguments": ""}
                    )
                    if tc_delta.get("id"):
                        entry["id"] = tc_delta["id"]
                    func_data = tc_delta.get("function", {}) or {}
                    if func_data.get("name"):
                        entry["function_name"] = func_data["name"]
                    if func_data.get("arguments"):
                        entry["arguments"] += func_data["arguments"]
                yield StreamChunk(
                    content="",
                    tool_calls=None,
                    finish_reason="",
                    response_type="tool_call",
                )

            # 普通 content 增量（逐 token 作为 thought 发射，需求 7.4）
            content = delta.get("content") or ""
            if content:
                yield StreamChunk(
                    content=content,
                    tool_calls=None,
                    finish_reason="",
                    response_type="content",
                )

            # 流结束信号：携带累积组装好的完整 tool_calls
            if finish_reason:
                final_tool_calls = self._build_tool_calls(tool_call_map)
                yield StreamChunk(
                    content="",
                    tool_calls=final_tool_calls or None,
                    finish_reason=finish_reason,
                    response_type="tool_call" if final_tool_calls else "content",
                )

    async def _post_stream(self, payload: dict) -> AsyncIterator[str]:
        """发起一次流式 POST 请求并逐行产出 SSE 文本行。

        本方法封装**单次** HTTP 请求，将 HTTP 状态码与连接错误归类为
        ``LLMTransientError`` / ``LLMNonTransientError``。任务 4.2 将在本方法
        外层包裹指数退避重试逻辑（瞬态重试、非瞬态立即停止），故此处仅做分类
        抛错、不做任何重试。

        Args:
            payload: OpenAI 兼容请求体。

        Yields:
            SSE 文本行（已 strip）。

        Raises:
            LLMTransientError: 瞬态错误（429/5xx 或连接错误）。
            LLMNonTransientError: 非瞬态错误（4xx）。
        """
        url = f"{self.base_url}/chat/completions"
        try:
            async with self._client.stream("POST", url, json=payload) as resp:
                if resp.status_code != 200:
                    # 读取错误体用于错误描述
                    await resp.aread()
                    self._raise_for_status(resp.status_code, resp.text)
                async for line in resp.aiter_lines():
                    yield line.strip()
        except httpx.TimeoutException as exc:
            # 超时视为瞬态错误，据以重试
            raise LLMTransientError(f"LLM 请求超时: {exc}") from exc
        except httpx.RequestError as exc:
            # 连接层错误视为瞬态错误，据以重试
            raise LLMTransientError(f"LLM 连接失败: {exc}") from exc

    async def _post_stream_with_retry(self, payload: dict) -> AsyncIterator[str]:
        """在 ``_post_stream`` 外层包裹重试分流策略（需求 7.5–7.7）。

        分流规则：

        - **瞬态错误** ``LLMTransientError``（HTTP 429/500/502/503/504、超时或
          连接错误）：指数退避后重试，初始退避 1s、每次翻倍，最多重试 2 次
          （需求 7.5）。仅在尚未向上层产出任何数据行时才重试——一旦流已开始
          产出，重试会导致上层收到重复 / 错乱片段，故此时不再重试，直接向上抛。
        - **非瞬态错误** ``LLMNonTransientError``（HTTP 400/401/403/404）：立即
          停止、不做任何重试，直接向上抛（需求 7.6）。
        - **重试耗尽**：仍失败则抛出最后一次的 ``LLMTransientError``，其消息
          携带失败原因，供上层发 error 事件（需求 7.7）。

        Args:
            payload: OpenAI 兼容请求体。

        Yields:
            SSE 文本行（已 strip）。

        Raises:
            LLMNonTransientError: 非瞬态错误，立即停止。
            LLMTransientError: 重试耗尽后仍失败。
        """
        backoff = LLM_INITIAL_BACKOFF_SECONDS
        # 总尝试次数 = 1 次初始请求 + LLM_MAX_RETRIES 次重试。
        for attempt in range(LLM_MAX_RETRIES + 1):
            produced = False
            try:
                async for line in self._post_stream(payload):
                    produced = True
                    yield line
                # 正常读完整个流，结束。
                return
            except LLMNonTransientError:
                # 非瞬态错误：立即停止、不重试（需求 7.6）。
                raise
            except LLMTransientError as exc:
                # 流已开始产出后不能安全重试，直接向上抛。
                if produced:
                    raise
                # 重试已耗尽：抛出携带失败原因的错误供上层发 error 事件（需求 7.7）。
                if attempt >= LLM_MAX_RETRIES:
                    logger.error(
                        "LLM 请求重试耗尽（共 %d 次尝试）仍失败: %s",
                        attempt + 1,
                        exc,
                    )
                    raise
                # 瞬态错误：指数退避后重试（需求 7.5）。
                logger.warning(
                    "LLM 瞬态错误，第 %d 次尝试失败，%.1fs 后重试: %s",
                    attempt + 1,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)
                backoff *= LLM_BACKOFF_MULTIPLIER

    @staticmethod
    def _raise_for_status(status_code: int, body: str) -> None:
        """按状态码将失败分类为瞬态 / 非瞬态错误。

        分类依据供任务 4.2 的重试分流策略使用（需求 7.5、7.6）。
        """
        detail = body[:500] if body else ""
        if status_code in TRANSIENT_STATUS_CODES:
            raise LLMTransientError(
                f"LLM 瞬态错误 HTTP {status_code}: {detail}",
                status_code=status_code,
            )
        if status_code in NON_TRANSIENT_STATUS_CODES:
            raise LLMNonTransientError(
                f"LLM 非瞬态错误 HTTP {status_code}: {detail}",
                status_code=status_code,
            )
        # 其它未分类状态码：保守视为瞬态，交由上层策略决定
        raise LLMTransientError(
            f"LLM 未预期错误 HTTP {status_code}: {detail}",
            status_code=status_code,
        )

    @staticmethod
    def _build_tool_calls(tool_call_map: dict[int, dict]) -> list[LLMToolCall]:
        """从累积的 tool_call_map 构建按 index 有序的 LLMToolCall 列表。"""
        result: list[LLMToolCall] = []
        for idx in sorted(tool_call_map.keys()):
            entry = tool_call_map[idx]
            if entry.get("function_name"):
                result.append(
                    LLMToolCall(
                        id=entry["id"],
                        function_name=entry["function_name"],
                        arguments=entry["arguments"],
                    )
                )
        return result

    async def close(self) -> None:
        """关闭底层 HTTP 客户端连接。"""
        await self._client.aclose()
