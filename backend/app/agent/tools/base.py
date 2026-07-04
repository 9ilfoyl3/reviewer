"""Agent_Tool 工具集基础设施。

对应需求 4.4、4.5、4.6、4.15、4.16。

- ``ToolResult``：所有工具统一的返回模型。
- ``Tool``：工具抽象基类，每个工具对内存中的 Repository_Snapshot 做只读操作。
- ``ToolRegistry``：工具注册表，按名分发调用；工具名不存在或参数非法时返回带
  错误原因的 ``ToolResult(success=False)``，使 ReAct 循环得以继续（需求 4.16）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from ...models.snapshot import RepositorySnapshot

# 读取指定文件内容的单次返回字符数上限（需求 4.5、4.15）。
MAX_FILE_CHARS = 100000


class ToolResult(BaseModel):
    """Agent_Tool 的统一返回结果。

    - ``success``：工具是否成功执行。
    - ``output``：工具输出文本（成功时为内容，失败时通常为空字符串）。
    - ``truncated``：内容是否因超过上限被截断（需求 4.15）。
    - ``error``：失败原因描述；成功时为 ``None``。
    """

    success: bool
    output: str
    truncated: bool = False
    error: str | None = None


class Tool(ABC):
    """Agent_Tool 抽象基类。

    每个工具声明名称与描述，并实现 ``run``，对内存中的 Repository_Snapshot 做
    只读操作。``run`` 抛出的异常由 ``ToolRegistry`` 捕获并转为错误 ToolResult。
    """

    name: str
    description: str

    @abstractmethod
    def run(self, snapshot: RepositorySnapshot, **kwargs: object) -> ToolResult:
        """对 Repository_Snapshot 执行工具逻辑并返回 ToolResult。"""


class ToolRegistry:
    """Agent_Tool 注册表，按工具名分发调用。

    绑定单个 Repository_Snapshot，Agent 通过 ``execute(name, args)`` 调用工具。
    工具名不在注册表、参数非法或工具执行抛异常时，返回带错误原因的
    ``ToolResult(success=False)``，ReAct 循环得以继续（需求 4.16）。
    """

    def __init__(self, snapshot: RepositorySnapshot) -> None:
        self._snapshot = snapshot
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具。"""

        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名获取工具，不存在时返回 ``None``。"""

        return self._tools.get(name)

    def names(self) -> list[str]:
        """返回已注册的工具名列表。"""

        return list(self._tools)

    def execute(self, name: str, args: dict[str, object] | None = None) -> ToolResult:
        """按名执行工具，容错处理未知工具名与非法参数（需求 4.16）。"""

        tool = self._tools.get(name)
        if tool is None:
            available = ", ".join(sorted(self._tools)) or "（空）"
            return ToolResult(
                success=False,
                output="",
                error=f"工具 '{name}' 不在可用工具列表中；可用工具：{available}",
            )

        call_args = args or {}
        try:
            return tool.run(self._snapshot, **call_args)
        except TypeError as exc:
            # 参数名 / 数量不匹配等非法参数情况。
            return ToolResult(
                success=False,
                output="",
                error=f"工具 '{name}' 调用参数非法：{exc}",
            )
        except Exception as exc:  # noqa: BLE001 - 工具层需兜底任意异常保证循环继续
            return ToolResult(
                success=False,
                output="",
                error=f"工具 '{name}' 执行失败：{exc}",
            )


def build_default_registry(snapshot: RepositorySnapshot) -> ToolRegistry:
    """构建包含全部默认工具的注册表（需求 4.4）。

    默认工具：read_tree、read_file、read_readme、read_metadata。
    """

    from .read_file import ReadFileTool
    from .read_metadata import ReadMetadataTool
    from .read_readme import ReadReadmeTool
    from .read_tree import ReadTreeTool

    registry = ToolRegistry(snapshot)
    registry.register(ReadTreeTool())
    registry.register(ReadFileTool())
    registry.register(ReadReadmeTool())
    registry.register(ReadMetadataTool())
    return registry
