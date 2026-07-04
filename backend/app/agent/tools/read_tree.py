"""read_tree 工具：返回目录结构（需求 4.4）。

对内存中的 Repository_Snapshot 只读操作，可选按路径前缀过滤。
"""

from __future__ import annotations

from ...models.snapshot import RepositorySnapshot
from .base import Tool, ToolResult


class ReadTreeTool(Tool):
    """返回 Repository_Snapshot 的目录结构。

    - 无参数时返回全部目录条目。
    - 提供 ``prefix`` 时仅返回路径以该前缀开头的条目。
    """

    name = "read_tree"
    description = "返回仓库目录结构；可选 prefix 参数按路径前缀过滤"

    def run(
        self,
        snapshot: RepositorySnapshot,
        prefix: str | None = None,
    ) -> ToolResult:
        entries = snapshot.tree
        if prefix:
            entries = [entry for entry in entries if entry.path.startswith(prefix)]

        lines = [f"{entry.type}\t{entry.path}" for entry in entries]
        output = "\n".join(lines)
        if snapshot.tree_truncated:
            output = f"{output}\n[目录树已截断：超过深度或条目上限]" if output else "[目录树已截断：超过深度或条目上限]"

        return ToolResult(success=True, output=output)
