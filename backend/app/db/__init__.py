"""PostgreSQL 持久化层。

Reviewer 的会话运行态存于 Redis（队列 / Pub/Sub / 会话 Hash），本层负责需要
**长期留存**的两类数据（数据流向清晰、与运行态解耦）：

- ``review_records``：每次仓库评估的历史记录。按仓库聚合，供前端侧边栏展示
  「每个仓库一段独立历史」。
- ``model_configs``：前端可配置的 LLM 模型（简化版），Worker 优先使用其中的
  默认配置，缺省时回退到环境变量。
"""
