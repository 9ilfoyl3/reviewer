"""Agent_Pipeline —— 多 Agent ReAct 协作流水线编排器（任务 6.8）。

对应需求 4.1、4.11、4.12、4.14、5.6、6.1 与设计文档「多 Agent ReAct 流水线
设计 → Agent 基类与三角色 → 编排顺序」。

编排逻辑：

1. 构建绑定 Repository_Snapshot 的工具注册表与共享 ``SeqCounter``（保证同一
   会话内所有 Agent 的事件 ``seq`` 单调递增，前端按序渲染）。
2. **并行执行** Code_Auditor 与 Product_Value_Agent——二者相互独立，用
   ``asyncio.gather`` 并发运行以缩短总时长（需求 4.11）。
3. 两者结论就绪后作为输入传给 Final_Judge 合成总分与综合建议，组装
   Health_Report（需求 4.11、4.12、6.1）。
4. 发射 ``final_report`` 事件，其 data 为完整 Health_Report（需求 5.6）。

流水线自身不触碰网络：GitHub 抓取在上游 Worker runner 完成并归一化为
Repository_Snapshot，LLM 调用经注入的 ``LLMProvider``；工具对内存中的
Snapshot 做只读操作。各 Agent 的生命周期/工具/结论事件由 Agent 自身发射
（需求 4.14），``final_report`` 由本编排器统一发射（需求 5.6）。
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..events.types import EventType, FinalReportData, ProgressEvent
from ..llm.provider import LLMProvider
from ..models.report import HealthReport
from ..models.snapshot import RepositorySnapshot
from .base import EventEmitter, PipelineContext, SeqCounter
from .code_auditor import CodeAuditor
from .final_judge import FinalJudge
from .product_value import ProductValueAgent
from .tools.base import build_default_registry

logger = logging.getLogger(__name__)


class AgentPipeline:
    """多 Agent ReAct 协作流水线编排器（需求 4.1、4.11、4.12、4.14、5.6、6.1）。

    编排 Code_Auditor（A）、Product_Value_Agent（B）、Final_Judge（C）三个角色：
    A、B 并行执行，结论就绪后传给 C 合成 Health_Report 并发射 final_report。
    """

    def __init__(
        self,
        llm: LLMProvider,
        event_bus: EventEmitter,
        *,
        max_iterations: int | None = None,
    ) -> None:
        """初始化流水线编排器。

        Args:
            llm: LLM_Provider 客户端，注入各 Agent（需求 7）。
            event_bus: Progress_Event 发射器（需求 4.14、5.6）。
            max_iterations: 各 Agent ReAct 最大轮数；``None`` 时由 Agent 从配置
                读取默认值（默认 8、范围 1–20，需求 4.7）。
        """
        self._llm = llm
        self._event_bus = event_bus
        self._max_iterations = max_iterations

    async def run(
        self, session_id: str, snapshot: RepositorySnapshot
    ) -> HealthReport:
        """执行完整流水线并返回 Health_Report（需求 4.11、4.12、5.6、6.1）。

        Args:
            session_id: 归属 Analysis_Session，用于事件路由。
            snapshot: 已抓取归一化的 Repository_Snapshot（内存只读）。

        Returns:
            合成完成的 Health_Report（同时已通过 ``final_report`` 事件推送）。
        """
        context = PipelineContext(session_id=session_id, snapshot=snapshot)

        # 会话级共享序号生成器：三个 Agent 共用，保证 seq 全局单调递增。
        seq_counter = SeqCounter()
        # 工具注册表绑定同一 Snapshot（A、B 只读共享，纯函数式、并发安全）。
        tools = build_default_registry(snapshot)

        code_auditor = CodeAuditor(
            self._llm,
            tools,
            self._event_bus,
            session_id=session_id,
            max_iterations=self._max_iterations,
            seq_counter=seq_counter,
        )
        product_value = ProductValueAgent(
            self._llm,
            tools,
            self._event_bus,
            session_id=session_id,
            max_iterations=self._max_iterations,
            seq_counter=seq_counter,
        )
        final_judge = FinalJudge(
            self._llm,
            self._event_bus,
            session_id=session_id,
            seq_counter=seq_counter,
        )

        # A、B 相互独立 → 并行执行（需求 4.11）。
        logger.info("流水线启动：并行执行 Code_Auditor 与 Product_Value_Agent（会话 %s）", session_id)
        code_conclusion, product_conclusion = await asyncio.gather(
            code_auditor.run(context),
            product_value.run(context),
        )

        # A、B 结论就绪 → 传给 Final_Judge 合成（需求 4.11、4.12、6.1）。
        logger.info("A/B 结论就绪，交由 Final_Judge 合成（会话 %s）", session_id)
        report = await final_judge.run(
            context, code_conclusion, product_conclusion
        )

        # 发射 final_report 事件，data 为完整 Health_Report（需求 5.6）。
        await self._emit_final_report(session_id, seq_counter, report)
        logger.info("流水线完成，已发射 final_report（会话 %s，总分 %d）", session_id, report.score)
        return report

    async def _emit_final_report(
        self, session_id: str, seq_counter: SeqCounter, report: HealthReport
    ) -> None:
        """发射 final_report 类型的 Progress_Event（需求 5.6）。"""

        event = ProgressEvent(
            type=EventType.FINAL_REPORT,
            session_id=session_id,
            agent=FinalJudge.role,
            seq=seq_counter.next(),
            data=FinalReportData(report=report).model_dump(),
            ts=time.time(),
        )
        await self._event_bus.emit(event)
