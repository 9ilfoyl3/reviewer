# Feature: reviewer, Property 7: 目录树截断上限
"""目录树截断基于属性的测试（任务 3.3）。

**Property 7: 目录树截断上限**
**Validates: Requirements 2.3**

对任意规模的仓库目录树，``GitHubClient`` 归一化后的 Repository_Snapshot 中：

- ``tree`` 条目数恒不超过 10000（``MAX_TREE_ENTRIES``）；
- 每个条目的深度恒不超过 10（``MAX_TREE_DEPTH``）；
- 当原始树超过任一上限（深度超限、条目超限或 GitHub 自身标记 truncated）时，
  ``tree_truncated`` 恒为 True。

实现说明：截断逻辑位于 :meth:`GitHubClient._get_tree`。测试通过 ``httpx.MockTransport``
注入生成的目录树 JSON，避免真实网络访问；直接调用 ``_get_tree`` 断言归一化结果。
"""

import asyncio

import httpx
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.config import Settings
from app.github.client import (
    MAX_TREE_DEPTH,
    MAX_TREE_ENTRIES,
    GitHubClient,
    _tree_depth,
)

# ---------------------------------------------------------------------------
# 生成器：构造任意规模的原始 GitHub 目录树
# ---------------------------------------------------------------------------

# 单个路径段：非空、且不含斜杠（斜杠仅用于表达层级）。
_segment = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="/"),
    min_size=1,
    max_size=6,
)


@st.composite
def _raw_tree(draw):
    """生成 ``(raw_entries, gh_truncated)``：模拟 GitHub trees 接口返回。

    为覆盖「深度超限」「条目超限」两类截断，同时兼顾性能：

    - ``sample_entries``：少量随机路径，深度 0–15（含超过 10 层的超限项），
      用于覆盖深度截断与类型（blob/tree）归一化。
    - ``bulk_count``：程序化生成的深度 1 浅层条目数（0–12000），
      用于低成本地触达 / 超过 10000 条目上限。
    """
    # 深度 0（空路径）到 15（远超 10 层上限）的随机路径。
    sample_entries = draw(
        st.lists(
            st.builds(
                lambda segs, is_dir: {
                    "path": "/".join(segs),
                    "type": "tree" if is_dir else "blob",
                },
                st.lists(_segment, min_size=0, max_size=15),
                st.booleans(),
            ),
            max_size=60,
        )
    )
    # 程序化批量浅层条目（深度 1），低成本逼近 / 超过条目上限。
    bulk_count = draw(st.integers(min_value=0, max_value=12000))
    gh_truncated = draw(st.booleans())

    raw_entries = list(sample_entries) + [
        {"path": f"bulk_{i}.txt", "type": "blob"} for i in range(bulk_count)
    ]
    return raw_entries, gh_truncated


def _run_get_tree(raw_entries, gh_truncated):
    """用 MockTransport 注入原始树，调用 GitHubClient._get_tree 并返回结果。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"tree": raw_entries, "truncated": gh_truncated}
        )

    async def _call():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://api.github.com"
        ) as ac:
            client = GitHubClient(settings=Settings(), client=ac)
            return await client._get_tree("owner", "repo", "main")

    return asyncio.run(_call())


@settings(
    max_examples=150,  # ≥100 样例（需求 2.3）
    deadline=None,  # 大规模目录树归一化耗时不定，关闭单例超时
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(data=_raw_tree())
def test_tree_truncation_limits_hold(data):
    """Property 7: 归一化后 tree 条目数 ≤10000、深度 ≤10，超限时 tree_truncated=True。"""
    raw_entries, gh_truncated = data
    entries, truncated = _run_get_tree(raw_entries, gh_truncated)

    # 1) 条目数上限：恒不超过 10000。
    assert len(entries) <= MAX_TREE_ENTRIES

    # 2) 深度上限：每个保留条目的深度恒不超过 10。
    assert all(entry.depth <= MAX_TREE_DEPTH for entry in entries)

    # 3) 超限时必须标记 tree_truncated：
    #    - 存在深度超过上限的原始条目 → 会被丢弃 → 截断；
    #    - 深度合法的条目数超过 10000 → 会被截断收集 → 截断；
    #    - GitHub 自身返回 truncated=True。
    depth_ok_count = sum(
        1 for item in raw_entries if _tree_depth(str(item.get("path", ""))) <= MAX_TREE_DEPTH
    )
    any_over_depth = any(
        _tree_depth(str(item.get("path", ""))) > MAX_TREE_DEPTH for item in raw_entries
    )
    exceeded = any_over_depth or depth_ok_count > MAX_TREE_ENTRIES or gh_truncated
    if exceeded:
        assert truncated is True
