"""read_metadata 工具：返回 Repository_Metadata（需求 4.4）。

从内存中 Repository_Snapshot 读取元数据并序列化为 JSON 文本供 Agent 观察。
"""

from __future__ import annotations

from ...models.snapshot import RepositorySnapshot
from .base import Tool, ToolResult


class ReadMetadataTool(Tool):
    """返回 Repository_Snapshot 的元数据（Star/Fork/语言分布等）。"""

    name = "read_metadata"
    description = "返回仓库元数据：Star 数、Fork 数、语言分布、Open Issue 数、最近提交时间等"

    def run(self, snapshot: RepositorySnapshot) -> ToolResult:
        output = snapshot.metadata.model_dump_json()
        return ToolResult(success=True, output=output)
