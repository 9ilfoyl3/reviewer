"""Final_Judge —— 分数钳制纯函数与合成裁判（任务 6.5、6.8）。

对应需求 4.11、4.12、4.13、6.1、6.2、6.3 与设计文档「Agent 基类与三角色 →
Final_Judge」「Final_Judge 分数钳制」小节。

本模块包含两部分：

1. ``clamp_score``：独立、可单测的纯函数，作用在 Final_Judge 输出与写入
   Health_Report 之间，保证 ``score`` 恒为 0–100 的整数（需求 4.13、6.2、6.3）。
2. ``FinalJudge``：合成裁判 Agent。汇总 Code_Auditor 与 Product_Value_Agent
   的结论，经单轮 LLM 合成得到 0–100 整数总分（过 ``clamp_score``）与 3–10 条
   综合建议，并组装成完整 ``Health_Report``（需求 4.11、4.12、6.1）。

设计要点（防御式）：即便模型越界、格式异常或字段缺失，也由 ``clamp_score``、
建议条数规整与结论字段兜底保证 Health_Report 合法，不依赖模型自觉。
"""

from __future__ import annotations

import json
import logging
import re
import time

from ..events.types import (
    AgentLifecycleData,
    EventType,
    ProgressEvent,
    ThoughtData,
)
from ..llm.provider import LLMProvider
from ..models.report import (
    CodeAuditorOpinion,
    HealthReport,
    LanguagePercent,
    MetadataSummary,
    ProductValueOpinion,
)
from ..models.snapshot import RepositorySnapshot
from .base import AgentConclusion, EventEmitter, PipelineContext, SeqCounter
from .prompts.final_judge import final_judge_system_prompt

logger = logging.getLogger(__name__)

# 综合优化建议条数上下限（需求 4.12）。
MIN_RECOMMENDATIONS = 3
MAX_RECOMMENDATIONS = 10


def clamp_score(raw: float | int | None) -> int:
    """将 Final_Judge 产出的原始分数钳制/修正为 [0, 100] 的整数。

    - ``None``（缺失）→ 修正为 0；
    - 非整数 → 四舍五入取整；
    - 无法转换为数值 → 修正为 0；
    - 越界 → 钳制到 [0, 100] 边界值。

    对应需求 4.13、6.2、6.3。
    边界：``-1 → 0``、``0 → 0``、``50 → 50``、``100 → 100``、``101 → 100``。
    """

    if raw is None:
        return 0                          # 缺失 → 修正为 0
    try:
        v = int(round(float(raw)))        # 非整数 → 取整
    except (TypeError, ValueError, OverflowError):
        # 无法转换为数值（含 NaN / 无穷等特殊浮点）→ 修正为 0
        return 0
    return max(0, min(100, v))            # 越界 → 钳制到 [0,100]


def normalize_language_distribution(
    languages: dict[str, int],
) -> list[LanguagePercent]:
    """将语言字节分布归一化为占比列表，且各占比之和恒为 100%（需求 6.6）。

    采用「最大余数法」（Largest Remainder Method）做四舍五入补偿：先按比例取整，
    再将因取整损失的余量按小数部分从大到小逐一补 1，确保占比之和精确等于 100。
    语言分布为空或总字节为 0 时返回空列表（无可归一化数据）。
    """

    # 过滤非正字节数，避免负数/零干扰归一化。
    valid = {name: b for name, b in languages.items() if b and b > 0}
    total = sum(valid.values())
    if total <= 0:
        return []

    # 先算每种语言的精确占比与向下取整值，记录小数余数用于补偿。
    floors: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    for name, byte_count in valid.items():
        exact = byte_count / total * 100
        floor = int(exact)
        floors[name] = floor
        remainders.append((exact - floor, name))

    # 需要补偿的整数余量 = 100 - 各向下取整值之和。
    remaining = 100 - sum(floors.values())
    # 按小数部分从大到小补 1，稳定顺序（相同余数按语言名）保证结果确定。
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for i in range(remaining):
        _, name = remainders[i % len(remainders)]
        floors[name] += 1

    # 保持输入的语言顺序输出。
    return [
        LanguagePercent(name=name, percent=float(floors[name]))
        for name in valid
    ]


def _ensure_nonempty(values: object, fallback: str) -> list[str]:
    """将结论字段规整为非空字符串列表；缺失/为空时以 fallback 兜底。"""

    if isinstance(values, list):
        items = [str(v).strip() for v in values if str(v).strip()]
        if items:
            return items
    elif isinstance(values, str) and values.strip():
        return [values.strip()]
    return [fallback]


class FinalJudge:
    """Final_Judge 合成裁判（Agent C，需求 4.11、4.12、6.1）。

    不同于 Code_Auditor / Product_Value_Agent 的多轮工具型 ReAct，Final_Judge
    的职责是「合成」：接收 A、B 两位 Agent 的结论，经单轮 LLM 合成得到总分与
    综合建议，再组装成完整 Health_Report。重点在分数钳制与建议聚合的防御式兜底。

    事件语义（需求 4.14）：发射 ``agent_start`` → 流式 ``thought`` → ``agent_complete``；
    ``final_report`` 事件由上层 Agent_Pipeline 统一发射（需求 5.6）。
    """

    role = "Final_Judge"

    def __init__(
        self,
        llm: LLMProvider,
        event_bus: EventEmitter,
        *,
        session_id: str | None = None,
        seq_counter: SeqCounter | None = None,
        temperature: float = 0.3,
    ) -> None:
        """初始化合成裁判。

        Args:
            llm: LLM_Provider 客户端（需求 7）。
            event_bus: Progress_Event 发射器（需求 4.14）。
            session_id: 归属会话；亦可在 ``run`` 时由 ``PipelineContext`` 提供。
            seq_counter: 会话级共享序号生成器；未提供时自建。
            temperature: 采样温度。
        """
        self.llm = llm
        self._event_bus = event_bus
        self._session_id = session_id
        self._seq = seq_counter or SeqCounter()
        self._temperature = temperature

    async def run(
        self,
        context: PipelineContext,
        code_conclusion: AgentConclusion,
        product_conclusion: AgentConclusion,
    ) -> HealthReport:
        """汇总 A、B 结论并合成 Health_Report（需求 4.11、4.12、6.1）。

        流程：

        1. 发射 ``agent_start``（需求 4.14）。
        2. 单轮流式调用 LLM，逐 token 发射 ``thought``，产出含 score 与
           recommendations 的 JSON（需求 4.12、7.4）。
        3. 分数过 ``clamp_score`` 钳制、建议规整到 3–10 条（防御式兜底）。
        4. 组装五部分 Health_Report（需求 6.1）并返回。
        5. 发射 ``agent_complete``（需求 4.14）。
        """
        self._session_id = context.session_id
        await self._emit_agent_start()

        # 从 A、B 结论提取五部分中的两部分意见（缺失字段防御式兜底）。
        code_opinion = self._build_code_opinion(code_conclusion)
        product_opinion = self._build_product_opinion(product_conclusion)

        # 单轮流式合成总分与综合建议。
        raw_text = await self._synthesize(code_opinion, product_opinion)
        parsed = self._extract_json(raw_text)

        # 分数钳制（需求 4.13、6.2、6.3）。
        score = clamp_score(parsed.get("score"))
        # 建议规整到 3–10 条（需求 4.12）。
        recommendations = self._normalize_recommendations(
            parsed.get("recommendations"), code_opinion, product_opinion
        )

        report = HealthReport(
            metadata_summary=self._build_metadata_summary(context.snapshot),
            code_auditor=code_opinion,
            product_value=product_opinion,
            recommendations=recommendations,
            score=score,
        )

        await self._emit_agent_complete(report)
        return report

    # ------------------------------------------------------------------ #
    # 合成与解析
    # ------------------------------------------------------------------ #

    async def _synthesize(
        self,
        code_opinion: CodeAuditorOpinion,
        product_opinion: ProductValueOpinion,
    ) -> str:
        """单轮流式调用 LLM 产出 score + recommendations 的 JSON 文本。"""

        messages = [
            {
                "role": "system",
                "content": final_judge_system_prompt(
                    self._format_code_opinion(code_opinion),
                    self._format_product_opinion(product_opinion),
                ),
            },
            {
                "role": "user",
                "content": (
                    "请综合以上两位 Agent 的结论，给出 0–100 的整数总分与 "
                    "3–10 条综合优化建议，并严格按要求的 JSON 结构输出。"
                ),
            },
        ]

        content_parts: list[str] = []
        # 合成裁判不需要工具，故不传 tools。
        async for chunk in self.llm.stream_with_tools(
            messages, None, self._temperature
        ):
            if chunk.content:
                content_parts.append(chunk.content)
                await self._emit_thought(chunk.content, iteration=1)
        return "".join(content_parts)

    def _build_code_opinion(
        self, conclusion: AgentConclusion
    ) -> CodeAuditorOpinion:
        """从 Code_Auditor 结论组装 CodeAuditorOpinion（≥1 优点 + ≥1 改进点）。"""

        data = conclusion.data or {}
        return CodeAuditorOpinion(
            strengths=_ensure_nonempty(
                data.get("strengths"),
                "项目具备可供审计的基本工程结构。",
            ),
            improvements=_ensure_nonempty(
                data.get("improvements"),
                "建议补充测试覆盖与关键模块文档以提升可维护性。",
            ),
            summary=self._coerce_summary(
                data.get("summary") or conclusion.raw_text,
                "代码审计结论摘要暂缺。",
            ),
        )

    def _build_product_opinion(
        self, conclusion: AgentConclusion
    ) -> ProductValueOpinion:
        """从 Product_Value_Agent 结论组装 ProductValueOpinion（三维度各 ≥1 条）。"""

        data = conclusion.data or {}
        return ProductValueOpinion(
            readme_clarity=_ensure_nonempty(
                data.get("readme_clarity"),
                "建议完善 README 的简介、安装与使用说明以提升清晰度。",
            ),
            practical_value=_ensure_nonempty(
                data.get("practical_value"),
                "建议明确项目定位与适用场景以凸显实用价值。",
            ),
            activeness=_ensure_nonempty(
                data.get("activeness"),
                "建议结合 Star/Fork 与最近提交时间综合判断活跃度。",
            ),
            summary=self._coerce_summary(
                data.get("summary") or conclusion.raw_text,
                "产品价值结论摘要暂缺。",
            ),
        )

    @staticmethod
    def _coerce_summary(value: object, fallback: str) -> str:
        """将 summary 规整为非空字符串。"""

        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    def _normalize_recommendations(
        self,
        raw: object,
        code_opinion: CodeAuditorOpinion,
        product_opinion: ProductValueOpinion,
    ) -> list[str]:
        """将建议规整到 3–10 条（需求 4.12）。

        - 去重、去空白；
        - 不足 3 条时，用 A/B 的改进点补足，仍不足则用通用兜底建议补齐；
        - 超过 10 条时截断保留前 10 条。
        """

        items: list[str] = []
        seen: set[str] = set()

        def _add(candidate: object) -> None:
            text = str(candidate).strip()
            if text and text not in seen:
                seen.add(text)
                items.append(text)

        if isinstance(raw, list):
            for entry in raw:
                _add(entry)
        elif isinstance(raw, str) and raw.strip():
            _add(raw)

        # 不足下限：从 A/B 改进点补足。
        if len(items) < MIN_RECOMMENDATIONS:
            for candidate in (
                *code_opinion.improvements,
                *product_opinion.readme_clarity,
                *product_opinion.practical_value,
                *product_opinion.activeness,
            ):
                if len(items) >= MIN_RECOMMENDATIONS:
                    break
                _add(candidate)

        # 仍不足：用通用兜底建议补齐到下限。
        generic_fallbacks = [
            "完善 README 文档，补充项目简介、安装步骤与使用示例。",
            "增加自动化测试并提升测试覆盖率，保障核心逻辑质量。",
            "梳理目录结构与模块职责，降低耦合、提升可维护性。",
        ]
        for candidate in generic_fallbacks:
            if len(items) >= MIN_RECOMMENDATIONS:
                break
            _add(candidate)

        # 超过上限：截断到 10 条。
        return items[:MAX_RECOMMENDATIONS]

    @staticmethod
    def _build_metadata_summary(snapshot: RepositorySnapshot) -> MetadataSummary:
        """组装报告部分 1：元数据摘要（Star/Fork 整数 + 语言占比归一化）。"""

        meta = snapshot.metadata
        return MetadataSummary(
            stars=meta.stars,
            forks=meta.forks,
            language_distribution=normalize_language_distribution(meta.languages),
        )

    @staticmethod
    def _format_code_opinion(opinion: CodeAuditorOpinion) -> str:
        """将 Code_Auditor 意见格式化为提示词上下文文本。"""

        strengths = "；".join(opinion.strengths)
        improvements = "；".join(opinion.improvements)
        return (
            f"优点：{strengths}\n"
            f"改进点：{improvements}\n"
            f"整体评价：{opinion.summary}"
        )

    @staticmethod
    def _format_product_opinion(opinion: ProductValueOpinion) -> str:
        """将 Product_Value_Agent 意见格式化为提示词上下文文本。"""

        return (
            f"README 清晰度：{'；'.join(opinion.readme_clarity)}\n"
            f"实用价值：{'；'.join(opinion.practical_value)}\n"
            f"活跃度：{'；'.join(opinion.activeness)}\n"
            f"整体评价：{opinion.summary}"
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

    # ------------------------------------------------------------------ #
    # 事件发射（需求 4.14）
    # ------------------------------------------------------------------ #

    async def _emit(self, event_type: EventType, data: dict) -> None:
        """构造并发射一条 Progress_Event（seq 单调递增）。"""

        event = ProgressEvent(
            type=event_type,
            session_id=self._session_id or "",
            agent=self.role,
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

    async def _emit_agent_complete(self, report: HealthReport) -> None:
        """发射 agent_complete 事件（需求 4.14）。"""

        summary = f"总分 {report.score}，综合建议 {len(report.recommendations)} 条"
        await self._emit(
            EventType.AGENT_COMPLETE,
            AgentLifecycleData(agent=self.role, conclusion=summary).model_dump(),
        )
