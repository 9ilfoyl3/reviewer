"""Health_Report 数据模型与序列化/解析器。

对应设计文档 Data Models 小节的 Health_Report，使用 Pydantic v2 建模，
天然支持 JSON 序列化/解析，并在缺字段/类型不匹配时抛出结构化校验错误
（需求 6.1、6.2、6.7、6.8）。
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError


class ReportParseError(ValueError):
    """解析 Health_Report 失败时抛出的描述性错误（需求 6.8）。"""


class LanguagePercent(BaseModel):
    """单一语言的占比条目，占比之和应为 100%。"""

    name: str
    percent: float


class MetadataSummary(BaseModel):
    """报告部分 1：仓库元数据摘要。"""

    stars: int
    forks: int
    language_distribution: list[LanguagePercent]  # [{name, percent}], 占比之和=100%


class CodeAuditorOpinion(BaseModel):
    """报告部分 2：Code_Auditor 技术意见。"""

    strengths: list[str]        # ≥1（需求 4.9）
    improvements: list[str]     # ≥1（需求 4.9）
    summary: str


class ProductValueOpinion(BaseModel):
    """报告部分 3：Product_Value_Agent 产品价值意见。"""

    readme_clarity: list[str]   # ≥1（需求 4.10）
    practical_value: list[str]  # ≥1
    activeness: list[str]       # ≥1
    summary: str


class HealthReport(BaseModel):
    """结构化健康体检报告（五部分，需求 6.1）。"""

    metadata_summary: MetadataSummary        # 部分 1
    code_auditor: CodeAuditorOpinion         # 部分 2
    product_value: ProductValueOpinion       # 部分 3
    recommendations: list[str]               # 部分 4：3–10 条综合建议（需求 4.12）
    score: int = Field(ge=0, le=100)         # 部分 5：0–100 整数总分（需求 6.2）


def serialize_report(report: HealthReport) -> str:
    """将 Health_Report 序列化为包含全部字段的 UTF-8 JSON 文本（需求 6.7）。"""

    return report.model_dump_json()


def parse_report(text: str) -> HealthReport:
    """解析 Health_Report JSON 文本（需求 6.7、6.8）。

    先探测 JSON 语法，语法非法抛带原因的 ``ReportParseError``；
    再做结构校验，缺字段/类型不匹配抛带字段名与原因的 ``ReportParseError``。
    """

    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportParseError(f"Health_Report JSON 语法非法: {exc}") from exc

    try:
        return HealthReport.model_validate_json(text)
    except ValidationError as exc:
        raise ReportParseError(
            f"Health_Report 结构不符合要求: {exc}"
        ) from exc
