"""Agent_Pipeline 编排测试（任务 6.9）。

对应需求 10.4 与设计文档「测试策略 → Agent_Pipeline 编排测试」：

- 用 LLM_Provider 与 GitHub_Client 的 mock 替身驱动流水线，并断言测试过程中
  未发起任何真实外部网络调用（通过守卫 ``httpx.AsyncClient``，任何真实网络
  构造/调用都会立即失败）。
- 验证编排步骤按预期顺序：
    1. 三个 Agent（Code_Auditor、Product_Value_Agent、Final_Judge）均被执行；
    2. Code_Auditor（A）与 Product_Value_Agent（B）的结论作为输入传给
       Final_Judge（C）；
    3. 各阶段通过 Event_Bus 发射对应 Progress_Event（每个 Agent 的
       agent_start / agent_complete，以及流水线末尾统一发射的 final_report）。

流水线自身不触碰网络：GitHub 抓取在上游完成并归一化为 Repository_Snapshot，
LLM 调用经注入的替身，工具对内存中的 Snapshot 做只读操作。本测试以内存替身
注入 LLMProvider 与 GitHubClient，全程无真实网络。
"""

from __future__ import annotations

import pytest

from app.agent.code_auditor import CodeAuditor
from app.agent.final_judge import FinalJudge
from app.agent.pipeline import AgentPipeline
from app.agent.product_value import ProductValueAgent
from app.events.types import EventType, ProgressEvent
from app.llm.provider import StreamChunk
from app.models.report import HealthReport
from app.models.snapshot import RepositorySnapshot

# Code_Auditor / Product_Value_Agent 结论中植入的独特标记文本，
# 用于断言 A/B 结论确实被传递给 Final_Judge。
_CODE_STRENGTH_MARKER = "分层清晰-来自CodeAuditor"
_CODE_IMPROVE_MARKER = "补充测试-来自CodeAuditor"
_PRODUCT_README_MARKER = "README完善-来自ProductValue"


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #


class SpyLLM:
    """LLMProvider 替身：按调用方角色返回对应的结构化 JSON 结论。

    通过系统提示词内容识别调用方角色（Code_Auditor / Product_Value_Agent /
    Final_Judge），并：

    - 记录每次调用的角色（``invocation_order``），用于断言三 Agent 均执行、
      且 Final_Judge 在 A、B 之后执行；
    - 记录每次调用传入的 messages（``messages_by_role``），用于断言 A、B 的
      结论被传递给 Final_Judge。

    不发起任何真实网络调用（纯内存产出）。
    """

    def __init__(self) -> None:
        self.invocation_order: list[str] = []
        self.messages_by_role: dict[str, list[dict]] = {}

    async def stream_with_tools(self, messages, tools=None, temperature=0.7):
        system_prompt = messages[0]["content"] if messages else ""
        role = self._detect_role(system_prompt)
        self.invocation_order.append(role)
        self.messages_by_role[role] = [dict(m) for m in messages]

        for ch in self._script_for(role):
            yield ch

    @staticmethod
    def _detect_role(system_prompt: str) -> str:
        # 注意：Final_Judge 提示词中也会提及另外两个角色名，故先判定 Final_Judge。
        if "你是 Final_Judge" in system_prompt:
            return FinalJudge.role
        if "你是 Code_Auditor" in system_prompt:
            return CodeAuditor.role
        if "你是 Product_Value_Agent" in system_prompt:
            return ProductValueAgent.role
        return "Unknown"

    def _script_for(self, role: str) -> list[StreamChunk]:
        return _content_chunks(_conclusion_json(role))


class SpyGitHubClient:
    """GitHub_Client 替身：返回预置 Repository_Snapshot，不发起任何网络调用。"""

    def __init__(self, snapshot: RepositorySnapshot) -> None:
        self._snapshot = snapshot
        self.fetch_calls: list[tuple[str, str]] = []

    async def fetch_snapshot(self, owner: str, repo: str) -> RepositorySnapshot:
        self.fetch_calls.append((owner, repo))
        return self._snapshot


class RecordingBus:
    """EventBus 替身：收集所有发射的 Progress_Event。"""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    async def emit(self, event: ProgressEvent):
        self.events.append(event)
        return 1


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _content_chunks(text: str, finish: str = "stop") -> list[StreamChunk]:
    """将文本拆成逐 token 的 content chunk，末尾附一个 finish chunk。"""

    chunks = [StreamChunk(content=ch, response_type="content") for ch in text]
    chunks.append(StreamChunk(finish_reason=finish, response_type="content"))
    return chunks


def _conclusion_json(role: str) -> str:
    """按角色返回其结构化 JSON 结论文本（单轮直接提交，不调用工具）。"""

    if role == CodeAuditor.role:
        return (
            '{"strengths": ["' + _CODE_STRENGTH_MARKER + '"], '
            '"improvements": ["' + _CODE_IMPROVE_MARKER + '"], '
            '"summary": "代码审计整体评价"}'
        )
    if role == ProductValueAgent.role:
        return (
            '{"readme_clarity": ["' + _PRODUCT_README_MARKER + '"], '
            '"practical_value": ["实用价值结论"], '
            '"activeness": ["活跃度结论"], '
            '"summary": "产品价值整体评价"}'
        )
    if role == FinalJudge.role:
        return '{"score": 77, "recommendations": ["建议一", "建议二", "建议三"]}'
    return "{}"


def _make_snapshot() -> RepositorySnapshot:
    return RepositorySnapshot(
        metadata={
            "owner": "octocat",
            "repo": "hello-world",
            "stars": 42,
            "forks": 7,
            "open_issues": 3,
            "languages": {"Python": 800, "TypeScript": 200},
            "last_commit_at": "2024-01-01T00:00:00Z",
            "default_branch": "main",
        },
        readme="# Hello World\n\n项目简介。",
        tree=[
            {"path": "README.md", "type": "file", "depth": 0},
            {"path": "src", "type": "dir", "depth": 0},
            {"path": "src/main.py", "type": "file", "depth": 1},
        ],
        tree_truncated=False,
        representative_files={"src/main.py": "print('hello')"},
        fetched_at="2024-01-02T00:00:00Z",
    )


@pytest.fixture
def no_real_network(monkeypatch):
    """守卫：构造真实 ``httpx.AsyncClient`` 即失败，确保测试无真实网络调用。

    LLMProvider 与 GitHubClient 的真实实现均通过 ``httpx.AsyncClient`` 发起
    外部请求。本测试全程使用内存替身，不应构造任何真实客户端；一旦有代码路径
    尝试构造，立即抛错以暴露隐藏的真实网络调用（需求 10.4）。
    """

    import httpx

    def _forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError(
            "测试期间检测到构造真实 httpx.AsyncClient——不允许发起真实外部网络调用（需求 10.4）"
        )

    monkeypatch.setattr(httpx, "AsyncClient", _forbidden)


# --------------------------------------------------------------------------- #
# 需求 10.4：编排顺序 + mock 替身 + 无真实网络
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pipeline_orchestrates_three_agents_in_order(no_real_network):
    """三 Agent 均执行、A/B 结论传给 Final_Judge、各阶段发射对应事件（需求 10.4）。"""

    llm = SpyLLM()
    bus = RecordingBus()
    github = SpyGitHubClient(_make_snapshot())

    # 以 GitHub_Client 替身抓取快照（模拟上游 Worker runner 流程，全程无网络）。
    snapshot = await github.fetch_snapshot("octocat", "hello-world")
    assert github.fetch_calls == [("octocat", "hello-world")]

    pipeline = AgentPipeline(llm, bus, max_iterations=8)
    report = await pipeline.run("sess-1", snapshot)

    # --- 返回值为合法 Health_Report，分数取自 Final_Judge（77）--- #
    assert isinstance(report, HealthReport)
    assert report.score == 77

    # --- 1. 三个 Agent 均被执行（各自触发一次 LLM 调用）--- #
    assert set(llm.invocation_order) == {
        CodeAuditor.role,
        ProductValueAgent.role,
        FinalJudge.role,
    }

    # --- 2. 编排顺序：A、B 先于 C；Final_Judge 为最后一次调用 --- #
    code_idx = llm.invocation_order.index(CodeAuditor.role)
    product_idx = llm.invocation_order.index(ProductValueAgent.role)
    judge_idx = llm.invocation_order.index(FinalJudge.role)
    assert judge_idx > code_idx
    assert judge_idx > product_idx
    assert llm.invocation_order[-1] == FinalJudge.role

    # --- 3. A、B 的结论作为输入传递给 Final_Judge --- #
    judge_messages = llm.messages_by_role[FinalJudge.role]
    judge_prompt = judge_messages[0]["content"]
    assert _CODE_STRENGTH_MARKER in judge_prompt
    assert _CODE_IMPROVE_MARKER in judge_prompt
    assert _PRODUCT_README_MARKER in judge_prompt

    # 且这些结论也如实反映在最终报告中。
    assert report.code_auditor.strengths == [_CODE_STRENGTH_MARKER]
    assert report.code_auditor.improvements == [_CODE_IMPROVE_MARKER]
    assert report.product_value.readme_clarity == [_PRODUCT_README_MARKER]


@pytest.mark.asyncio
async def test_pipeline_emits_events_per_stage(no_real_network):
    """各阶段发射对应 Progress_Event：三 Agent 的 start/complete 与末尾 final_report。"""

    llm = SpyLLM()
    bus = RecordingBus()
    snapshot = _make_snapshot()

    pipeline = AgentPipeline(llm, bus, max_iterations=8)
    report = await pipeline.run("sess-1", snapshot)

    # 每个 Agent 各发射一次 agent_start 与 agent_complete（需求 4.14）。
    for role in (CodeAuditor.role, ProductValueAgent.role, FinalJudge.role):
        starts = [
            e
            for e in bus.events
            if e.type == EventType.AGENT_START and e.agent == role
        ]
        completes = [
            e
            for e in bus.events
            if e.type == EventType.AGENT_COMPLETE and e.agent == role
        ]
        assert len(starts) == 1, f"{role} 应恰好发射一次 agent_start"
        assert len(completes) == 1, f"{role} 应恰好发射一次 agent_complete"

    # 三个 Agent 均产生了流式 thought 事件（需求 4.2、7.4）。
    thought_roles = {
        e.agent for e in bus.events if e.type == EventType.THOUGHT
    }
    assert thought_roles == {
        CodeAuditor.role,
        ProductValueAgent.role,
        FinalJudge.role,
    }

    # 流水线末尾恰好发射一次 final_report，其 data 为完整 Health_Report（需求 5.6）。
    final_events = [
        e for e in bus.events if e.type == EventType.FINAL_REPORT
    ]
    assert len(final_events) == 1
    final_event = final_events[0]
    assert final_event.agent == FinalJudge.role
    assert final_event.data["report"]["score"] == report.score

    # final_report 为整个事件序列中的最后一条事件。
    assert bus.events[-1].type == EventType.FINAL_REPORT

    # 事件 seq 全局单调递增且唯一（会话级共享 SeqCounter，保证前端按序渲染）。
    seqs = [e.seq for e in bus.events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


@pytest.mark.asyncio
async def test_final_report_event_after_both_agents_complete(no_real_network):
    """final_report 事件在 A、B、C 三者 agent_complete 之后发射（编排顺序）。"""

    llm = SpyLLM()
    bus = RecordingBus()

    pipeline = AgentPipeline(llm, bus, max_iterations=8)
    await pipeline.run("sess-1", _make_snapshot())

    types_seq = [e.type for e in bus.events]
    final_report_pos = types_seq.index(EventType.FINAL_REPORT)
    complete_positions = [
        i for i, t in enumerate(types_seq) if t == EventType.AGENT_COMPLETE
    ]
    # 三个 agent_complete 均出现在 final_report 之前。
    assert len(complete_positions) == 3
    assert all(pos < final_report_pos for pos in complete_positions)
