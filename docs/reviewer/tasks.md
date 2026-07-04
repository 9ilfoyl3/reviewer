# Implementation Plan: Reviewer 多 Agent 仓库评估工具

## Overview

本实施计划将设计文档转化为一系列增量式编码任务。后端使用 Python（FastAPI + Uvicorn + httpx + redis-py asyncio + Pydantic v2 + pytest/hypothesis），前端使用 TypeScript（React 18 + Vite + Tailwind CSS v4 + shadcn/ui + vitest）。

任务遵循设计的目录结构（`reviewer/backend/` 与 `reviewer/frontend/`），并按增量顺序推进：脚手架与配置 → 数据模型（含往返序列化与 PBT）→ GitHub 客户端 → LLM Provider → Redis 队列/会话状态/跨进程 EventBus/SSE 桥接 → 多 Agent ReAct 流水线（基类 + 工具集 + 三角色 + Final_Judge 分数钳制）→ API 层 → Worker 消费者（并发信号量 + 失败隔离）→ 前端 → 组装与 README → 测试补全。

**重点**：多 Agent ReAct 流水线（任务 6）与后端并发/队列设计（任务 5、8）是核心，需优先保证其正确性与可扩展性。

每个任务都引用其对应的需求条款。带 `*` 的子任务为可选测试任务，可跳过以加速 MVP。

## Tasks

- [x] 1. 搭建项目脚手架与配置
  - [x] 1.1 创建后端目录结构与依赖清单
    - 在工作区根目录创建 `reviewer/backend/app/` 分层目录（`api/`、`queue/`、`events/`、`worker/`、`agent/`（含 `tools/`、`prompts/`）、`github/`、`llm/`、`models/`）与 `tests/` 目录
    - 编写 `backend/requirements.txt`：fastapi、uvicorn、httpx、redis、pydantic、pydantic-settings、pytest、hypothesis、pytest-asyncio
    - 创建各包的 `__init__.py`，确保 API 层、Agent 流水线层、GitHub 客户端层、模型调用层四模块相互隔离
    - _Requirements: 9.1, 9.2_

  - [x] 1.2 实现后端配置与启动期 fail-fast 校验
    - 在 `backend/app/config.py` 用 pydantic-settings 定义 Settings：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`（必需）、`GITHUB_TOKEN`、`REDIS_URL`、`REVIEW_MAX_CONCURRENT`（默认 4）、`AGENT_MAX_ITERATIONS`（默认 8，范围 1–20）
    - 实现启动校验函数：任一必需项缺失/为空时逐项打印缺失环境变量名称并终止启动；`GITHUB_TOKEN` 缺失时打印提示并继续
    - 编写 `backend/app/logging_config.py` 日志配置
    - 创建 `backend/.env.example`
    - _Requirements: 7.2, 7.3, 9.4, 9.5_

  - [x] 1.3 编写配置启动校验单元测试
    - 测试必需变量缺失时逐项报错并终止、可选变量缺失时提示并继续、`AGENT_MAX_ITERATIONS` 越界处理
    - _Requirements: 7.3, 9.4, 9.5_

  - [x] 1.4 创建前端脚手架
    - 在 `reviewer/frontend/` 初始化 React 18 + TypeScript + Vite 项目，配置 Tailwind CSS v4 与 shadcn/ui
    - 编写 `package.json`（含 react、typescript、vite、tailwindcss、shadcn 相关、react-bits/gsap/motion、vitest、@testing-library/react）、`vite.config.ts`、`tailwind.config.js`、`index.html`
    - 建立 `src/` 目录结构（`pages/`、`components/`（含 `ui/`）、`hooks/`、`lib/`、`types/`），沿用 artoo 的配色、排版与组件样式
    - _Requirements: 8.1, 8.2, 9.1, 9.2_

- [x] 2. 实现核心数据模型与序列化/解析
  - [x] 2.1 实现 Repository_Snapshot 数据模型与序列化器/解析器
    - 在 `backend/app/models/snapshot.py` 用 Pydantic v2 定义 `RepositoryMetadata`、`TreeEntry`、`RepositorySnapshot`
    - 实现 `serialize_snapshot(snapshot) -> str`（`model_dump_json`，UTF-8，含全部字段）
    - 实现 `parse_snapshot(text) -> RepositorySnapshot`：先 `json.loads` 探测语法（非法抛带原因的 `SnapshotParseError`），再 `model_validate_json`（缺字段/类型不匹配抛带字段名与原因的错误）
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 2.2 编写 Repository_Snapshot 往返基于属性的测试
    - **Property 1: Repository_Snapshot 序列化往返一致**
    - **Validates: Requirements 3.1, 3.2, 3.5**
    - 使用 hypothesis 生成合法 Snapshot（覆盖 Unicode/特殊字符、空 README、大规模目录树），运行 ≥100 样例，验证 `parse_snapshot(serialize_snapshot(x)) == x`
    - _Requirements: 10.2_

  - [x] 2.3 编写 Snapshot 解析错误单元测试
    - 覆盖非 JSON 文本、缺必需字段、字段类型不匹配，断言抛描述性错误且不返回对象
    - _Requirements: 3.3, 3.4_

  - [x] 2.4 实现 Health_Report 数据模型与序列化器/解析器
    - 在 `backend/app/models/report.py` 定义 `MetadataSummary`、`LanguagePercent`、`CodeAuditorOpinion`、`ProductValueOpinion`、`HealthReport`（`score` 用 `Field(ge=0, le=100)`）
    - 实现 `serialize_report` / `parse_report`，解析不符合结构的 JSON 抛描述性错误
    - _Requirements: 6.1, 6.2, 6.7, 6.8_

  - [x] 2.5 编写 Health_Report 往返基于属性的测试
    - **Property 2: Health_Report 序列化往返一致**
    - **Validates: Requirements 6.7, 6.9**
    - 使用 hypothesis 生成五部分齐全、总分 0–100 的合法 Health_Report，运行 ≥100 样例，验证往返相等
    - _Requirements: 10.3_

  - [x] 2.6 实现 Analysis_Session 状态模型
    - 在 `backend/app/queue/session_store.py` 定义 `SessionStatus` 枚举与 `AnalysisSession` 模型（含 `queued/running/completed/failed` 状态与 `error` 字段）
    - _Requirements: 1.4_

- [x] 3. 实现 GitHub 客户端层
  - [x] 3.1 实现 GitHubClient 抓取与归一化
    - 在 `backend/app/github/client.py` 基于 `httpx.AsyncClient` 实现 `fetch_snapshot(owner, repo)`
    - `_get_metadata`：抓取 stars/forks/open_issues/languages/pushed_at，时间转 ISO 8601 UTC
    - `_get_readme`：抓取默认分支 README；404 无 README 时置空字符串继续，不中止
    - `_get_tree`：递归抓取目录树，深度上限 10 层、条目上限 10000，超限截断并置 `tree_truncated=True`
    - `WHERE` 环境提供 `GITHUB_TOKEN` 时请求头带 `Authorization: Bearer <token>`
    - 归一化为 `RepositorySnapshot`
    - _Requirements: 2.1, 2.2, 2.3, 2.6, 2.8, 2.9_

  - [x] 3.2 实现 GitHub 超时、重试与错误降级
    - 单请求超时 15s；失败最多重试 2 次、间隔 ≥1s；3 次尝试仍失败返回超时错误、不生成 Snapshot
    - 404 仓库不存在/非公开 → 返回资源不存在错误、不生成 Snapshot
    - 403 且 `X-RateLimit-Remaining: 0` → 返回速率限制错误，含 `X-RateLimit-Reset` 转 ISO 8601 UTC 的重置时间
    - _Requirements: 2.4, 2.5, 2.7, 2.10_

  - [x] 3.3 编写目录树截断基于属性的测试
    - **Property 7: 目录树截断上限**
    - **Validates: Requirements 2.3**
    - 使用 hypothesis 生成任意规模目录树，断言归一化后 tree 条目数 ≤10000、深度 ≤10，超限时 `tree_truncated` 为 True
    - _Requirements: 2.3_

  - [x] 3.4 编写 GitHub 客户端单元测试
    - 用 mock 响应测试字段归一化、404 资源不存在、403 速率限制含重置时间、超时重试次数与间隔、无 README 置空继续
    - _Requirements: 2.1, 2.4, 2.5, 2.7, 2.9, 2.10_

- [x] 4. 实现 LLM Provider 模型调用层
  - [x] 4.1 实现 LLMProvider 客户端
    - 在 `backend/app/llm/provider.py` 基于 `httpx.AsyncClient` 实现调用 OpenAI 兼容 `/v1/chat/completions`，连接与响应超时 60s
    - 实现 `stream_with_tools(messages, tools, temperature)` 流式接收，逐片段产出 StreamChunk
    - 从配置读取 base_url/api_key/model，初始化时校验缺失则中止推理相关初始化
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [x] 4.2 实现 LLM 重试分流策略
    - 瞬态错误 429/500/502/503/504 → 指数退避重试（初始 1s，每次翻倍，最多 2 次）
    - 非瞬态错误 400/401/403/404 → 立即停止、不重试
    - 重试耗尽仍失败 → 抛出携带失败原因的错误供上层发 error 事件
    - _Requirements: 7.5, 7.6, 7.7_

  - [x] 4.3 编写 LLM Provider 单元测试
    - 参数化各瞬态码断言退避重试 2 次；各非瞬态码断言不重试；重试耗尽产生错误；缺配置 fail-fast
    - _Requirements: 7.1, 7.3, 7.5, 7.6, 7.7_

- [x] 5. 实现 Redis 队列、会话状态与跨进程 EventBus/SSE 桥接（重点）
  - [x] 5.1 实现 Redis Stream 任务队列
    - 在 `backend/app/queue/task_queue.py` 封装 Redis Stream `reviewer:tasks` + Consumer Group `reviewer-workers`：API `XADD` 入队、Worker `XREADGROUP` 消费、成功 `XACK`
    - 实现 `XAUTOCLAIM` 孤儿消息回收接口
    - 实现以 `(owner, repo)` 归一化哈希为去重键的入队幂等逻辑（已有活跃会话则复用 session_id）
    - _Requirements: 1.6_

  - [x] 5.2 实现会话状态存储
    - 在 `backend/app/queue/session_store.py` 用 Redis Hash `reviewer:session:{sid}` 实现会话创建、状态流转（queued→running→completed/failed）、超时巡检置 failed
    - _Requirements: 1.4_

  - [x] 5.3 定义 Progress_Event 事件类型
    - 在 `backend/app/events/types.py` 定义 `EventType` 枚举（agent_start/thought/tool_call/tool_result/agent_complete/final_report/error/heartbeat）与 `ProgressEvent` 模型（含单调递增 `seq`）
    - 定义各类型 data 载荷结构
    - _Requirements: 5.2_

  - [x] 5.4 实现 Worker 侧 ReviewEventBus（发布到 Redis Pub/Sub）
    - 在 `backend/app/events/event_bus.py` 实现 `ReviewEventBus.emit(event)`：将事件 `model_dump_json()` 后 `PUBLISH` 到 `reviewer:events:{session_id}` 频道
    - 保持与进程内 EventBus 一致接口，业务代码无感知跨进程
    - _Requirements: 5.2_

  - [x] 5.5 实现 API 侧 EventBridge（订阅 Pub/Sub → SSE）
    - 在 `backend/app/events/bridge.py` 用 `redis.pubsub()` 订阅 `reviewer:events:{sid}`，按 `seq` 保序生成 SSE 帧 `event: {type}\ndata: {json}\n\n`
    - 实现心跳：`asyncio.wait_for(queue.get(), timeout=15)`，15 秒无事件发一条 heartbeat 帧
    - 收到 final_report 或 error 后关闭流并停止推送任何后续事件
    - _Requirements: 5.1, 5.2, 5.8, 5.9_

- [x] 6. 实现多 Agent ReAct 协作流水线（核心）
  - [x] 6.1 实现 Agent_Tool 工具集
    - 在 `backend/app/agent/tools/` 实现 `base.py`（`ToolResult` 模型 + `ToolRegistry`）、`read_tree.py`、`read_file.py`、`read_readme.py`、`read_metadata.py`，均对内存中 Repository_Snapshot 操作
    - `read_file`：命中返回文本，单次上限 100000 字符，超出截断并标记 `truncated=True`；文件不存在返回"文件不存在"结果且不中断
    - 工具名不在注册表或参数非法时返回带错误原因的 `ToolResult(success=False)`，循环继续
    - _Requirements: 4.4, 4.5, 4.6, 4.15, 4.16_

  - [x] 6.2 编写 read_file 长度上限基于属性的测试
    - **Property 6: read_file 内容长度上限**
    - **Validates: Requirements 4.5, 4.15**
    - 使用 hypothesis 生成任意长度文件内容，断言返回字符数 ≤100000；未超上限时全等原内容，超上限时截断至 100000 且 `truncated=True`
    - _Requirements: 4.5, 4.15_

  - [x] 6.3 实现 BaseReActAgent 循环骨架
    - 在 `backend/app/agent/base.py` 实现 `BaseReActAgent`（`system_prompt`、`synthesize_fallback` 抽象方法与 `run` 循环）
    - ReAct 循环：每轮先流式 Think（逐 token emit thought）→ 分析响应 → Act 调用工具（emit tool_call/tool_result 并将结果纳入下一轮上下文）或提交结论
    - 轮数上限从配置读取（默认 8，范围 1–20）；达上限未提交结论则调用 `synthesize_fallback` 基于已有观察合成兜底结论
    - 为启动、每次工具调用、每次工具结果、每次结论提交通过 EventBus 发射对应事件
    - _Requirements: 4.2, 4.3, 4.7, 4.8, 4.14, 7.4_

  - [x] 6.4 实现 Code_Auditor 与 Product_Value_Agent
    - 在 `backend/app/agent/code_auditor.py` 实现 Code_Auditor，输出目录结构与核心代码质量技术意见（≥1 优点 + ≥1 改进点）
    - 在 `backend/app/agent/product_value.py` 实现 Product_Value_Agent，输出 README 清晰度/实用价值/开源活跃度三维度评估（每维度 ≥1 条）
    - 在 `backend/app/agent/prompts/` 编写各角色系统提示词（强制结构化 JSON 提交结论）
    - _Requirements: 4.1, 4.9, 4.10_

  - [x] 6.5 实现 Final_Judge 分数钳制纯函数
    - 在 `backend/app/agent/final_judge.py` 实现 `clamp_score(raw) -> int`：None→0、非整数取整、越界钳制到 [0,100]
    - _Requirements: 4.13, 6.2, 6.3_

  - [x] 6.6 编写 Final_Judge 分数钳制基于属性的测试
    - **Property 3: Final_Judge 分数钳制**
    - **Validates: Requirements 4.13, 6.2, 6.3**
    - 使用 hypothesis 生成任意整数/浮点数/None，断言输出恒为 [0,100] 整数
    - _Requirements: 10.5_

  - [x] 6.7 编写 Final_Judge 分数钳制边界单元测试
    - 覆盖 -1、0、50、100、101，断言输出钳制在 [0,100]
    - _Requirements: 10.5_

  - [x] 6.8 实现 Final_Judge 合成与 Agent_Pipeline 编排器
    - 在 `backend/app/agent/final_judge.py` 实现 Final_Judge：汇总 A、B 结论生成 0–100 整数总分（经 clamp_score）与 3–10 条综合建议，组装 Health_Report
    - 在 `backend/app/agent/pipeline.py` 实现 Agent_Pipeline：`asyncio.gather` 并行执行 Code_Auditor 与 Product_Value_Agent，两者结论就绪后传给 Final_Judge，发射 final_report 事件
    - _Requirements: 4.1, 4.11, 4.12, 4.14, 5.6, 6.1_

  - [x] 6.9 编写 Agent_Pipeline 编排测试
    - 用 LLM_Provider 与 GitHub_Client 的 mock 替身，断言过程中未发起任何真实外部网络调用
    - 验证编排步骤按预期顺序：三 Agent 执行、A/B 结论传给 Final_Judge、各阶段发射对应事件
    - _Requirements: 10.4_

- [x] 7. 检查点 - 确保后端核心（数据模型、GitHub、LLM、流水线）测试通过
  - 确保所有测试通过，如有疑问请询问用户。

- [x] 8. 实现 API 层与 Worker 执行层
  - [x] 8.1 实现 API 请求/响应模型与 URL 校验解析
    - 在 `backend/app/api/schemas.py` 定义请求/响应 Pydantic 模型
    - 实现后端 URL 校验与 owner/repo 解析（支持带 .git/不带 .git HTTPS、SSH 格式；拒绝空、缺主机名、非 git 协议、超 2048 字符）；解析失败返回 HTTP 400 与原因、不建会话
    - _Requirements: 1.4, 1.5_

  - [x] 8.2 编写 URL 解析单元测试与往返属性测试
    - **Property 4: URL 解析往返**
    - **Validates: Requirements 1.4, 1.5**
    - 单元测试覆盖合法输入（带 .git HTTPS、不带 .git HTTPS、SSH 格式）与非法输入（空字符串、缺主机名、非 git 协议、超 2048 字符）
    - 属性测试：合法 owner/repo 组装的 URL 解析还原相等；非法字符串解析失败不产出会话
    - _Requirements: 10.1_

  - [x] 8.3 实现 POST /api/analysis 与 SSE 端点
    - 在 `backend/app/api/analysis.py` 实现 `POST /api/analysis`：校验 URL + 解析 owner/repo + 创建会话状态写入 Redis + 任务入队，返回 session_id
    - 实现 `GET /api/analysis/{sid}/events`：建立 SSE 流，2 秒内经 EventBridge 订阅 Redis Pub/Sub 转发事件
    - 在 `backend/app/main.py` 装配 FastAPI 应用（API 进程入口，不执行抓取或推理）
    - _Requirements: 1.4, 1.5, 5.1, 5.2, 9.2_

  - [x] 8.4 实现 Worker 消费者与单任务执行编排
    - 在 `backend/app/worker/consumer.py` 实现 `ReviewConsumer`：`XREADGROUP` 消费 + `asyncio.Semaphore(REVIEW_MAX_CONCURRENT)` 并发控制 + 单任务 `try/except` 失败隔离（异常仅置该 session failed 并发 error 事件）
    - 在 `backend/app/worker/runner.py` 实现单任务执行编排：会话置 running → GitHub 抓取 → Agent_Pipeline → 更新会话状态；`httpx.AsyncClient` 有界连接池
    - 在 `backend/app/worker_main.py` 实现 Worker 进程入口，可水平扩展多副本
    - _Requirements: 5.7, 7.7_

  - [x] 8.5 编写 Worker 失败隔离与并发单元测试
    - 断言单任务异常不影响同进程其它任务、信号量限制并发数、error 事件正确发射
    - _Requirements: 5.7_

- [x] 9. 实现前端界面与交互
  - [x] 9.1 实现前端 URL 校验纯函数与类型定义
    - 在 `frontend/src/lib/urlValidation.ts` 实现 `validateRepoUrl(input): {valid, owner?, repo?, error?}`：空/超 2048 字符/不符合 `https://github.com/{owner}/{repo}` 格式返回具体失败原因
    - 在 `frontend/src/types/events.ts` 定义 ProgressEvent / HealthReport TS 类型
    - 在 `frontend/src/lib/sseParser.ts`、`frontend/src/lib/api.ts` 实现 SSE 解析与 API 调用封装
    - _Requirements: 1.1, 1.3_

  - [x] 9.2 编写前端 URL 校验单元测试
    - **Property 5: 前端 URL 校验分类**
    - **Validates: Requirements 1.3**
    - 覆盖合法输入与非法输入（空、格式错误），断言校验结果与预期一致
    - _Requirements: 10.6_

  - [x] 9.3 实现 useAnalysisStream SSE 消费 hook
    - 在 `frontend/src/hooks/useAnalysisStream.ts` 用 `EventSource` 消费 `/api/analysis/{sid}/events`，用 `useReducer` 按事件类型归约（thought 追加/tool_call 追加/final_report 渲染/error 提示）
    - 10 秒无数据判定中断并显示提示，自动重连最多 3 次、间隔 3 秒（带 `Last-Event-ID`），3 次失败保留中断提示与重新发起入口
    - 收到 final_report/error 后主动 close
    - _Requirements: 5.3, 5.4, 8.3, 8.4, 8.5, 8.6_

  - [x] 9.4 实现 RepoUrlForm 与提交流程
    - 在 `frontend/src/components/RepoUrlForm.tsx` 基于 shadcn Input+Button+Form 实现 URL 输入（1–2048 字符）与提交，内联显示校验错误
    - 合法时 `POST /api/analysis` 并禁用提交控件；分析中持续禁用；30s 无响应或非成功响应显示失败/超时提示并重新启用
    - _Requirements: 1.1, 1.2, 1.3, 1.6, 1.7_

  - [x] 9.5 实现 Agent 进度看板组件
    - 在 `frontend/src/components/` 实现 `AgentBoard.tsx`、`AgentCard.tsx`（状态徽章：等待/执行/完成/失败）、`ThoughtStream.tsx`（逐 token 追加渲染 thought）、`ToolCallItem.tsx`（工具名 + 结果摘要，>500 字符截断）
    - 每 ≤2s 刷新 Agent 实时状态，Agent 完成 2s 内更新为已完成
    - _Requirements: 5.4, 5.5, 8.3, 8.4_

  - [x] 9.6 实现 HealthReport 报告卡片
    - 在 `frontend/src/components/HealthReport.tsx` 基于 shadcn Card+Progress+Badge 渲染五部分（元数据摘要、代码审计意见、产品价值意见、综合建议、总分环形进度）
    - 缺任一部分显示占位提示并保留已收到部分；Star/Fork 整数展示；语言分布归一化为「语言名 + 占比」列表且占比之和为 100%（四舍五入补偿）
    - _Requirements: 6.4, 6.5, 6.6_

  - [x] 9.7 编写前端语言占比归一化单元测试
    - **Property 8: 语言占比归一化**
    - **Validates: Requirements 6.6**
    - 随机字节分布断言归一化产出的占比之和恒为 100%
    - _Requirements: 6.6_

  - [x] 9.8 编写 SSE Progress_Event 渲染测试
    - 用 @testing-library/react 分别覆盖 thought、tool_call、final_report、error 四种事件，断言各渲染出对应界面元素（thought 追加渲染、tool_call 显示工具名与摘要截断、final_report 渲染五部分、error 显示中断提示）
    - 用假定时器测试提交禁用/重新启用、30s 超时提示、10s 中断判定与重连（≤3 次、间隔 3s）
    - _Requirements: 10.7_

- [x] 10. 组装与动画集成
  - [x] 10.1 装配主页面与全局反馈
    - 在 `frontend/src/pages/Review.tsx`、`frontend/src/App.tsx`、`frontend/src/main.tsx` 将 RepoUrlForm、AgentBoard、HealthReport 与 useAnalysisStream 串联为完整单向数据流
    - 接入 shadcn Toast/Sonner 处理请求失败、超时、连接中断提示
    - _Requirements: 1.6, 1.7, 8.5, 8.6_

  - [x] 10.2 集成动画方案
    - 使用 react-bits / GSAP / motion 实现：Agent 卡片状态切换过渡、思考文本逐 token 淡入、分数环形进度动画、报告分段 stagger 揭示、工具调用进入动画、加载与连接中断骨架脉冲
    - 保持与 artoo 视觉风格一致，仅强化状态反馈不干扰阅读
    - _Requirements: 8.2_

- [x] 11. 编写运行说明文档
  - [x] 11.1 编写 reviewer/README.md 与环境变量样例
    - 在 `reviewer/README.md` 依次编写前端本地运行步骤、后端本地运行步骤（含 API 进程与 Worker 进程启动命令、Redis 依赖）
    - 环境变量清单至少列出 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`GITHUB_TOKEN` 四项及各自用途，并补充 `REDIS_URL`、`REVIEW_MAX_CONCURRENT`、`AGENT_MAX_ITERATIONS`
    - 创建 `reviewer/.env.example`
    - _Requirements: 9.3_

- [x] 12. 最终检查点 - 确保前后端全部测试通过
  - 确保所有测试通过（后端 `pytest`，前端 `vitest --run`），如有疑问请询问用户。

## Notes

- 带 `*` 的子任务为可选测试任务，可跳过以加速 MVP
- 每个任务引用具体需求条款以保证可追溯性
- 检查点确保增量验证
- 属性测试验证 8 条正确性属性（Property 1–8），单元测试验证具体样例与边界
- 核心重点为任务 6（多 Agent ReAct 流水线）与任务 5、8（Redis 队列 + 可水平扩展 Worker + async httpx 有界并发）
- 后端属性测试均使用 hypothesis（≥100 样例），前端使用 vitest + @testing-library/react

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.4"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1", "2.4", "2.6", "9.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.5", "3.1", "4.1", "5.1", "5.2", "5.3", "6.1", "9.2", "9.3"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "4.2", "4.3", "5.4", "5.5", "6.2", "6.3", "6.5", "9.4", "9.5", "9.6"] },
    { "id": 4, "tasks": ["6.4", "6.6", "6.7", "8.1", "9.7", "9.8"] },
    { "id": 5, "tasks": ["6.8", "8.2", "8.3"] },
    { "id": 6, "tasks": ["6.9", "8.4", "10.1", "10.2"] },
    { "id": 7, "tasks": ["8.5", "11.1"] }
  ]
}
```
