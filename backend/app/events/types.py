"""Progress_Event 事件类型与载荷定义。

对应需求 5.2 与设计文档「EventBus / SSE 跨进程流式推送设计」小节。

- ``EventType`` 枚举定义 Agent 流水线在执行过程中发射的全部事件类型
  （agent_start / thought / tool_call / tool_result / agent_complete /
  final_report / error / heartbeat）。
- ``ProgressEvent`` 为统一事件模型，含单调递增的 ``seq`` 序号，保证前端
  按序渲染；``data`` 字段承载类型相关载荷。
- 各类型的 ``data`` 载荷结构以独立的 Pydantic 模型显式定义，便于 Worker
  侧构造事件与 API 侧转发时进行结构约束。

使用 Pydantic v2 建模，天然支持 ``model_dump_json()`` 序列化，供
``ReviewEventBus`` 发布到 Redis Pub/Sub、``EventBridge`` 转发为 SSE 帧。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from app.models.report import HealthReport


class EventType(str, Enum):
    """Progress_Event 事件类型集合（需求 5.2）。"""

    AGENT_START = "agent_start"
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_COMPLETE = "agent_complete"
    FINAL_REPORT = "final_report"
    ERROR = "error"
    HEARTBEAT = "heartbeat"  # 保活（需求 5.9）


# --------------------------------------------------------------------------- #
# 各类型 data 载荷结构
# --------------------------------------------------------------------------- #


class ThoughtData(BaseModel):
    """``thought`` 载荷：Agent 推理的增量片段（需求 5.3）。"""

    content: str
    iteration: int


class ToolCallData(BaseModel):
    """``tool_call`` 载荷：工具调用请求。"""

    tool: str
    args: dict


class ToolResultData(BaseModel):
    """``tool_result`` 载荷：工具执行结果摘要。

    ``summary`` 超过 500 字符时由前端截断显示（需求 5.5），``truncated``
    标记结果是否在生成摘要时已被截断。
    """

    tool: str
    summary: str
    truncated: bool = False


class AgentLifecycleData(BaseModel):
    """``agent_start`` / ``agent_complete`` 载荷。

    ``agent`` 为归属 Agent 角色名；``conclusion`` 仅在 ``agent_complete``
    时可选携带该角色提交的结论摘要。
    """

    agent: str
    conclusion: str | None = None


class FinalReportData(BaseModel):
    """``final_report`` 载荷：完整的 Health_Report（需求 5.6、6.1）。"""

    report: HealthReport


class ErrorData(BaseModel):
    """``error`` 载荷：失败原因描述与所处阶段（需求 5.7）。"""

    message: str
    stage: str


# --------------------------------------------------------------------------- #
# 统一事件模型
# --------------------------------------------------------------------------- #


class ProgressEvent(BaseModel):
    """Event_Bus 发射的进度事件（需求 5.2）。

    - ``type``：事件类型。
    - ``session_id``：归属 Analysis_Session。
    - ``agent``：归属 Agent 角色（心跳等非 Agent 事件为 ``None``）。
    - ``seq``：单调递增序号，保证前端按序渲染并支持断线重连补发。
    - ``data``：类型相关载荷（结构见上方各 ``*Data`` 模型）。
    - ``ts``：事件产生时间戳（Unix 秒）。
    """

    type: EventType
    session_id: str
    agent: str | None = None
    seq: int
    data: dict
    ts: float
