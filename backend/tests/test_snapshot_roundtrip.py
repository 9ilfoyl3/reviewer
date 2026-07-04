# Feature: reviewer, Property 1: Repository_Snapshot 序列化往返一致
"""Repository_Snapshot 往返基于属性的测试（任务 2.2）。

**Property 1: Repository_Snapshot 序列化往返一致**
**Validates: Requirements 3.1, 3.2, 3.5**

对任意合法的 Repository_Snapshot 对象，先序列化为 JSON 文本再解析，所得对象在
字段存在性、字段类型与字段取值上均与原对象相等，即
``parse_snapshot(serialize_snapshot(x)) == x``。

生成器覆盖以下边界：
- Unicode / 特殊字符（含 emoji、控制字符、CJK、引号、反斜杠等）
- 空 README（``readme == ""``）
- 大规模目录树（数千条目、深度 0–10）
"""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.models.snapshot import (
    RepositoryMetadata,
    RepositorySnapshot,
    TreeEntry,
    parse_snapshot,
    serialize_snapshot,
)

# 覆盖 Unicode / 特殊字符：普通文本、CJK、emoji、控制字符、引号、反斜杠、换行等。
text_strategy = st.text(
    alphabet=st.characters(
        # 允许除代理项外的全部可编码码位，确保覆盖 Unicode 各平面与控制字符。
        blacklist_categories=("Cs",),
    ),
    max_size=200,
)

# owner / repo 采用较短文本即可，同样覆盖特殊字符。
short_text_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=40,
)


@st.composite
def metadata_strategy(draw) -> RepositoryMetadata:
    """生成合法的 Repository_Metadata。"""
    languages = draw(
        st.dictionaries(
            keys=short_text_strategy,
            values=st.integers(min_value=0, max_value=10_000_000),
            max_size=15,
        )
    )
    return RepositoryMetadata(
        owner=draw(short_text_strategy),
        repo=draw(short_text_strategy),
        stars=draw(st.integers(min_value=0, max_value=10_000_000)),
        forks=draw(st.integers(min_value=0, max_value=10_000_000)),
        open_issues=draw(st.integers(min_value=0, max_value=10_000_000)),
        languages=languages,
        last_commit_at=draw(text_strategy),
        default_branch=draw(short_text_strategy),
    )


tree_entry_strategy = st.builds(
    TreeEntry,
    path=text_strategy,
    type=st.sampled_from(["file", "dir"]),
    depth=st.integers(min_value=0, max_value=10),
)


@st.composite
def snapshot_strategy(draw) -> RepositorySnapshot:
    """生成合法的 Repository_Snapshot，覆盖空 README 与大规模目录树。"""
    # README：一半概率为空字符串（覆盖需求 2.9 的空 README 边界）。
    readme = draw(st.one_of(st.just(""), text_strategy))

    # 目录树：从空树到大规模树（最多 3000 条目），覆盖大规模场景。
    tree = draw(st.lists(tree_entry_strategy, max_size=3000))

    representative_files = draw(
        st.dictionaries(
            keys=text_strategy,
            values=text_strategy,
            max_size=20,
        )
    )

    return RepositorySnapshot(
        metadata=draw(metadata_strategy()),
        readme=readme,
        tree=tree,
        tree_truncated=draw(st.booleans()),
        representative_files=representative_files,
        fetched_at=draw(text_strategy),
    )


@settings(
    max_examples=150,  # ≥100 样例（需求 10.2）
    deadline=None,  # 大规模目录树序列化耗时不定，关闭单例超时
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(snapshot=snapshot_strategy())
def test_snapshot_serialize_parse_roundtrip(snapshot: RepositorySnapshot):
    """Property 1: parse_snapshot(serialize_snapshot(x)) == x。"""
    text = serialize_snapshot(snapshot)
    assert isinstance(text, str)

    restored = parse_snapshot(text)

    # Pydantic 模型 __eq__ 按字段值比较，满足字段存在性 / 类型 / 取值均相等。
    assert restored == snapshot
    # 再次序列化应产出稳定文本，进一步佐证往返稳定。
    assert serialize_snapshot(restored) == text
