# Feature: reviewer, Property 3: Final_Judge 分数钳制
"""Final_Judge 分数钳制基于属性的测试（任务 6.6）。

**Property 3: Final_Judge 分数钳制**
**Validates: Requirements 4.13, 6.2, 6.3**

对任意整数 / 浮点数 / None 的原始分数输入，clamp_score 的输出恒为落在
[0, 100] 闭区间内的整数：

- ``None``（缺失）→ 修正为 0；
- 非整数 → 四舍五入取整；
- 越界 → 钳制到 [0, 100] 边界值。

生成器覆盖以下边界：
- 任意整数（含负数、远超 100 的大值、区间内值）
- 任意浮点数（含负值、区间内小数、越界值，以及 NaN / 无穷等特殊浮点）
- ``None`` 缺失输入
"""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from app.agent.final_judge import clamp_score

# 覆盖整数 / 浮点数 / None 三类输入。
# 浮点数排除 NaN / 无穷：round(float(nan)) 与 round(float(inf)) 无法转换为 int，
# 会走 clamp_score 中的 ValueError 分支修正为 0（见下方独立用例覆盖）。
score_input_strategy = st.one_of(
    st.none(),
    st.integers(min_value=-1000, max_value=1000),
    st.floats(
        min_value=-1000.0,
        max_value=1000.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)


@settings(max_examples=200)  # ≥100 样例
@given(raw=score_input_strategy)
def test_clamp_score_always_int_in_range(raw):
    """Property 3: 输出恒为 [0, 100] 闭区间内的整数。"""
    result = clamp_score(raw)

    # 恒成立：返回值为 int（bool 是 int 子类，需显式排除）。
    assert type(result) is int
    # 恒成立：落在 [0, 100] 闭区间内。
    assert 0 <= result <= 100


def test_clamp_score_none_maps_to_zero():
    """缺失输入（None）修正为 0。"""
    assert clamp_score(None) == 0


def test_clamp_score_special_floats_map_to_zero():
    """NaN / 无穷等无法取整的特殊浮点修正为 0。"""
    assert clamp_score(math.nan) == 0
    assert clamp_score(math.inf) == 0
    assert clamp_score(-math.inf) == 0
