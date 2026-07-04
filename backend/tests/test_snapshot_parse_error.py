"""Repository_Snapshot 解析错误单元测试（任务 2.3）。

覆盖需求 3.3（语法非法文本）与需求 3.4（缺必需字段、字段类型不匹配）：
断言 ``parse_snapshot`` 抛出描述性的 ``SnapshotParseError`` 且不返回对象。
"""

import json

import pytest

from app.models.snapshot import (
    RepositorySnapshot,
    SnapshotParseError,
    parse_snapshot,
    serialize_snapshot,
)


def _valid_snapshot_dict() -> dict:
    """构造一段合法的 Repository_Snapshot 数据（字典形式）。"""
    return {
        "metadata": {
            "owner": "octocat",
            "repo": "hello-world",
            "stars": 10,
            "forks": 2,
            "open_issues": 1,
            "languages": {"Python": 1234},
            "last_commit_at": "2024-01-01T00:00:00Z",
            "default_branch": "main",
        },
        "readme": "# Hello",
        "tree": [{"path": "README.md", "type": "file", "depth": 0}],
        "tree_truncated": False,
        "representative_files": {"README.md": "# Hello"},
        "fetched_at": "2024-01-02T00:00:00Z",
    }


def test_valid_json_parses_successfully():
    """合法 JSON 文本应成功解析为 RepositorySnapshot（作为对照基线）。"""
    text = json.dumps(_valid_snapshot_dict())
    snapshot = parse_snapshot(text)
    assert isinstance(snapshot, RepositorySnapshot)
    assert snapshot.metadata.owner == "octocat"


# ---------------------------------------------------------------------------
# 需求 3.3：非 JSON 语法文本
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_text",
    [
        "",  # 空字符串
        "not json at all",  # 纯文本
        "{unclosed: ",  # 未闭合的括号
        '{"metadata": }',  # 缺少值
        "{'single': 'quotes'}",  # 非法单引号
        "[1, 2, 3",  # 未闭合数组
    ],
)
def test_parse_snapshot_rejects_non_json_text(invalid_text):
    """非 JSON 语法文本应抛出描述性 SnapshotParseError，且不返回对象。"""
    with pytest.raises(SnapshotParseError) as exc_info:
        parse_snapshot(invalid_text)
    # 错误信息应指明语法非法原因（描述性）。
    assert "语法非法" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 需求 3.4：语法合法但缺失必需字段
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_field",
    ["metadata", "readme", "tree", "representative_files", "fetched_at"],
)
def test_parse_snapshot_rejects_missing_required_field(missing_field):
    """缺失顶层必需字段应抛出指明该字段的 SnapshotParseError。"""
    data = _valid_snapshot_dict()
    del data[missing_field]
    text = json.dumps(data)

    with pytest.raises(SnapshotParseError) as exc_info:
        parse_snapshot(text)
    message = str(exc_info.value)
    assert "结构非法" in message
    # 描述性错误应包含缺失的字段名。
    assert missing_field in message


@pytest.mark.parametrize(
    "missing_field",
    ["owner", "repo", "stars", "forks", "open_issues", "languages", "last_commit_at", "default_branch"],
)
def test_parse_snapshot_rejects_missing_nested_metadata_field(missing_field):
    """缺失 metadata 内嵌必需字段应抛出指明该字段的 SnapshotParseError。"""
    data = _valid_snapshot_dict()
    del data["metadata"][missing_field]
    text = json.dumps(data)

    with pytest.raises(SnapshotParseError) as exc_info:
        parse_snapshot(text)
    message = str(exc_info.value)
    assert "结构非法" in message
    assert missing_field in message


# ---------------------------------------------------------------------------
# 需求 3.4：语法合法但字段类型不匹配
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("readme", 123),  # 应为 str
        ("tree", "not-a-list"),  # 应为 list
        ("tree_truncated", [1, 2]),  # 应为 bool（列表无法强制转换）
        ("representative_files", ["not", "a", "dict"]),  # 应为 dict
        ("fetched_at", 456),  # 应为 str
    ],
)
def test_parse_snapshot_rejects_type_mismatch_top_level(field, bad_value):
    """顶层字段类型不匹配应抛出指明该字段的 SnapshotParseError。"""
    data = _valid_snapshot_dict()
    data[field] = bad_value
    text = json.dumps(data)

    with pytest.raises(SnapshotParseError) as exc_info:
        parse_snapshot(text)
    message = str(exc_info.value)
    assert "结构非法" in message
    assert field in message


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("stars", "ten"),  # 应为 int
        ("forks", "two"),  # 应为 int
        ("open_issues", [1]),  # 应为 int
        ("languages", "Python"),  # 应为 dict[str, int]
        ("owner", 100),  # 应为 str
    ],
)
def test_parse_snapshot_rejects_type_mismatch_nested_metadata(field, bad_value):
    """metadata 内嵌字段类型不匹配应抛出指明该字段的 SnapshotParseError。"""
    data = _valid_snapshot_dict()
    data["metadata"][field] = bad_value
    text = json.dumps(data)

    with pytest.raises(SnapshotParseError) as exc_info:
        parse_snapshot(text)
    message = str(exc_info.value)
    assert "结构非法" in message
    assert field in message


def test_parse_snapshot_rejects_invalid_tree_entry_type_literal():
    """tree 条目 type 字段取值不在 Literal 范围内应抛出 SnapshotParseError。"""
    data = _valid_snapshot_dict()
    data["tree"] = [{"path": "x", "type": "symlink", "depth": 0}]
    text = json.dumps(data)

    with pytest.raises(SnapshotParseError) as exc_info:
        parse_snapshot(text)
    assert "结构非法" in str(exc_info.value)


def test_parse_error_does_not_return_object():
    """解析失败路径不返回任何对象——异常应在返回前抛出。"""
    data = _valid_snapshot_dict()
    del data["readme"]
    text = json.dumps(data)

    result = None
    with pytest.raises(SnapshotParseError):
        result = parse_snapshot(text)
    assert result is None


def test_roundtrip_valid_snapshot_still_parses():
    """序列化后的合法 Snapshot 仍可解析——确保错误测试未误伤正常路径。"""
    snapshot = RepositorySnapshot(**_valid_snapshot_dict())
    text = serialize_snapshot(snapshot)
    assert parse_snapshot(text) == snapshot
