"""单任务执行编排：GitHub 抓取 → Agent_Pipeline → 会话状态流转（任务 8.4）。

对应设计文档「后端并发与队列设计」与「Worker 执行层」职责：Worker 从队列取到
一条体检任务后，由 :class:`AnalysisRunner` 完成**单个** Analysis_Session 的端到端
执行：

    会话置 running --> GitHub 抓取归一化 Snapshot --> Agent_Pipeline 流水线
        --> final_report 已推送 --> 会话置 completed

失败处理（需求 2.4/2.5/2.10、5.7、7.7）：
  - GitHub 抓取失败（仓库不存在 / 速率限制 / 超时）→ 发 error 事件、会话置 failed，
    且不生成 Snapshot、不进入流水线（需求 2.4、2.5、2.10）。
  - LLM 重试耗尽或流水线未捕获异常 → 发 error 事件、会话置 failed（需求 5.7、7.7）。
  - 无论成功或失败，最终都释放 ``(owner, repo)`` 去重键，允许后续重新体检。

并发与连接：GitHub 抓取复用注入的、**有界连接池**的 ``httpx.AsyncClient``
（``httpx.Limits(max_connections=...)``），避免多任务并发时打爆下游连接；LLM
调用经注入的 ``LLMProvider``（其自带 60s 超时的 httpx 客户端）。执行侧全程
``asyncio + httpx``，单任务在独立协程中运行，失败隔离由上层 Consumer 保证。

_Requirements: 5.7, 7.7_
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from ..agent.base import SeqCounter
from ..agent.pipeline import AgentPipeline
from ..config import Settings
from ..db.review_repo import ReviewRepository
from ..events.aggregate import aggregate_agents
from ..events.event_bus import ReviewEventBus
from ..events.types import ErrorData, EventType, ProgressEvent
from ..github.client import GitHubClient
from ..github.errors import GitHubClientError
from ..db.model_config_repo import ModelConfigRepository
from ..llm.provider import LLMProvider, LLMProviderError
from ..models.report import serialize_report
from ..queue.session_store import SessionStore
from ..queue.task_queue import TaskQueue

logger = logging.getLogger(__name__)


class _TracingEventBus:
    """事件总线包装：转发 emit 到真实总线的同时收集事件，用于过程落库。

    每个任务独立构造一个实例，收集本会话流水线发射的全部 Progress_Event
    （``model_dump()`` 后的 dict），供 :func:`aggregate_agents` 聚合后持久化，
    使刷新/历史回看时仍能还原多 Agent 协作过程。
    """

    def __init__(self, delegate: ReviewEventBus) -> None:
        self._delegate = delegate
        self.events: list[dict] = []

    async def emit(self, event: ProgressEvent) -> int:
        self.events.append(event.model_dump(mode="json"))
        return await self._delegate.emit(event)


def _serialize_trace(tracer: "_TracingEventBus | None") -> str | None:
    """把 tracer 收集的事件聚合为 Agent 过程视图 JSON（无过程时返回 None）。"""
    if tracer is None or not tracer.events:
        return None
    try:
        agents = aggregate_agents(tracer.events)
        if not agents:
            return None
        return json.dumps(agents, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - 过程落库失败不应影响主流程
        logger.debug("聚合 Agent 过程失败", exc_info=True)
        return None


class AnalysisRunner:
    """单个体检任务的执行编排器（需求 5.7、7.7）。

    由 Worker 进程构造一次、供其内所有并发任务复用（无状态、线程/协程安全）：
    共享注入的 ``SessionStore`` / ``ReviewEventBus`` / ``LLMProvider`` /
    有界 ``httpx.AsyncClient`` / ``TaskQueue``。每次 :meth:`run` 处理一条任务，
    自身完整处理其失败路径（发 error 事件 + 置 failed + 释放去重键），不会因
    单任务失败而抛出影响其它任务——失败隔离的最终保障仍由 Consumer 的
    ``try/except`` 提供。
    """

    def __init__(
        self,
        *,
        session_store: SessionStore,
        event_bus: ReviewEventBus,
        llm: LLMProvider,
        settings: Settings,
        http_client: httpx.AsyncClient,
        task_queue: TaskQueue | None = None,
        review_repo: ReviewRepository | None = None,
        model_config_repo: ModelConfigRepository | None = None,
    ) -> None:
        """初始化执行编排器。

        Args:
            session_store: 会话状态存储（running / completed / failed 流转）。
            event_bus: Worker 侧事件总线（发 error 等 Progress_Event 到 Pub/Sub）。
            llm: LLM_Provider 客户端，注入 Agent_Pipeline。
            settings: 应用配置（GitHub token、Agent 轮数上限等）。
            http_client: **有界连接池**的 httpx 客户端，供 GitHub 抓取复用，
                须以 ``base_url=GITHUB_API_BASE`` 构造。
            task_queue: 任务队列，用于任务结束后释放 ``(owner, repo)`` 去重键；
                为 None 时跳过去重释放（便于测试）。
        """
        self._session_store = session_store
        self._event_bus = event_bus
        self._llm = llm
        self._settings = settings
        self._http_client = http_client
        self._task_queue = task_queue
        # 体检历史落库（None 时跳过，便于测试）。
        self._review_repo = review_repo
        # 模型配置仓储：优先用前端配置的默认模型，缺省回退注入的 env LLM（None 时跳过）。
        self._model_config_repo = model_config_repo

    async def run(self, payload: dict) -> None:
        """执行单个体检任务的端到端编排（需求 5.7、7.7）。

        Args:
            payload: 队列任务载荷，含 ``session_id`` / ``owner`` / ``repo`` /
                ``repo_url``（见 :meth:`TaskQueue.enqueue`）。

        本方法自身完整处理成功与失败路径，正常情况下不向上抛出异常；仅当会话状态
        流转等基础设施出现意外错误时才可能抛出，由上层 Consumer 兜底隔离。
        """
        session_id = str(payload.get("session_id", ""))
        owner = str(payload.get("owner", ""))
        repo = str(payload.get("repo", ""))
        # 会话级序号生成器：供本 runner 发射的 error 事件使用（正常事件由流水线内部发射）。
        seq = SeqCounter()

        # 本任务实际使用的 LLM（默认注入的 env LLM；若前端配置了默认模型则临时构造）。
        llm = self._llm
        llm_owned = False
        try:
            # 1) 会话置 running（queued -> running）。
            await self._session_store.mark_running(session_id)
            await self._history_running(session_id)
            logger.info("开始执行体检任务：会话 %s 仓库 %s/%s", session_id, owner, repo)

            # 2) GitHub 抓取并归一化为 Repository_Snapshot（复用有界连接池）。
            try:
                async with GitHubClient(
                    self._settings, client=self._http_client
                ) as gh:
                    snapshot = await gh.fetch_snapshot(owner, repo)
            except GitHubClientError as exc:
                # 仓库不存在 / 速率限制 / 超时：发 error、置 failed、不生成 Snapshot
                # （需求 2.4、2.5、2.10）。
                await self._fail(session_id, seq, stage="github_fetch", error=exc)
                return

            # 2.5) 选用前端配置的默认模型（若有），否则回退注入的 env LLM。
            llm, llm_owned = await self._resolve_llm()

            # 3) 执行多 Agent 流水线（内部发射 agent/thought/tool/final_report 事件）。
            #    用 tracer 包装事件总线，收集过程事件以便落库、支持刷新回看。
            tracer = _TracingEventBus(self._event_bus)
            try:
                pipeline = AgentPipeline(
                    llm,
                    tracer,
                    max_iterations=self._settings.agent_max_iterations,
                )
                report = await pipeline.run(session_id, snapshot)
            except LLMProviderError as exc:
                # LLM 重试耗尽等推理失败：发 error、置 failed（需求 7.7）。
                await self._fail(
                    session_id, seq, stage="agent_pipeline", error=exc, tracer=tracer
                )
                return
            except Exception as exc:  # noqa: BLE001 - 流水线未捕获异常统一降级（需求 5.7）
                await self._fail(
                    session_id, seq, stage="agent_pipeline", error=exc, tracer=tracer
                )
                return

            # 4) final_report 已推送 → 会话置 completed（running -> completed）。
            await self._session_store.mark_completed(session_id)
            await self._history_completed(
                session_id, report, agents_json=_serialize_trace(tracer)
            )
            logger.info("体检任务完成：会话 %s 仓库 %s/%s", session_id, owner, repo)
        finally:
            # 若本任务临时构造了 LLM（前端默认模型），用完即释放其连接。
            if llm_owned:
                try:
                    await llm.close()
                except Exception:  # noqa: BLE001
                    logger.debug("释放临时 LLM 连接失败", exc_info=True)
            # 无论成功失败均释放去重键，允许后续对同仓库重新体检。
            await self._release_dedup(owner, repo)

    async def _fail(
        self,
        session_id: str,
        seq: SeqCounter,
        *,
        stage: str,
        error: BaseException,
        tracer: "_TracingEventBus | None" = None,
    ) -> None:
        """失败降级：发 error 事件并将会话置 failed（需求 2.4/2.5/2.10、5.7、7.7）。

        Args:
            session_id: 目标会话。
            seq: 会话级序号生成器（error 事件的 seq 由其分配）。
            stage: 失败所处阶段（``github_fetch`` / ``agent_pipeline``）。
            error: 触发失败的异常，其消息作为 error 事件与会话 error 字段内容。
        """
        message = str(error) or error.__class__.__name__
        logger.warning(
            "体检任务失败：会话 %s 阶段 %s 原因 %s", session_id, stage, message
        )
        # 先发 error 事件（需求 5.7），再置会话 failed，保证前端收到失败原因。
        await self._emit_error(session_id, seq, message=message, stage=stage)
        try:
            await self._session_store.mark_failed(session_id, message)
        except Exception:  # noqa: BLE001 - 状态流转失败不应掩盖原始错误
            logger.exception("将会话 %s 置 failed 时出错", session_id)
        await self._history_failed(
            session_id, message, agents_json=_serialize_trace(tracer)
        )

    # ---- 体检历史落库（失败不阻断主流程） ----

    async def _history_running(self, session_id: str) -> None:
        if self._review_repo is None:
            return
        try:
            await self._review_repo.mark_running(session_id)
        except Exception:  # noqa: BLE001
            logger.debug("更新体检历史 running 失败：%s", session_id, exc_info=True)

    async def _history_completed(
        self, session_id: str, report, *, agents_json: str | None = None
    ) -> None:
        if self._review_repo is None:
            return
        try:
            await self._review_repo.mark_completed(
                session_id,
                score=int(report.score),
                report_json=serialize_report(report),
                agents_json=agents_json,
            )
        except Exception:  # noqa: BLE001
            logger.debug("更新体检历史 completed 失败：%s", session_id, exc_info=True)

    async def _history_failed(
        self, session_id: str, message: str, *, agents_json: str | None = None
    ) -> None:
        if self._review_repo is None:
            return
        try:
            await self._review_repo.mark_failed(
                session_id, message, agents_json=agents_json
            )
        except Exception:  # noqa: BLE001
            logger.debug("更新体检历史 failed 失败：%s", session_id, exc_info=True)

    async def _resolve_llm(self) -> tuple[LLMProvider, bool]:
        """解析本次任务使用的 LLM。

        Returns:
            ``(llm, owned)``：``owned`` 为 True 表示是临时构造的、用完需关闭的实例；
            为 False 表示复用注入的 env LLM（由 Worker 生命周期统一关闭）。
        """
        if self._model_config_repo is None:
            return self._llm, False
        try:
            config = await self._model_config_repo.get_default()
        except Exception:  # noqa: BLE001 - 取配置失败回退 env LLM
            logger.debug("读取默认模型配置失败，回退环境变量 LLM", exc_info=True)
            return self._llm, False
        if config is None:
            return self._llm, False
        try:
            llm = LLMProvider(
                base_url=config.base_url,
                api_key=config.api_key or "",
                model=config.model,
            )
        except LLMProviderError:
            logger.warning("默认模型配置无效，回退环境变量 LLM：%s", config.name)
            return self._llm, False
        logger.info("使用前端配置的默认模型：%s（%s）", config.name, config.model)
        return llm, True

    async def _emit_error(
        self, session_id: str, seq: SeqCounter, *, message: str, stage: str
    ) -> None:
        """发射一条 error 类型 Progress_Event（需求 5.7）。"""
        event = ProgressEvent(
            type=EventType.ERROR,
            session_id=session_id,
            agent=None,
            seq=seq.next(),
            data=ErrorData(message=message, stage=stage).model_dump(),
            ts=time.time(),
        )
        await self._event_bus.emit(event)

    async def _release_dedup(self, owner: str, repo: str) -> None:
        """释放 ``(owner, repo)`` 去重键（会话进入终态后调用）。"""
        if self._task_queue is None:
            return
        try:
            await self._task_queue.release_dedup(owner, repo)
        except Exception:  # noqa: BLE001 - 去重释放失败仅记录，有 TTL 兜底
            logger.exception("释放去重键失败：%s/%s", owner, repo)
