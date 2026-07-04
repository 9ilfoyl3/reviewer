"""Agent_Tool 工具集单元测试（任务 6.1）。

覆盖需求：
- 4.4：提供 read_tree / read_file / read_readme / read_metadata 四个工具。
- 4.5：read_file 命中返回文本，单次上限 100000 字符。
- 4.6：read_file 目标文件不存在时返回 "文件不存在" 结果且循环继续。
- 4.15：read_file 内容超过 100000 字符时截断并标记 truncated=True。
- 4.16：工具名不在注册表或参数非法时返回带错误原因的 ToolResult(success=False)。
"""

import json

import pytest

from app.agent.tools import (
    MAX_FILE_CHARS,
    ReadFileTool,
    ReadMetadataTool,
    ReadReadmeTool,
    ReadTreeTool,
    ToolRegistry,
    ToolResult,
    build_default_registry,
)
from app.models.snapshot import RepositorySnapshot


def _make_snapshot(**overrides) -> RepositorySnapshot:
    """构造用于测试的 Repository_Snapshot。"""
    data = {
        "metadata": {
            "owner": "octocat",
            "repo": "hello-world",
            "stars": 42,
            "forks": 7,
            "open_issues": 3,
            "languages": {"Python": 1000, "TypeScript": 500},
            "last_commit_at": "2024-01-01T00:00:00Z",
            "default_branch": "main",
        },
        "readme": "# Hello World\n\n这是一个示例仓库。",
        "tree": [
            {"path": "README.md", "type": "file", "depth": 0},
            {"path": "src", "type": "dir", "depth": 0},
            {"path": "src/main.py", "type": "file", "depth": 1},
        ],
        "tree_truncated": False,
        "representative_files": {
            "README.md": "# Hello World",
            "src/main.py": "print('hello')",
        },
        "fetched_at": "2024-01-02T00:00:00Z",
    }
    data.update(overrides)
    return RepositorySnapshot(**data)


# ---------------------------------------------------------------------------
# 需求 4.4：默认注册表提供四个工具
# ---------------------------------------------------------------------------


def test_default_registry_provides_four_tools():
    registry = build_default_registry(_make_snapshot())
    assert set(registry.names()) == {
        "read_tree",
        "read_file",
        "read_readme",
        "read_metadata",
    }


# ---------------------------------------------------------------------------
# read_tree
# ---------------------------------------------------------------------------


def test_read_tree_returns_all_entries():
    snapshot = _make_snapshot()
    result = ReadTreeTool().run(snapshot)
    assert result.success is True
    assert "README.md" in result.output
    assert "src/main.py" in result.output


def test_read_tree_filters_by_prefix():
    snapshot = _make_snapshot()
    result = ReadTreeTool().run(snapshot, prefix="src/")
    assert result.success is True
    assert "src/main.py" in result.output
    assert "README.md" not in result.output


def test_read_tree_marks_truncation():
    snapshot = _make_snapshot(tree_truncated=True)
    result = ReadTreeTool().run(snapshot)
    assert "截断" in result.output


# ---------------------------------------------------------------------------
# 需求 4.5：read_file 命中返回文本
# ---------------------------------------------------------------------------


def test_read_file_returns_content_when_present():
    snapshot = _make_snapshot()
    result = ReadFileTool().run(snapshot, path="src/main.py")
    assert result.success is True
    assert result.output == "print('hello')"
    assert result.truncated is False


# ---------------------------------------------------------------------------
# 需求 4.6：read_file 文件不存在返回 "文件不存在" 结果，循环继续
# ---------------------------------------------------------------------------


def test_read_file_missing_returns_not_found_without_error():
    snapshot = _make_snapshot()
    result = ReadFileTool().run(snapshot, path="does/not/exist.py")
    # 不中断循环：success 仍为 True，输出为 "文件不存在" 描述。
    assert result.success is True
    assert "文件不存在" in result.output
    assert "does/not/exist.py" in result.output


# ---------------------------------------------------------------------------
# 需求 4.15：read_file 超过上限时截断并标记 truncated
# ---------------------------------------------------------------------------


def test_read_file_truncates_over_limit():
    big = "a" * (MAX_FILE_CHARS + 500)
    snapshot = _make_snapshot(representative_files={"big.txt": big})
    result = ReadFileTool().run(snapshot, path="big.txt")
    assert result.success is True
    assert result.truncated is True
    assert len(result.output) == MAX_FILE_CHARS


def test_read_file_exactly_at_limit_not_truncated():
    exact = "b" * MAX_FILE_CHARS
    snapshot = _make_snapshot(representative_files={"exact.txt": exact})
    result = ReadFileTool().run(snapshot, path="exact.txt")
    assert result.success is True
    assert result.truncated is False
    assert len(result.output) == MAX_FILE_CHARS


# ---------------------------------------------------------------------------
# read_readme
# ---------------------------------------------------------------------------


def test_read_readme_returns_text():
    snapshot = _make_snapshot()
    result = ReadReadmeTool().run(snapshot)
    assert result.success is True
    assert "Hello World" in result.output


def test_read_readme_handles_empty():
    snapshot = _make_snapshot(readme="")
    result = ReadReadmeTool().run(snapshot)
    assert result.success is True
    assert "无 README" in result.output


# ---------------------------------------------------------------------------
# read_metadata
# ---------------------------------------------------------------------------


def test_read_metadata_returns_serialized_metadata():
    snapshot = _make_snapshot()
    result = ReadMetadataTool().run(snapshot)
    assert result.success is True
    parsed = json.loads(result.output)
    assert parsed["stars"] == 42
    assert parsed["forks"] == 7
    assert parsed["languages"] == {"Python": 1000, "TypeScript": 500}


# ---------------------------------------------------------------------------
# 需求 4.16：工具名不存在或参数非法返回错误 ToolResult，循环继续
# ---------------------------------------------------------------------------


def test_registry_unknown_tool_returns_error_result():
    registry = build_default_registry(_make_snapshot())
    result = registry.execute("nonexistent_tool")
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert result.error is not None
    assert "nonexistent_tool" in result.error


def test_registry_invalid_args_returns_error_result():
    registry = build_default_registry(_make_snapshot())
    # read_file 需要 path 参数，此处传入未知参数触发 TypeError。
    result = registry.execute("read_file", {"wrong_arg": "value"})
    assert result.success is False
    assert result.error is not None
    assert "参数非法" in result.error


def test_registry_missing_required_arg_returns_error_result():
    registry = build_default_registry(_make_snapshot())
    # read_file 缺少必需的 path 参数。
    result = registry.execute("read_file", {})
    assert result.success is False
    assert result.error is not None


def test_registry_execute_dispatches_to_correct_tool():
    registry = build_default_registry(_make_snapshot())
    result = registry.execute("read_file", {"path": "README.md"})
    assert result.success is True
    assert result.output == "# Hello World"


def test_registry_tool_exception_is_caught():
    """工具执行抛异常时应被捕获为错误 ToolResult，保证循环继续（需求 4.16）。"""

    class BrokenTool(ReadTreeTool):
        name = "broken"

        def run(self, snapshot, **kwargs):  # noqa: ARG002
            raise RuntimeError("boom")

    registry = ToolRegistry(_make_snapshot())
    registry.register(BrokenTool())
    result = registry.execute("broken")
    assert result.success is False
    assert "boom" in result.error
