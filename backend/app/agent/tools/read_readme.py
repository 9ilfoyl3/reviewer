"""read_readme 工具：返回 README 文本（需求 4.4）。

从内存中 Repository_Snapshot 读取 README；无 README 时其值为空字符串（需求 2.9）。
"""

from __future__ import annotations

from ...models.snapshot import RepositorySnapshot
from .base import Tool, ToolResult


class ReadReadmeTool(Tool):
    """返回 Repository_Snapshot 的 README 文本。"""

    name = "read_readme"
    description = "返回仓库 README 文本；无 README 时返回空内容"

    def run(self, snapshot: RepositorySnapshot) -> ToolResult:
        readme = snapshot.readme
        if not readme:
            return ToolResult(success=True, output="仓库无 README 内容")
        return ToolResult(success=True, output=readme)
