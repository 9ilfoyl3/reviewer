"""Agent_Tool 工具集。

对内存中的 Repository_Snapshot 进行只读操作的纯函数式工具：
read_tree、read_file、read_readme、read_metadata。

对应需求 4.4、4.5、4.6、4.15、4.16。
"""

from .base import (
    MAX_FILE_CHARS,
    Tool,
    ToolRegistry,
    ToolResult,
    build_default_registry,
)
from .read_file import ReadFileTool
from .read_metadata import ReadMetadataTool
from .read_readme import ReadReadmeTool
from .read_tree import ReadTreeTool

__all__ = [
    "MAX_FILE_CHARS",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
    "ReadFileTool",
    "ReadMetadataTool",
    "ReadReadmeTool",
    "ReadTreeTool",
]
