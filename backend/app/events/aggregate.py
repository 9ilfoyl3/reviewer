"""将一串 Progress_Event 聚合为「按 Agent 归约的过程视图」，用于持久化与回看。

流式过程（各 Agent 的思考、工具调用）通过 SSE 实时推送，是易失的。为了在刷新
或历史回看时仍能还原「多 Agent 协作过程」，Worker 在执行时收集本会话发射的事件，
用本模块聚合成与前端 ``AgentView`` 同构的紧凑结构，随评估记录一并落库。

聚合规则与前端 ``useAnalysisStream`` 的归约保持一致：
- ``agent_start``：该 Agent 置执行中。
- ``thought``：追加思考增量，更新轮次。
- ``tool_call``：追加一条未完成的工具调用。
- ``tool_result``：就近匹配同名未完成调用，补齐摘要并标记完成；无匹配则补一条完成项。
- ``agent_complete``：该 Agent 置完成。
- ``error``：将仍在执行的 Agent 置失败。
"""

from __future__ import annotations

from typing import Any

# 工具结果摘要展示上限（与前端 sseParser.TOOL_SUMMARY_MAX 一致）。
TOOL_SUMMARY_MAX = 500


def _agent_name_of(event: dict[str, Any]) -> str | None:
    agent = event.get("agent")
    if isinstance(agent, str) and agent:
        return agent
    data = event.get("data") or {}
    da = data.get("agent")
    return da if isinstance(da, str) and da else None


def _truncate(summary: str) -> tuple[str, bool]:
    if len(summary) > TOOL_SUMMARY_MAX:
        return summary[:TOOL_SUMMARY_MAX] + "…", True
    return summary, False


def aggregate_agents(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把事件列表聚合为按首次出现顺序排列的 Agent 过程视图列表。

    Args:
        events: 本会话发射的 Progress_Event（``model_dump()`` 后的 dict）列表。

    Returns:
        与前端 ``AgentView`` 同构的字典列表：
        ``{name, status, thought, iteration, tools:[{tool,args,summary,truncated,completed}]}``。
    """
    agents: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def ensure(name: str) -> dict[str, Any]:
        if name not in agents:
            agents[name] = {
                "name": name,
                "status": "waiting",
                "thought": "",
                "iteration": 0,
                "tools": [],
            }
            order.append(name)
        return agents[name]

    for event in events:
        etype = event.get("type")
        data = event.get("data") or {}
        name = _agent_name_of(event)

        if etype == "error":
            for a in agents.values():
                if a["status"] == "running":
                    a["status"] = "failed"
            continue

        if not name:
            continue
        agent = ensure(name)

        if etype == "agent_start":
            agent["status"] = "running"
        elif etype == "thought":
            agent["status"] = "running"
            agent["thought"] += str(data.get("content", ""))
            agent["iteration"] = max(agent["iteration"], int(data.get("iteration", 0) or 0))
        elif etype == "tool_call":
            agent["status"] = "running"
            agent["tools"].append(
                {
                    "tool": str(data.get("tool", "")),
                    "args": data.get("args") or {},
                    "completed": False,
                }
            )
        elif etype == "tool_result":
            tool = str(data.get("tool", ""))
            summary, truncated = _truncate(str(data.get("summary", "")))
            truncated = truncated or bool(data.get("truncated", False))
            # 就近匹配同名未完成调用。
            idx = next(
                (
                    i
                    for i in range(len(agent["tools"]) - 1, -1, -1)
                    if agent["tools"][i]["tool"] == tool and not agent["tools"][i]["completed"]
                ),
                -1,
            )
            if idx == -1:
                agent["tools"].append(
                    {
                        "tool": tool,
                        "args": {},
                        "summary": summary,
                        "truncated": truncated,
                        "completed": True,
                    }
                )
            else:
                agent["tools"][idx].update(
                    {"summary": summary, "truncated": truncated, "completed": True}
                )
        elif etype == "agent_complete":
            agent["status"] = "completed"

    return [agents[name] for name in order]
