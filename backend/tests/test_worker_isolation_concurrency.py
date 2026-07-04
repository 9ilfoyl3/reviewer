"""Worker 失败隔离与并发单元测试（任务 8.5）。

对应需求 5.7 与设计文档「后端并发与队列设计 → 并发控制」：

- **失败隔离**：单个评估任务在 :class:`ReviewConsumer` 的独立协程 + ``try/except``
  中运行，任一任务异常只把该 session 置 failed，绝不波及同进程其它在途任务。
- **并发信号量**：``asyncio.Semaphore(REVIEW_MAX_CONCURRENT)`` 限制单 Worker
  同时执行的评估数，在途任务数恒不超过配置上限。
- **error 事件发射**：:class:`AnalysisRunner` 在 GitHub 抓取失败与流水线异常时
  发射一条 error 类型 Progress_Event 并将会话置 failed（需求 5.7、7.7、2.4/2.5/2.10）。

全部使用内存替身 / fakeredis，不发起任何真实网络调用。

_Requirements: 5.7_
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from app.config import Settings
from app.events.types import ErrorData, EventType, ProgressEvent
from app.github.errors import GitHubNotFoundError, GitHubRateLimitError, GitHubTimeoutError
from app.llm.provider import LLMProviderError
from app.queue.session_store import SessionStatus, SessionStore
from app.queue.task_queue import ConsumedTask
from app.worker.consumer import ReviewConsumer
from app.worker.runner import AnalysisRunner


# --------------------------------------------------------------------------- #
# ReviewConsumer 用测试替身
# --------------------------------------------------------------------------- #


class FakeRunner:
    """AnalysisRunner 替身：记录执行、可对指定 session 抛异常、可用门控测并发。

    - ``fail_sessions``：位于其中的 session_id 在执行时抛异常，模拟单任务失败。
    - ``gate``：可选 ``asyncio.Event``；不为 None 时每个任务进入后阻塞等待其被
      set，用于观测「同时在途任务数」是否被信号量限制。
    - ``peak_concurrent``：记录执行期间观测到的最大并发在途任务数。
    """

    def __init__(
        self,
        *,
        fail_sessions: set[str] | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.fail_sessions = fail_sessions or set()
        self.gate = gate
        self.completed: list[str] = []
        self.started: list[str] = []
        self._current = 0
        self.peak_concurrent = 0

    async def run(self, payload: dict) -> None:
        session_id = str(payload.get("session_id", ""))
        self.started.append(session_id)
        self._current += 1
        self.peak_concurrent = max(self.peak_concurrent, self._current)
        try:
            if self.gate is not None:
                await self.gate.wait()
            else:
                # 让出事件循环，使多个任务有机会交叠调度。
                await asyncio.sleep(0)
            if session_id in self.fail_sessions:
                raise RuntimeError(f"任务执行失败：{session_id}")
            self.completed.append(session_id)
        finally:
            self._current -= 1


class FakeQueue:
    """TaskQueue 替身：仅记录被 ACK 的消息 ID。"""

    def __init__(self) -> None:
        self.acked: list[str] = []

    async def ack(self, message_id: str) -> int:
        self.acked.append(message_id)
        return 1


class FakeSessionStore:
    """SessionStore 替身：记录被兜底置 failed 的会话。"""

    def __init__(self) -> None:
        self.failed: dict[str, str] = {}

    async def mark_failed(self, session_id: str, error: str) -> None:
        self.failed[session_id] = error


def _make_consumed(session_id: str, message_id: str) -> ConsumedTask:
    """构造一条消费到的任务。"""
    return ConsumedTask(
        message_id=message_id,
        payload={
            "session_id": session_id,
            "owner": "octocat",
            "repo": session_id,
            "repo_url": f"https://github.com/octocat/{session_id}",
        },
    )


def _make_consumer(
    *,
    runner: FakeRunner,
    queue: FakeQueue,
    session_store: FakeSessionStore,
    max_concurrent: int = 4,
) -> ReviewConsumer:
    return ReviewConsumer(
        task_queue=queue,  # type: ignore[arg-type]
        session_store=session_store,  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
        consumer_name="worker-test",
        max_concurrent=max_concurrent,
    )


# --------------------------------------------------------------------------- #
# 失败隔离（需求 5.7）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_single_task_failure_does_not_affect_others():
    """单任务异常被隔离：其它同进程任务仍正常完成（需求 5.7）。"""
    runner = FakeRunner(fail_sessions={"s1"})
    queue = FakeQueue()
    store = FakeSessionStore()
    consumer = _make_consumer(runner=runner, queue=queue, session_store=store)

    # 调度 3 个任务，其中 s1 会抛异常。
    for i in range(3):
        consumer._schedule(_make_consumed(f"s{i}", f"msg-{i}"))
    await consumer._drain()

    # s0、s2 正常完成，s1 因异常未进入 completed。
    assert sorted(runner.completed) == ["s0", "s2"]
    # 失败任务被兜底置 failed，且不波及其它任务。
    assert "s1" in store.failed
    assert store.failed["s1"]  # 含失败原因描述
    assert "s0" not in store.failed
    assert "s2" not in store.failed


@pytest.mark.asyncio
async def test_all_tasks_acked_regardless_of_outcome():
    """无论成功或失败，任务最终都被 XACK（至少一次语义，需求 5.7）。"""
    runner = FakeRunner(fail_sessions={"s1"})
    queue = FakeQueue()
    store = FakeSessionStore()
    consumer = _make_consumer(runner=runner, queue=queue, session_store=store)

    for i in range(3):
        consumer._schedule(_make_consumed(f"s{i}", f"msg-{i}"))
    await consumer._drain()

    # 三条消息（含失败的 s1）均被确认，移出 PEL。
    assert sorted(queue.acked) == ["msg-0", "msg-1", "msg-2"]


@pytest.mark.asyncio
async def test_failure_isolation_does_not_raise_to_caller():
    """单任务异常不向上抛出，_handle 自行吞掉并降级（需求 5.7）。"""
    runner = FakeRunner(fail_sessions={"boom"})
    queue = FakeQueue()
    store = FakeSessionStore()
    consumer = _make_consumer(runner=runner, queue=queue, session_store=store)

    # 直接 await _handle，不应抛出异常。
    await consumer._handle(_make_consumed("boom", "msg-boom"))
    assert "boom" in store.failed
    assert queue.acked == ["msg-boom"]


# --------------------------------------------------------------------------- #
# 并发信号量限制（design.md 并发控制）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_semaphore_limits_concurrent_tasks():
    """信号量限制单 Worker 同时在途任务数不超过 max_concurrent。"""
    gate = asyncio.Event()
    runner = FakeRunner(gate=gate)
    queue = FakeQueue()
    store = FakeSessionStore()
    consumer = _make_consumer(
        runner=runner, queue=queue, session_store=store, max_concurrent=2
    )

    # 调度 5 个任务，但信号量容量为 2。
    for i in range(5):
        consumer._schedule(_make_consumed(f"s{i}", f"msg-{i}"))

    # 让事件循环推进，使可运行的任务尽量进入 runner.run 并在门控处阻塞。
    for _ in range(10):
        await asyncio.sleep(0)

    # 同时进入执行的任务数被限制为 2，其余 3 个在信号量外等待。
    assert runner.peak_concurrent == 2
    assert len(runner.started) == 2

    # 释放门控，全部任务依次完成。
    gate.set()
    await consumer._drain()
    assert sorted(runner.completed) == ["s0", "s1", "s2", "s3", "s4"]
    # 全程峰值并发从未超过信号量容量。
    assert runner.peak_concurrent == 2


@pytest.mark.asyncio
async def test_max_concurrent_at_least_one():
    """max_concurrent 传入非正值时被纠正为至少 1（不至于死锁）。"""
    runner = FakeRunner()
    queue = FakeQueue()
    store = FakeSessionStore()
    consumer = _make_consumer(
        runner=runner, queue=queue, session_store=store, max_concurrent=0
    )
    consumer._schedule(_make_consumed("s0", "msg-0"))
    await consumer._drain()
    assert runner.completed == ["s0"]


# --------------------------------------------------------------------------- #
# AnalysisRunner error 事件发射（需求 5.7、7.7、2.4/2.5/2.10）
# --------------------------------------------------------------------------- #


class RecordingBus:
    """EventBus 替身：收集所有发射的 Progress_Event。"""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    async def emit(self, event: ProgressEvent) -> int:
        self.events.append(event)
        return 1


class _FakeGitHubClient:
    """GitHubClient 替身（异步上下文管理器）：可返回快照或抛出抓取错误。"""

    def __init__(self, *, snapshot=None, error: Exception | None = None) -> None:
        self._snapshot = snapshot
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch_snapshot(self, owner: str, repo: str):
        if self._error is not None:
            raise self._error
        return self._snapshot


class _FakePipeline:
    """AgentPipeline 替身：可正常返回或抛出指定异常。"""

    _instances: list["_FakePipeline"] = []

    def __init__(self, llm, event_bus, *, max_iterations=None) -> None:
        self.event_bus = event_bus
        self.ran = False
        _FakePipeline._instances.append(self)

    async def run(self, session_id: str, snapshot):
        self.ran = True
        return None


@pytest_asyncio.fixture
async def session_store():
    """基于 fakeredis 的真实 SessionStore（decode_responses=True）。"""
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield SessionStore(client)
    await client.aclose()


async def _create_queued(store: SessionStore, session_id: str) -> None:
    await store.create_session(
        session_id=session_id,
        repo_url=f"https://github.com/octocat/{session_id}",
        owner="octocat",
        repo=session_id,
    )


def _make_runner(store: SessionStore, bus: RecordingBus) -> AnalysisRunner:
    return AnalysisRunner(
        session_store=store,
        event_bus=bus,
        llm=object(),  # 被替身 pipeline 忽略
        settings=Settings(),
        http_client=object(),  # 被替身 GitHubClient 忽略
        task_queue=None,
    )


def _error_events(bus: RecordingBus) -> list[ProgressEvent]:
    return [e for e in bus.events if e.type == EventType.ERROR]


@pytest.mark.parametrize(
    "gh_error, expected_fragment",
    [
        (GitHubNotFoundError("仓库不存在或非公开"), "不存在"),
        (
            GitHubRateLimitError("速率限制", reset_at="2024-01-01T00:00:00Z"),
            "速率",
        ),
        (GitHubTimeoutError("3 次尝试仍超时"), "超时"),
    ],
)
@pytest.mark.asyncio
async def test_github_fetch_failure_emits_error_event(
    monkeypatch, session_store, gh_error, expected_fragment
):
    """GitHub 抓取失败发射 error 事件并将会话置 failed（需求 2.4/2.5/2.10、5.7）。"""
    bus = RecordingBus()
    runner = _make_runner(session_store, bus)
    await _create_queued(session_store, "sess-gh")

    fake_gh = _FakeGitHubClient(error=gh_error)
    monkeypatch.setattr(
        "app.worker.runner.GitHubClient",
        lambda settings, client=None: fake_gh,
    )

    await runner.run(
        {"session_id": "sess-gh", "owner": "octocat", "repo": "repo"}
    )

    # 恰好发射一条 error 事件，stage 指向抓取阶段，含失败原因描述。
    errors = _error_events(bus)
    assert len(errors) == 1
    payload = ErrorData.model_validate(errors[0].data)
    assert payload.stage == "github_fetch"
    assert expected_fragment in payload.message

    # 会话被置 failed 且记录了失败原因。
    session = await session_store.get_session("sess-gh")
    assert session.status is SessionStatus.FAILED
    assert session.error


@pytest.mark.asyncio
async def test_github_failure_skips_pipeline(monkeypatch, session_store):
    """GitHub 抓取失败时不进入流水线（需求 2.4）。"""
    bus = RecordingBus()
    runner = _make_runner(session_store, bus)
    await _create_queued(session_store, "sess-skip")

    fake_gh = _FakeGitHubClient(error=GitHubNotFoundError("不存在"))
    monkeypatch.setattr(
        "app.worker.runner.GitHubClient",
        lambda settings, client=None: fake_gh,
    )
    _FakePipeline._instances.clear()
    monkeypatch.setattr("app.worker.runner.AgentPipeline", _FakePipeline)

    await runner.run(
        {"session_id": "sess-skip", "owner": "octocat", "repo": "repo"}
    )

    # 流水线从未被构造/执行。
    assert _FakePipeline._instances == []


@pytest.mark.asyncio
async def test_pipeline_llm_exhaustion_emits_error_event(monkeypatch, session_store):
    """LLM 重试耗尽等流水线异常发射 error 事件并置 failed（需求 5.7、7.7）。"""
    bus = RecordingBus()
    runner = _make_runner(session_store, bus)
    await _create_queued(session_store, "sess-llm")

    # 抓取成功返回一个占位快照（值不重要，流水线被替身接管）。
    fake_gh = _FakeGitHubClient(snapshot=object())
    monkeypatch.setattr(
        "app.worker.runner.GitHubClient",
        lambda settings, client=None: fake_gh,
    )

    class _FailingPipeline(_FakePipeline):
        async def run(self, session_id: str, snapshot):
            raise LLMProviderError("重试耗尽仍失败")

    monkeypatch.setattr("app.worker.runner.AgentPipeline", _FailingPipeline)

    await runner.run(
        {"session_id": "sess-llm", "owner": "octocat", "repo": "repo"}
    )

    errors = _error_events(bus)
    assert len(errors) == 1
    payload = ErrorData.model_validate(errors[0].data)
    assert payload.stage == "agent_pipeline"
    assert "重试耗尽" in payload.message

    session = await session_store.get_session("sess-llm")
    assert session.status is SessionStatus.FAILED


@pytest.mark.asyncio
async def test_pipeline_unexpected_exception_emits_error_event(
    monkeypatch, session_store
):
    """流水线未捕获异常统一降级为 error 事件（需求 5.7）。"""
    bus = RecordingBus()
    runner = _make_runner(session_store, bus)
    await _create_queued(session_store, "sess-boom")

    fake_gh = _FakeGitHubClient(snapshot=object())
    monkeypatch.setattr(
        "app.worker.runner.GitHubClient",
        lambda settings, client=None: fake_gh,
    )

    class _BoomPipeline(_FakePipeline):
        async def run(self, session_id: str, snapshot):
            raise ValueError("意外崩溃")

    monkeypatch.setattr("app.worker.runner.AgentPipeline", _BoomPipeline)

    await runner.run(
        {"session_id": "sess-boom", "owner": "octocat", "repo": "repo"}
    )

    errors = _error_events(bus)
    assert len(errors) == 1
    assert ErrorData.model_validate(errors[0].data).stage == "agent_pipeline"

    session = await session_store.get_session("sess-boom")
    assert session.status is SessionStatus.FAILED


@pytest.mark.asyncio
async def test_successful_run_emits_no_error_and_completes(monkeypatch, session_store):
    """成功路径不发射 error 事件，会话置 completed（对照组）。"""
    bus = RecordingBus()
    runner = _make_runner(session_store, bus)
    await _create_queued(session_store, "sess-ok")

    fake_gh = _FakeGitHubClient(snapshot=object())
    monkeypatch.setattr(
        "app.worker.runner.GitHubClient",
        lambda settings, client=None: fake_gh,
    )
    _FakePipeline._instances.clear()
    monkeypatch.setattr("app.worker.runner.AgentPipeline", _FakePipeline)

    await runner.run(
        {"session_id": "sess-ok", "owner": "octocat", "repo": "repo"}
    )

    assert _error_events(bus) == []
    assert _FakePipeline._instances[0].ran is True
    session = await session_store.get_session("sess-ok")
    assert session.status is SessionStatus.COMPLETED
