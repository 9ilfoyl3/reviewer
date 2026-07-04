"""Repository_Snapshot 数据模型与序列化 / 解析器。

对应需求 3：仓库数据序列化与解析（往返一致）。

- 使用 Pydantic v2 建模，天然支持 ``model_dump_json()`` 序列化与
  ``model_validate_json()`` 解析，并在缺字段 / 类型不匹配时抛出结构化的
  ``ValidationError``。
- ``serialize_snapshot`` 产出包含全部字段的 UTF-8 JSON 文本（需求 3.1）。
- ``parse_snapshot`` 先探测 JSON 语法（语法非法抛 ``SnapshotParseError``，
  需求 3.3），再做结构校验（缺字段 / 类型不匹配抛带字段名与原因的
  ``SnapshotParseError``，需求 3.4）。
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ValidationError


class SnapshotParseError(ValueError):
    """Repository_Snapshot 解析失败时抛出的描述性错误。

    覆盖两类失败（需求 3.3 / 3.4）：

    - 语法非法：无法解析为 JSON 的文本。
    - 结构非法：语法合法但缺失必需字段或字段类型不匹配。
    """


class RepositoryMetadata(BaseModel):
    """仓库元数据（需求 2.1）。"""

    owner: str
    repo: str
    stars: int  # Star 数（整数）
    forks: int  # Fork 数（整数）
    open_issues: int  # Open Issue 数
    languages: dict[str, int]  # 语言 -> 所占字节数
    last_commit_at: str  # ISO 8601 UTC 时间戳
    default_branch: str


class TreeEntry(BaseModel):
    """目录树中的单个条目。"""

    path: str  # 文件 / 目录路径
    type: Literal["file", "dir"]
    depth: int  # 目录层级


class RepositorySnapshot(BaseModel):
    """由 GitHub_Client 抓取并归一化后的仓库数据集合。"""

    metadata: RepositoryMetadata
    readme: str  # README 文本；无 README 时为 ""（需求 2.9）
    tree: list[TreeEntry]  # 目录结构（深度≤10, 条目≤10000）
    tree_truncated: bool = False  # 超上限时标记已截断（需求 2.3）
    representative_files: dict[str, str]  # 路径 -> 代表性文件内容
    fetched_at: str  # 抓取时间 ISO 8601 UTC


def serialize_snapshot(snapshot: RepositorySnapshot) -> str:
    """将 Repository_Snapshot 序列化为 UTF-8 JSON 文本（需求 3.1）。

    ``model_dump_json`` 默认包含模型的全部字段，产出的字符串为 UTF-8 可编码文本。
    """

    return snapshot.model_dump_json()


def parse_snapshot(text: str) -> RepositorySnapshot:
    """将 JSON 文本解析为 Repository_Snapshot（需求 3.2–3.4）。

    步骤：

    1. 先用 ``json.loads`` 探测语法；语法非法抛 ``SnapshotParseError`` 带原因
       （需求 3.3）。
    2. 再用 ``model_validate_json`` 做结构校验；缺字段 / 类型不匹配抛
       ``SnapshotParseError`` 带字段名与原因（需求 3.4）。
    """

    # 步骤 1：语法探测。
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise SnapshotParseError(f"JSON 语法非法：{exc}") from exc

    # 步骤 2：结构校验。
    try:
        return RepositorySnapshot.model_validate_json(text)
    except ValidationError as exc:
        details = "; ".join(
            f"字段 '{'.'.join(str(loc) for loc in err['loc'])}' {err['msg']}"
            for err in exc.errors()
        )
        raise SnapshotParseError(f"Repository_Snapshot 结构非法：{details}") from exc
