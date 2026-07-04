"""Final_Judge 分数钳制边界单元测试（任务 6.7）。

**Validates: Requirements 10.5**

覆盖低于 0 的输入（-1）、边界值 0、区间内输入（50）、边界值 100 与高于
100 的输入（101），断言 ``clamp_score`` 的输出被钳制在 [0, 100]（含两端）区间内。

对应设计「Final_Judge 分数钳制」小节的边界：
``-1 → 0``、``0 → 0``、``50 → 50``、``100 → 100``、``101 → 100``。
"""

from __future__ import annotations

import pytest

from app.agent.final_judge import clamp_score


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (-1, 0),      # 低于下界 → 钳制到 0
        (0, 0),       # 边界值 0 → 0
        (50, 50),     # 区间内 → 原值
        (100, 100),   # 边界值 100 → 100
        (101, 100),   # 高于上界 → 钳制到 100
    ],
)
def test_clamp_score_boundaries(raw: int, expected: int):
    """需求 10.5：-1、0、50、100、101 的钳制结果符合预期，且落在 [0, 100]。"""
    result = clamp_score(raw)

    assert result == expected
    assert 0 <= result <= 100
