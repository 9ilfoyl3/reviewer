"""BaseReActAgent —— 多 Agent ReAct 协作流水线的循环骨架（任务 6.3）。

对应需求 4.2、4.3、4.7、4.8、4.14、7.4 与设计文档「多 Agent ReAct 流水线设计」。

本模块实现单个 Agent 的 ReAct（Think → Act → Observe）循环骨架 ``BaseReActAgent``：

- **Think**：每轮先流式调用 LLM，逐 token 发射 ``thought`` 事件（需求 4.2、7.4）。
- **分析响应**：流结束后分析模型输出——若请求工具调用则进入 Act，否则视为提交结论。
- **Act / Observe**：执行 Agent_Tool，发射 ``tool_call`` / ``tool_result`` 事件，
  并将工具结果纳入下一轮上下文（需求 4.3、4.14）。
- **轮数上限**：默认 8、可配置范围 1–20（从配置读取，需求 4.7）；达上限仍未提交
  结论则调用抽象方法 ``synthesize_fallback`` 基于已有观察合成兜底结论（需求 4.8）。
- **事件发射**：为启动、每次工具调用、每次工具结果、每次结论提交通过 EventBus
  发射对应 Progress_Event（需求 4.14）。

具体角色（Code_Auditor / Product_Value_Agent）在任务 6.4 中继承本类实现
``system_prompt`` 与 ``synthesize_fallback`` 两个抽象方法。
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from ..config import get_settings
from ..events.types import (
    AgentLifecycleData,
    EventType,
    ProgressEvent,
    ThoughtData,
    ToolCallData,
    ToolResultData,
)
from ..llm.provider import LLMProvider
from ..models.snapshot import RepositorySnapshot
from .tools.base import ToolRegistry

logger = logging.getLogger(__name__)

# 工具结果摘要长度上限：超出部分在事件层截断（与需求 5.5 前端截断策略呼应）。
_TOOL_SUMMARY_LIMIT = 500


@runtime_checkable
class EventEmitter(Protocol):
    """EventBus 发射接口协议。

    ``BaseReActAgent`` 只依赖 ``emit`` 这一异步方法，从而与具体的
    ``ReviewEventBus``（任务 5.4，发布到 Redis Pub/Sub）解耦——任何提供
    ``async def emit(event: ProgressEvent)`` 的对象都可注入使用，便于测试
    以内存替身注入。
    """

    async def emit(self, event: ProgressEvent) -> object:  # pragma: no cover - 协议声明
        ...


class SeqCounter:
    """单调递增序号生成器。

    Progress_Event 的 ``seq`` 需在一次 Analysis_Session 内单调递增以保证前端
    按序渲染。同一会话内的多个 Agent 应共享同一个 ``SeqCounter``，由
    Agent_Pipeline（任务 6.8）创建并注入。
    """

    def __init__(self, start: int = 0) -> None:
        self._n = start

    def next(self) -> int:
        value = self._n
        self._n += 1
        return value


class Observation(BaseModel):
    """一次工具调用的观察结果（Observe），纳入下一轮上下文并供兜底合成使用。"""

    iteration: int
    tool: str
    args: dict
    output: str
    success: bool
    truncated: bool = False
    error: str | None = None
    tool_call_id: str = ""


class AgentConclusion(BaseModel):
    """单个 Agent 提交的结论。

    - ``role``：产出该结论的 Agent 角色。
    - ``data``：从模型输出中解析出的结构化 JSON 结论（各角色语义不同，
      由子类在其后续处理中解释）。
    - ``raw_text``：模型输出的原始文本（解析失败时的兜底载体）。
    - ``synthesized``：是否为达最大轮数后由 ``synthesize_fallback`` 合成（需求 4.8）。
    """

    role: str
    data: dict = {}
    raw_text: str = ""
    synthesized: bool = False


@dataclass
class PipelineContext:
    """一次流水线执行的共享上下文。

    - ``session_id``：归属 Analysis_Session，用于事件路由。
    - ``snapshot``：内存中的 Repository_Snapshot，作为工具只读操作与提示词上下文来源。
    """

    session_id: str
    snapshot: RepositorySnapshot


class BaseReActAgent(ABC):
    """单 Agent 的 ReAct 循环骨架（需求 4.2、4.3、4.7、4.8、4.14）。

    子类需声明 ``role`` 并实现 ``system_prompt`` 与 ``synthesize_fallback``。
    """

    role: str = "BaseAgent"

    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        event_bus: EventEmitter,
        *,
        session_id: str | None = None,
        max_iterations: int | None = None,
        seq_counter: SeqCounter | None = None,
        temperature: float = 0.3,
    ) -> None:
        """初始化 Agent。

        Args:
            llm: LLM_Provider 客户端（需求 7）。
            tools: 绑定 Repository_Snapshot 的工具注册表（需求 4.4）。
            event_bus: Progress_Event 发射器（需求 4.14）。
            session_id: 归属会话；亦可在 ``run`` 时由 ``PipelineContext`` 提供。
            max_iterations: ReAct 最大轮数；默认从配置读取（默认 8、范围 1–20，需求 4.7）。
            seq_counter: 会话级共享序号生成器；未提供时自建（单 Agent 场景）。
            temperature: 采样温度。
        """
        self.llm = llm
        self._tools = tools
        self._event_bus = event_bus
        self._session_id = session_id
        if max_iterations is None:
            # 从配置读取轮数上限（已在 Settings 中钳制到 1–20，需求 4.7）。
            max_iterations = get_settings().agent_max_iterations
        # 双保险：即便直接注入越界值，也钳制到合法范围，保证循环不悬挂。
        self.max_iterations = max(1, min(20, int(max_iterations)))
        self._seq = seq_counter or SeqCounter()
        self._temperature = temperature
        self._tool_schemas = self._build_tool_schemas()

    # ------------------------------------------------------------------ #
    # 抽象方法：由具体角色 Agent 实现（任务 6.4）
    # ------------------------------------------------------------------ #

    @abstractmethod
    def system_prompt(self, snapshot_ctx: str) -> str:
        """返回本角色的系统提示词。

        Args:
            snapshot_ctx: 由 ``_build_snapshot_ctx`` 生成的 Repository_Snapshot 摘要。
        """

    @abstractmethod
    def synthesize_fallback(self, observations: list[Observation]) -> AgentConclusion:
        """达最大轮数仍未提交结论时的兜底合成（需求 4.8）。

        子类应基于已获得的 ``observations`` 合成一个本角色结论，保证流水线不悬挂。
        """

    # ------------------------------------------------------------------ #
    # ReAct 主循环
    # ------------------------------------------------------------------ #

    async def run(self, context: PipelineContext) -> AgentConclusion:
        """执行 ReAct 循环并返回本角色结论。

        流程（对应需求 4.2、4.3、4.7、4.8、4.14）：

        1. 发射 ``agent_start``（需求 4.14）。
        2. 每轮流式 Think，逐 token 发射 ``thought``（需求 4.2、7.4）。
        3. 分析响应：请求工具调用则 Act（发射 ``tool_call`` / ``tool_result``，
           将结果纳入下一轮上下文，需求 4.3、4.14）；否则视为提交结论。
        4. 达最大轮数上限仍未提交结论则 ``synthesize_fallback`` 合成兜底结论
           （需求 4.8）。
        5. 发射 ``agent_complete``（需求 4.14）。
        """
        self._session_id = context.session_id
        await self._emit_agent_start()

        snapshot_ctx = self._build_snapshot_ctx(context.snapshot)
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt(snapshot_ctx)},
            {"role": "user", "content": self._initial_user_message()},
        ]
        observations: list[Observation] = []

        for iteration in range(1, self.max_iterations + 1):
            content, tool_calls = await self._think(messages, iteration)

            if tool_calls:
                # Act：将本轮 assistant 的工具调用请求写回上下文。
                messages.append(self._assistant_tool_message(content, tool_calls))
                for call in tool_calls:
                    observation = await self._act(call, iteration)
                    observations.append(observation)
                    # Observe：工具结果作为 tool 消息纳入下一轮上下文（需求 4.3）。
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": observation.tool_call_id,
                            "content": observation.output
                            or (observation.error or ""),
                        }
                    )
                continue

            # 未请求工具 → 视为提交结论。
            conclusion = self._parse_conclusion(content)
            await self._emit_agent_complete(conclusion)
            return conclusion

        # 达最大轮数上限仍未提交结论 → 兜底合成（需求 4.8）。
        logger.info(
            "Agent %s 达最大轮数 %d 未提交结论，触发兜底合成（会话 %s）",
            self.role,
            self.max_iterations,
            self._session_id,
        )
        conclusion = self.synthesize_fallback(observations)
        conclusion.synthesized = True
        await self._emit_agent_complete(conclusion)
        return conclusion

    async def _think(
        self, messages: list[dict], iteration: int
    ) -> tuple[str, list]:
        """流式 Think：逐 token 发射 thought，返回累积文本与工具调用列表。"""
        content_parts: list[str] = []
        tool_calls: list = []

        async for chunk in self.llm.stream_with_tools(
            messages, self._tool_schemas, self._temperature
        ):
            if chunk.content:
                content_parts.append(chunk.content)
                # 逐 token 发射 thought（需求 4.2、7.4）。
                await self._emit_thought(chunk.content, iteration)
            if chunk.tool_calls:
                tool_calls = chunk.tool_calls

        return "".join(content_parts), tool_calls

    async def _act(self, call, iteration: int) -> Observation:
        """Act + Observe：执行工具、发射事件、返回观察结果（需求 4.3、4.14）。"""
        try:
            args = json.loads(call.arguments) if call.arguments else {}
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            args = {}

        await self._emit_tool_call(call.function_name, args)

        # 工具层已对未知工具名 / 非法参数容错（返回 success=False），循环继续（需求 4.16）。
        result = self._tools.execute(call.function_name, args)
        summary = result.output if result.success else (result.error or "")
        await self._emit_tool_result(
            call.function_name, summary, result.truncated
        )

        return Observation(
            iteration=iteration,
            tool=call.function_name,
            args=args,
            output=result.output,
            success=result.success,
            truncated=result.truncated,
            error=result.error,
            tool_call_id=call.id,
        )

    # ------------------------------------------------------------------ #
    # 提示词与上下文构建
    # ------------------------------------------------------------------ #

    def _initial_user_message(self) -> str:
        """首轮用户消息：指引 Agent 按需调用工具、以结构化 JSON 提交结论。"""
        tool_names = "、".join(self._tools.names())
        return (
            "请对该仓库进行分析。你可以按需调用以下工具收集证据："
            f"{tool_names}。"
            "当你收集到足够信息后，请直接输出结构化 JSON 作为最终结论"
            "（不要再调用工具）。"
        )

    @staticmethod
    def _build_snapshot_ctx(snapshot: RepositorySnapshot) -> str:
        """生成注入系统提示词的 Repository_Snapshot 摘要。

        包含元数据概览、目录条目数与前若干条目、README 长度，供 Agent 建立
        初始上下文（避免一次性灌入超大内容，具体内容由工具按需读取）。
        """
        meta = snapshot.metadata
        tree_preview = "\n".join(
            f"  {entry.type}\t{entry.path}" for entry in snapshot.tree[:50]
        )
        truncated_note = "（目录树已截断）" if snapshot.tree_truncated else ""
        return (
            f"仓库：{meta.owner}/{meta.repo}\n"
            f"Star：{meta.stars}，Fork：{meta.forks}，Open Issues：{meta.open_issues}\n"
            f"语言分布（字节）：{meta.languages}\n"
            f"默认分支：{meta.default_branch}，最近提交：{meta.last_commit_at}\n"
            f"README 字符数：{len(snapshot.readme)}\n"
            f"目录条目数：{len(snapshot.tree)}{truncated_note}\n"
            f"目录预览（至多 50 条）：\n{tree_preview}"
        )

    def _build_tool_schemas(self) -> list[dict]:
        """将注册表中的工具转换为 OpenAI function-calling 工具 schema。

        通过反射 ``Tool.run`` 的签名推导参数（排除 ``self`` / ``snapshot``），
        无默认值的参数视为必需，其余视为可选，从而无需修改工具层即可生成 schema。
        """
        schemas: list[dict] = []
        for name in self._tools.names():
            tool = self._tools.get(name)
            if tool is None:
                continue
            properties: dict[str, dict] = {}
            required: list[str] = []
            signature = inspect.signature(tool.run)
            for param_name, param in signature.parameters.items():
                if param_name in ("self", "snapshot") or param.kind in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                ):
                    continue
                properties[param_name] = {
                    "type": "string",
                    "description": f"参数 {param_name}",
                }
                if param.default is inspect.Parameter.empty:
                    required.append(param_name)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return schemas

    def _parse_conclusion(self, content: str) -> AgentConclusion:
        """从模型输出文本中解析结构化 JSON 结论。

        优先整体解析为 JSON；失败时回退到提取首个 ``{...}`` 片段解析；仍失败
        则以原始文本承载（不丢弃模型产出）。
        """
        data = self._extract_json(content)
        return AgentConclusion(
            role=self.role,
            data=data,
            raw_text=content,
        )

    @staticmethod
    def _extract_json(text: str) -> dict:
        """尽力从文本中解析出一个 JSON 对象，失败返回空字典。"""
        if not text:
            return {}
        stripped = text.strip()
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
        # 回退：提取首个花括号包裹片段（兼容模型在 JSON 前后夹带说明文本）。
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _assistant_tool_message(content: str, tool_calls: list) -> dict:
        """构造携带工具调用的 assistant 消息（OpenAI 格式），写回上下文。"""
        return {
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function_name,
                        "arguments": call.arguments,
                    },
                }
                for call in tool_calls
            ],
        }

    # ------------------------------------------------------------------ #
    # 事件发射（需求 4.14）
    # ------------------------------------------------------------------ #

    async def _emit(
        self, event_type: EventType, data: dict, *, agent: str | None = None
    ) -> None:
        """构造并发射一条 Progress_Event（seq 单调递增）。"""
        event = ProgressEvent(
            type=event_type,
            session_id=self._session_id or "",
            agent=agent if agent is not None else self.role,
            seq=self._seq.next(),
            data=data,
            ts=time.time(),
        )
        await self._event_bus.emit(event)

    async def _emit_agent_start(self) -> None:
        """发射 agent_start 事件（需求 4.14）。"""
        await self._emit(
            EventType.AGENT_START,
            AgentLifecycleData(agent=self.role).model_dump(),
        )

    async def _emit_thought(self, content: str, iteration: int) -> None:
        """发射 thought 增量事件（需求 4.2、7.4）。"""
        await self._emit(
            EventType.THOUGHT,
            ThoughtData(content=content, iteration=iteration).model_dump(),
        )

    async def _emit_tool_call(self, tool: str, args: dict) -> None:
        """发射 tool_call 事件（需求 4.14）。"""
        await self._emit(
            EventType.TOOL_CALL,
            ToolCallData(tool=tool, args=args).model_dump(),
        )

    async def _emit_tool_result(
        self, tool: str, summary: str, truncated: bool
    ) -> None:
        """发射 tool_result 事件（需求 4.14）；摘要超上限时截断标记。"""
        was_truncated = truncated
        if len(summary) > _TOOL_SUMMARY_LIMIT:
            summary = summary[:_TOOL_SUMMARY_LIMIT]
            was_truncated = True
        await self._emit(
            EventType.TOOL_RESULT,
            ToolResultData(
                tool=tool, summary=summary, truncated=was_truncated
            ).model_dump(),
        )

    async def _emit_agent_complete(self, conclusion: AgentConclusion) -> None:
        """发射 agent_complete 事件（需求 4.14）。"""
        summary = conclusion.raw_text[:_TOOL_SUMMARY_LIMIT] if conclusion.raw_text else None
        await self._emit(
            EventType.AGENT_COMPLETE,
            AgentLifecycleData(agent=self.role, conclusion=summary).model_dump(),
        )
