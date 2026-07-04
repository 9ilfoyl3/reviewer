# Feature: reviewer, Property 6: read_file 内容长度上限
"""read_file 长度上限基于属性的测试（任务 6.2）。

**Property 6: read_file 内容长度上限**
**Validates: Requirements 4.5, 4.15**

对任意存在于 Repository_Snapshot 的文件与任意长度的文件内容，read_file 工具返回
内容的字符数恒不超过 100000；当原内容不超过上限时返回内容与原内容全等，超过上限
时返回被截断至 100000 字符的内容并标记 ``truncated=True``。

生成器覆盖以下边界：
- 任意长度文件内容（含空内容、远超上限的超长内容）
- Unicode / 特殊字符（含 emoji、CJK、控制字符、引号、反斜杠等）
- 恰好等于上限、上限 ±1 附近的临界长度
"""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.agent.tools.base import MAX_FILE_CHARS
from app.agent.tools.read_file import ReadFileTool
from app.models.snapshot import RepositoryMetadata, RepositorySnapshot

# 覆盖 Unicode / 特殊字符：普通文本、CJK、emoji、控制字符、引号、反斜杠、换行等。
content_alphabet = st.characters(blacklist_categories=("Cs",))


def _make_snapshot(path: str, content: str) -> RepositorySnapshot:
    """构造仅含单个代表性文件的 Repository_Snapshot。"""
    return RepositorySnapshot(
        metadata=RepositoryMetadata(
            owner="octocat",
            repo="hello-world",
            stars=0,
            forks=0,
            open_issues=0,
            languages={},
            last_commit_at="2024-01-01T00:00:00Z",
            default_branch="main",
        ),
        readme="",
        tree=[],
        tree_truncated=False,
        representative_files={path: content},
        fetched_at="2024-01-02T00:00:00Z",
    )


# Hypothesis 无法直接生成接近 100000 长度的文本（受内部缓冲上限限制），
# 因此改为「生成小段种子文本 + 目标长度」，再由种子循环拼接构造出指定长度的
# 内容。这样既覆盖任意长度（含空内容、恰好上限、超限），又能覆盖 Unicode /
# 特殊字符边界。
@st.composite
def content_strategy(draw) -> str:
    """构造任意长度、含 Unicode / 特殊字符的文件内容。"""
    # 目标长度：覆盖 0 到远超上限，并在上限附近（±5）加密采样临界值。
    target_len = draw(
        st.one_of(
            st.integers(min_value=0, max_value=MAX_FILE_CHARS + 1000),
            st.integers(min_value=MAX_FILE_CHARS - 5, max_value=MAX_FILE_CHARS + 5),
        )
    )
    if target_len == 0:
        return ""

    # 非空种子文本，覆盖 Unicode / 特殊字符；循环拼接到目标长度后精确截取。
    seed = draw(st.text(alphabet=content_alphabet, min_size=1, max_size=64))
    repeats = target_len // len(seed) + 1
    return (seed * repeats)[:target_len]


@settings(
    max_examples=150,  # ≥100 样例
    deadline=None,  # 超长内容生成 / 处理耗时不定，关闭单例超时
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(content=content_strategy())
def test_read_file_length_limit(content: str):
    """Property 6: read_file 返回字符数 ≤100000；未超上限时全等，超上限时截断并标记。"""
    path = "sample.txt"
    snapshot = _make_snapshot(path, content)

    result = ReadFileTool().run(snapshot, path=path)

    # 恒成立：返回内容字符数不超过上限。
    assert result.success is True
    assert len(result.output) <= MAX_FILE_CHARS

    if len(content) <= MAX_FILE_CHARS:
        # 未超上限：返回内容与原内容全等，且不标记截断。
        assert result.output == content
        assert result.truncated is False
    else:
        # 超上限：截断至恰好 MAX_FILE_CHARS 字符，且标记 truncated=True。
        assert len(result.output) == MAX_FILE_CHARS
        assert result.output == content[:MAX_FILE_CHARS]
        assert result.truncated is True
