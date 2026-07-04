"""Health_Report 序列化往返基于属性的测试（任务 2.5）。

# Feature: reviewer, Property 2: Health_Report 序列化往返一致
# Validates: Requirements 6.7, 6.9

使用 hypothesis 生成五部分齐全、总分 0–100 的合法 Health_Report，
运行 ≥100 样例，验证 ``parse_report(serialize_report(x)) == x`` 的往返属性。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.report import (
    CodeAuditorOpinion,
    HealthReport,
    LanguagePercent,
    MetadataSummary,
    ProductValueOpinion,
    parse_report,
    serialize_report,
)

# 覆盖 Unicode/特殊字符的文本策略，验证 UTF-8 序列化不失真。
_text = st.text(max_size=200)
_nonempty_list = lambda: st.lists(_text, min_size=1, max_size=5)


_language_percent = st.builds(
    LanguagePercent,
    name=_text,
    percent=st.floats(
        min_value=0.0,
        max_value=100.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)

_metadata_summary = st.builds(
    MetadataSummary,
    stars=st.integers(min_value=0, max_value=10_000_000),
    forks=st.integers(min_value=0, max_value=10_000_000),
    language_distribution=st.lists(_language_percent, max_size=6),
)

_code_auditor = st.builds(
    CodeAuditorOpinion,
    strengths=_nonempty_list(),       # ≥1（需求 4.9）
    improvements=_nonempty_list(),    # ≥1（需求 4.9）
    summary=_text,
)

_product_value = st.builds(
    ProductValueOpinion,
    readme_clarity=_nonempty_list(),   # ≥1（需求 4.10）
    practical_value=_nonempty_list(),  # ≥1
    activeness=_nonempty_list(),       # ≥1
    summary=_text,
)

_health_report = st.builds(
    HealthReport,
    metadata_summary=_metadata_summary,
    code_auditor=_code_auditor,
    product_value=_product_value,
    recommendations=st.lists(_text, min_size=3, max_size=10),  # 3–10 条（需求 4.12）
    score=st.integers(min_value=0, max_value=100),             # 0–100 整数（需求 6.2）
)


@settings(max_examples=200)
@given(report=_health_report)
def test_health_report_roundtrip(report: HealthReport):
    """先序列化再解析所得对象与原对象字段值相等（需求 6.7、6.9）。"""
    restored = parse_report(serialize_report(report))
    assert restored == report
