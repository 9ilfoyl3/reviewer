"""Reviewer 后端应用包。

多 Agent 协作的 GitHub 仓库评估工具后端，采用分层解耦结构：
- api/     API 层（REST + SSE），与执行层隔离
- agent/   Agent 流水线层（核心，多 Agent ReAct 协作）
- github/  GitHub 客户端层
- llm/     模型调用层
- queue/   队列层（Redis Stream 任务队列 + 会话状态）
- events/  事件桥接层（跨进程 EventBus / SSE）
- worker/  Worker 执行层
- models/  数据模型层
"""
