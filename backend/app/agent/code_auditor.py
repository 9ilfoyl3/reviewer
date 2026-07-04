"""Code_Auditor —— 代码审计 Agent（任务 6.4）。

对应需求 4.1、4.9 与设计文档「Agent 基类与三角色」。

Code_Auditor 继承 ``BaseReActAgent``，评估仓库目录结构与核心代码质量，
提交的结论至少包含 1 个优点与 1 个改进点（需求 4.9）。结论以结构化 JSON
承载于 ``AgentConclusion.data``，字段与 ``CodeAuditorOpinion`` 对齐
（``strengths`` / ``improvements`` / ``summary``），供 Final_Judge 直接消费。
"""

from __future__ import annotations

from .base import AgentConclusion, BaseReActAgent, Observation
from .prompts.code_auditor import code_auditor_system_prompt


class CodeAuditor(BaseReActAgent):
    """代码审计 Agent（Agent A）。

    职责：评估目录结构与核心代码质量，给出技术意见（≥1 优点 + ≥1 改进点，需求 4.9）。
    """

    role = "Code_Auditor"

    def system_prompt(self, snapshot_ctx: str) -> str:
        """返回 Code_Auditor 的系统提示词（强制结构化 JSON 提交结论）。"""

        return code_auditor_system_prompt(snapshot_ctx)

    def synthesize_fallback(
        self, observations: list[Observation]
    ) -> AgentConclusion:
        """达最大轮数仍未提交结论时的兜底合成（需求 4.8、4.9）。

        基于已获得的观察结果合成一个满足"≥1 优点 + ≥1 改进点"约束的结论，
        保证流水线不悬挂且下游可消费。
        """

        # 统计成功读取到内容的观察，作为兜底优点/改进点的证据。
        read_targets = [
            obs.args.get("path") or obs.tool
            for obs in observations
            if obs.success and obs.output
        ]
        evidence = "、".join(str(t) for t in read_targets[:5]) or "有限的可用信息"

        strengths = [
            f"项目提供了可供审计的目录结构与代码（已考察：{evidence}），"
            "具备基本的工程组织。"
        ]
        improvements = [
            "在最大分析轮数内未能收集到足够证据形成完整审计结论，"
            "建议补充关键模块的代码可读性与测试覆盖信息以便进一步评估。"
        ]

        return AgentConclusion(
            role=self.role,
            data={
                "strengths": strengths,
                "improvements": improvements,
                "summary": (
                    "因达到最大分析轮数，基于已有观察合成的兜底技术意见；"
                    "结论覆盖至少 1 个优点与 1 个改进点。"
                ),
            },
            raw_text="",
        )
