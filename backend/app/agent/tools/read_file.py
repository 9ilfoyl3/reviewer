"""read_file 工具：返回指定文件内容（需求 4.5、4.6、4.15）。

从内存中 Repository_Snapshot 的 ``representative_files`` 读取：

- 命中：返回文件文本内容，单次上限 ``MAX_FILE_CHARS`` 字符，超出截断并标记
  ``truncated=True``（需求 4.5、4.15）。
- 未命中：返回 "文件不存在" 结果且不中断 ReAct 循环（需求 4.6）。
"""

from __future__ import annotations

from ...models.snapshot import RepositorySnapshot
from .base import MAX_FILE_CHARS, Tool, ToolResult


class ReadFileTool(Tool):
    """读取 Repository_Snapshot 中指定文件的内容。"""

    name = "read_file"
    description = (
        "读取指定文件的文本内容；参数 path 为文件路径。"
        f"单次返回上限 {MAX_FILE_CHARS} 字符，超出将被截断。"
    )

    def run(self, snapshot: RepositorySnapshot, path: str) -> ToolResult:
        # 文件不存在：返回 "文件不存在" 结果，循环继续（需求 4.6）。
        if path not in snapshot.representative_files:
            return ToolResult(
                success=True,
                output=f"文件不存在：{path}",
            )

        content = snapshot.representative_files[path]

        # 超过上限：截断至 MAX_FILE_CHARS 字符并标记 truncated（需求 4.5、4.15）。
        if len(content) > MAX_FILE_CHARS:
            return ToolResult(
                success=True,
                output=content[:MAX_FILE_CHARS],
                truncated=True,
            )

        return ToolResult(success=True, output=content)
