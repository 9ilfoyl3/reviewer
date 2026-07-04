"""BaseReActAgent 循环骨架单元测试（任务 6.3）。

覆盖需求：
- 4.2：每轮先流式 Think（逐 token 发射 thought），再决定调用工具或提交结论。
- 4.3：工具结果纳入下一轮上下文（Observe）。
- 4.7：轮数上限从配置读取（默认 8、范围 1–20）。
- 4.8：达最大轮数仍未提交结论则调用 synthesize_fallback 合成兜底结论。
- 4.14：为启动、每次工具调用、每次工具结果、每次结论提交发射对应事件。
- 7.4：以流式方式逐片段发射 thought 事件。

使用内存替身注入 LLMProvider 与 EventBus，不发起任何真实网络调用。
"""

import pytest

from app.agent.base import (
    AgentConclusion,
    BaseReActAgent,
    Observation,
    PipelineContext,
    SeqCounter,
)
from app.agent.tools import build_default_registry
from app.events.types import EventType, ProgressEvent
from app.llm.provider import LLMToolCall, StreamChunk
from app.models.snapshot import RepositorySnapshot


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #


class FakeLLM:
    """LLMProvider 替身：按预设的每轮 chunk 脚本逐轮产出流式片段。

    ``scripts`` 为多轮脚本，每轮是一个 StreamChunk 列表；每次调用
    ``stream_with_tools`` 消费下一轮脚本。
    """

    def __init__(self, scripts: list[list[StreamChunk]]):
        self._scripts = scripts
        self.calls: list[list[dict]] = []

    async def stream_with_tools(self, messages, tools=None, temperature=0.7):
        # 记录传入的 messages 以断言上下文累积（需求 4.3）。
        self.calls.append([dict(m) for m in messages])
        idx = len(self.calls) - 1
        script = self._scripts[idx] if idx < len(self._scripts) else []
        for chunk in script:
            yield chunk


class FakeBus:
    """EventBus 替身：收集所有发射的 Progress_Event。"""

    def __init__(self):
        self.events: list[ProgressEvent] = []

    async def emit(self, event: ProgressEvent):
        self.events.append(event)
        return 1


class _StubAgent(BaseReActAgent):
    """具体 Agent 桩：实现两个抽象方法以驱动循环骨架测试。"""

    role = "StubAgent"

    def system_prompt(self, snapshot_ctx: str) -> str:
        return f"you are a stub agent.\n{snapshot_ctx}"

    def synthesize_fallback(self, observations: list[Observation]) -> AgentConclusion:
        return AgentConclusion(
            role=self.role,
            data={"fallback": True, "observation_count": len(observations)},
            raw_text="fallback conclusion",
        )


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #


def _make_snapshot() -> RepositorySnapshot:
    return RepositorySnapshot(
        metadata={
            "owner": "octocat",
            "repo": "hello-world",
            "stars": 42,
            "forks": 7,
            "open_issues": 3,
            "languages": {"Python": 1000},
            "last_commit_at": "2024-01-01T00:00:00Z",
            "default_branch": "main",
        },
        readme="# Hello",
        tree=[{"path": "README.md", "type": "file", "depth": 0}],
        tree_truncated=False,
        representative_files={"README.md": "# Hello"},
        fetched_at="2024-01-02T00:00:00Z",
    )


def _content_chunks(text: str, finish: str = "stop") -> list[StreamChunk]:
    """将文本拆成逐 token 的 content chunk，末尾附一个 finish chunk。"""
    chunks = [StreamChunk(content=ch, response_type="content") for ch in text]
    chunks.append(StreamChunk(finish_reason=finish, response_type="content"))
    return chunks


def _tool_call_chunks(tool: str, args_json: str) -> list[StreamChunk]:
    """构造一个请求工具调用的轮次脚本。"""
    return [
        StreamChunk(response_type="tool_call"),
        StreamChunk(
            tool_calls=[
                LLMToolCall(id="call-1", function_name=tool, arguments=args_json)
            ],
            finish_reason="tool_calls",
            response_type="tool_call",
        ),
    ]


def _build_agent(llm, bus, **kwargs):
    snapshot = _make_snapshot()
    tools = build_default_registry(snapshot)
    # 显式传入 max_iterations 以避免依赖环境配置（config 默认路径由专门用例覆盖）。
    kwargs.setdefault("max_iterations", 8)
    agent = _StubAgent(
        llm=llm,
        tools=tools,
        event_bus=bus,
        session_id="sess-1",
        seq_counter=SeqCounter(),
        **kwargs,
    )
    return agent, PipelineContext(session_id="sess-1", snapshot=snapshot)


# --------------------------------------------------------------------------- #
# 需求 4.2 / 7.4：流式 Think 逐 token 发射 thought，随后提交结论
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_think_emits_thought_per_token_then_concludes():
    llm = FakeLLM([_content_chunks('{"verdict": "ok"}')])
    bus = FakeBus()
    agent, ctx = _build_agent(llm, bus)

    conclusion = await agent.run(ctx)

    # 结论被解析为结构化 JSON（需求 4.2 提交结论）。
    assert conclusion.role == "StubAgent"
    assert conclusion.data == {"verdict": "ok"}
    assert conclusion.synthesized is False

    # 逐 token 发射 thought（需求 4.2、7.4）：thought 事件数等于字符数。
    thought_events = [e for e in bus.events if e.type == EventType.THOUGHT]
    assert len(thought_events) == len('{"verdict": "ok"}')
    # 拼接 thought 增量应还原完整推理文本。
    joined = "".join(e.data["content"] for e in thought_events)
    assert joined == '{"verdict": "ok"}'


# --------------------------------------------------------------------------- #
# 需求 4.14：发射 agent_start 与 agent_complete
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_emits_agent_start_and_complete():
    llm = FakeLLM([_content_chunks("{}")])
    bus = FakeBus()
    agent, ctx = _build_agent(llm, bus)

    await agent.run(ctx)

    types = [e.type for e in bus.events]
    assert types[0] == EventType.AGENT_START
    assert types[-1] == EventType.AGENT_COMPLETE
    # 所有事件的 seq 单调递增。
    seqs = [e.seq for e in bus.events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


# --------------------------------------------------------------------------- #
# 需求 4.3 / 4.14：Act 调用工具，发射 tool_call / tool_result，结果纳入下一轮上下文
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tool_call_emits_events_and_feeds_context():
    llm = FakeLLM(
        [
            # 第 1 轮：请求 read_file 工具
            _tool_call_chunks("read_file", '{"path": "README.md"}'),
            # 第 2 轮：提交结论
            _content_chunks('{"done": true}'),
        ]
    )
    bus = FakeBus()
    agent, ctx = _build_agent(llm, bus)

    conclusion = await agent.run(ctx)
    assert conclusion.data == {"done": True}

    # 发射了 tool_call 与 tool_result 事件（需求 4.14）。
    call_events = [e for e in bus.events if e.type == EventType.TOOL_CALL]
    result_events = [e for e in bus.events if e.type == EventType.TOOL_RESULT]
    assert len(call_events) == 1
    assert call_events[0].data["tool"] == "read_file"
    assert call_events[0].data["args"] == {"path": "README.md"}
    assert len(result_events) == 1
    assert result_events[0].data["tool"] == "read_file"

    # 工具结果被纳入第 2 轮上下文（需求 4.3）：第 2 次 LLM 调用的 messages
    # 中应含 role=tool 的消息且内容为 README 文件内容。
    second_call_messages = llm.calls[1]
    tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "# Hello"


# --------------------------------------------------------------------------- #
# 需求 4.8：达最大轮数仍未提交结论则 synthesize_fallback 合成兜底结论
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reaches_max_iterations_triggers_fallback():
    # 每轮都请求工具调用，永不提交结论 → 触发兜底。
    scripts = [_tool_call_chunks("read_readme", "{}") for _ in range(3)]
    llm = FakeLLM(scripts)
    bus = FakeBus()
    agent, ctx = _build_agent(llm, bus, max_iterations=3)

    conclusion = await agent.run(ctx)

    # 兜底合成结论（需求 4.8）。
    assert conclusion.synthesized is True
    assert conclusion.data["fallback"] is True
    # 观察数应等于轮数（每轮一次工具调用）。
    assert conclusion.data["observation_count"] == 3
    # LLM 恰好被调用 max_iterations 次。
    assert len(llm.calls) == 3
    # 仍发射 agent_complete。
    assert bus.events[-1].type == EventType.AGENT_COMPLETE


# --------------------------------------------------------------------------- #
# 需求 4.7：轮数上限从配置读取并钳制到 1–20
# --------------------------------------------------------------------------- #


def test_max_iterations_clamped_to_valid_range():
    llm = FakeLLM([])
    bus = FakeBus()
    tools = build_default_registry(_make_snapshot())

    over = _StubAgent(llm=llm, tools=tools, event_bus=bus, max_iterations=99)
    assert over.max_iterations == 20

    under = _StubAgent(llm=llm, tools=tools, event_bus=bus, max_iterations=0)
    assert under.max_iterations == 1


def test_max_iterations_defaults_from_settings(monkeypatch):
    # 提供必需 LLM 配置，使 get_settings 通过 fail-fast 校验。
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    from app.config import get_settings

    get_settings.cache_clear()
    expected = get_settings().agent_max_iterations

    llm = FakeLLM([])
    bus = FakeBus()
    tools = build_default_registry(_make_snapshot())
    agent = _StubAgent(llm=llm, tools=tools, event_bus=bus)
    assert agent.max_iterations == expected
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# 需求 5.5 呼应：tool_result 摘要超 500 字符截断标记
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tool_result_summary_truncated_over_limit():
    big_content = "x" * 1200
    snapshot = _make_snapshot()
    snapshot.representative_files["big.txt"] = big_content
    tools = build_default_registry(snapshot)

    llm = FakeLLM(
        [
            _tool_call_chunks("read_file", '{"path": "big.txt"}'),
            _content_chunks("{}"),
        ]
    )
    bus = FakeBus()
    agent = _StubAgent(
        llm=llm,
        tools=tools,
        event_bus=bus,
        session_id="sess-1",
        seq_counter=SeqCounter(),
        max_iterations=8,
    )
    ctx = PipelineContext(session_id="sess-1", snapshot=snapshot)

    await agent.run(ctx)

    result_events = [e for e in bus.events if e.type == EventType.TOOL_RESULT]
    assert len(result_events) == 1
    assert len(result_events[0].data["summary"]) == 500
    assert result_events[0].data["truncated"] is True
