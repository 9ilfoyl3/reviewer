"""Product_Value_Agent —— 产品价值 Agent（任务 6.4）。

对应需求 4.1、4.10 与设计文档「Agent 基类与三角色」。

Product_Value_Agent 继承 ``BaseReActAgent``，从 README 清晰度、实用价值与
开源活跃度三个维度评估仓库产品价值，每个维度至少 1 条评估结论（需求 4.10）。
结论以结构化 JSON 承载于 ``AgentConclusion.data``，字段与 ``ProductValueOpinion``
对齐（``readme_clarity`` / ``practical_value`` / ``activeness`` / ``summary``）。
"""

from __future__ import annotations

from .base import AgentConclusion, BaseReActAgent, Observation
from .prompts.product_value import product_value_system_prompt


class ProductValueAgent(BaseReActAgent):
    """产品价值 Agent（Agent B）。

    职责：评估 README 清晰度、实用价值与开源活跃度三个维度，
    每维度至少 1 条评估结论（需求 4.10）。
    """

    role = "Product_Value_Agent"

    def system_prompt(self, snapshot_ctx: str) -> str:
        """返回 Product_Value_Agent 的系统提示词（强制结构化 JSON 提交结论）。"""

        return product_value_system_prompt(snapshot_ctx)

    def synthesize_fallback(
        self, observations: list[Observation]
    ) -> AgentConclusion:
        """达最大轮数仍未提交结论时的兜底合成（需求 4.8、4.10）。

        基于已获得的观察结果合成一个三维度各至少 1 条结论的意见，
        保证流水线不悬挂且下游可消费。
        """

        # 判断是否读取到 README 与元数据，作为兜底结论的依据。
        saw_readme = any(
            obs.success and (obs.tool == "read_readme" or "README" in str(obs.args))
            for obs in observations
        )
        saw_metadata = any(
            obs.success and obs.tool == "read_metadata" for obs in observations
        )

        readme_note = (
            "已读取到 README，但在最大分析轮数内未完成清晰度综合评估，"
            "建议关注其结构完整性与使用示例。"
            if saw_readme
            else "未能充分考察 README，建议补充简介、安装与使用说明以提升清晰度。"
        )
        activeness_note = (
            "已获取仓库元数据，建议结合 Star/Fork 与最近提交时间综合判断活跃度。"
            if saw_metadata
            else "未能充分考察活跃度指标，建议参考 Star/Fork 与提交新近程度。"
        )

        return AgentConclusion(
            role=self.role,
            data={
                "readme_clarity": [readme_note],
                "practical_value": [
                    "在最大分析轮数内未形成完整的实用价值判断，"
                    "建议结合项目定位与适用场景进一步评估。"
                ],
                "activeness": [activeness_note],
                "summary": (
                    "因达到最大分析轮数，基于已有观察合成的兜底产品价值意见；"
                    "三个维度均至少覆盖 1 条结论。"
                ),
            },
            raw_text="",
        )
