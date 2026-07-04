# Requirements Document

## Introduction

Reviewer 是一个多 Agent 协作的 GitHub 仓库"评估"工具。用户在 Web 界面输入一个公开 GitHub 仓库 URL，系统通过 GitHub API 抓取仓库元数据、README、代码结构等信息，再由一条具备 ReAct（Think → Act → Observe 循环 + 工具调用）特征的多 Agent 流水线协作分析，最终输出一份结构化的"健康评估报告"（含 0-100 的总分与优化建议）。整个分析过程通过 SSE 以事件流的形式实时推送到前端，用户可逐步看到每个 Agent 的思考、工具调用、阶段性结论与最终报告。

本系统在工程结构上参考工作区内 WeKnora、SAG、artoo 三个既有项目，采用干净、分层解耦的系统架构。前端技术栈与视觉风格直接沿用 artoo（React 18 + TypeScript + Tailwind CSS v4 + Vite）；后端沿用 artoo 风格（FastAPI + Uvicorn，EventBus 驱动 SSE 流式推送，通过 HTTP 调用外部 LLM 服务）。系统作为独立项目存放于工作区根目录下的 `reviewer` 目录。

本文档遵循 EARS 模式与 INCOSE 质量规则描述需求。多 Agent ReAct 流水线是本系统的核心，相关需求（需求 4、需求 5、需求 6）为重点。

## Glossary

- **Reviewer_System**：本"评估"工具的整体系统，包含前端、后端与 Agent 流水线。
- **Frontend**：基于 React 18 + TypeScript + Tailwind CSS v4 + Vite 的 Web 前端应用。
- **Backend**：基于 FastAPI + Uvicorn 的后端服务。
- **GitHub_Client**：后端中负责调用 GitHub REST API 抓取仓库数据的模块。
- **Repository_URL**：用户输入的公开 GitHub 仓库地址，形如 `https://github.com/{owner}/{repo}`。
- **Repository_Snapshot**：由 GitHub_Client 抓取并归一化后的仓库数据集合，包含元数据、README 文本、代码目录结构与代表性代码文件内容。
- **Repository_Metadata**：仓库元数据，包含 Star 数、Fork 数、编程语言分布、Open Issue 数、最近提交时间等字段。
- **Agent_Pipeline**：多 Agent 协作流水线，编排 Code_Auditor、Product_Value_Agent、Final_Judge 三个角色的协作。
- **ReAct_Loop**：单个 Agent 内部的 Think → Act → Observe 循环，Agent 在循环中进行推理、调用工具、观察工具结果，直至提交本角色结论。
- **Agent_Tool**：Agent 在 ReAct_Loop 中可调用的工具，例如读取目录结构、读取指定文件内容、读取 README、查询仓库元数据。
- **Code_Auditor**：代码审计 Agent（Agent A），评估目录结构与核心代码质量并给出技术意见。
- **Product_Value_Agent**：产品价值 Agent（Agent B），评估 README 清晰度、实用价值与开源活跃度/热度。
- **Final_Judge**：总分裁判 Agent（Agent C），汇总 Agent A 与 Agent B 的意见，生成 0-100 的总分与综合优化建议。
- **Health_Report**：最终输出的结构化健康评估报告，包含各 Agent 结论、总分与优化建议。
- **Event_Bus**：后端事件总线，Agent 流水线在执行过程中向其发射进度事件。
- **Progress_Event**：Event_Bus 发射的进度事件，类型包括 thought、tool_call、tool_result、agent_start、agent_complete、final_report、error。
- **SSE_Stream**：后端向前端推送 Progress_Event 的 Server-Sent Events 通道。
- **LLM_Provider**：后端通过 HTTP 调用的外部大语言模型服务（OpenAI 兼容接口）。
- **Analysis_Session**：一次完整的仓库评估任务，从提交 URL 到生成 Health_Report 或失败终止。

## Requirements

### 需求 1：仓库 URL 输入与校验

**User Story:** 作为使用者，我想在一个简洁的输入框中提交 GitHub 仓库地址，以便系统开始对该仓库进行评估。

#### 验收标准

1. THE Frontend SHALL 提供一个用于输入 Repository_URL 的文本输入框（接受长度为 1 至 2048 个字符的输入）与一个提交控件。
2. WHEN 用户提交一个符合 `https://github.com/{owner}/{repo}` 格式（owner 与 repo 均为非空且仅由字母、数字、连字符、下划线或点号组成）的 Repository_URL，THE Frontend SHALL 向 Backend 发起创建 Analysis_Session 的请求，并禁用提交控件。
3. IF 用户提交的字符串为空、超过 2048 个字符或不符合 GitHub 仓库 URL 格式，THEN THE Frontend SHALL 在输入框正下方显示指明具体校验失败原因的格式错误提示，并阻止发起分析请求。
4. WHEN Backend 接收到创建 Analysis_Session 的请求，THE Backend SHALL 校验 Repository_URL 并从中解析出 owner 与 repo 两个非空标识。
5. IF Backend 解析 Repository_URL 失败，THEN THE Backend SHALL 返回 HTTP 400 状态码与指明 URL 格式非法的错误信息，且不创建 Analysis_Session。
6. WHILE 一个 Analysis_Session 处于自创建成功起、至该 Session 分析完成或失败前的分析中状态，THE Frontend SHALL 禁用提交控件以阻止重复提交同一分析。
7. IF THE Frontend 在发起创建 Analysis_Session 请求后 30 秒内未收到 Backend 响应，或收到非成功响应，THEN THE Frontend SHALL 显示指明请求失败或超时的错误提示，并重新启用提交控件。

### 需求 2：GitHub 仓库数据抓取

**User Story:** 作为系统，我需要从 GitHub API 抓取仓库的元数据与内容，以便 Agent 有充分的分析依据。

#### 验收标准

1. WHEN 一个 Analysis_Session 开始，THE GitHub_Client SHALL 通过 GitHub REST API 获取 Repository_Metadata，且该 Repository_Metadata SHALL 包含 Star 数、Fork 数、编程语言分布（各语言所占字节数）、Open Issue 数与最近一次提交时间（以 ISO 8601 UTC 时间戳表示）。
2. WHEN 一个 Analysis_Session 开始，THE GitHub_Client SHALL 获取仓库默认分支的 README 文本内容。
3. WHEN 一个 Analysis_Session 开始，THE GitHub_Client SHALL 获取仓库默认分支的代码目录结构，包含文件路径与目录层级，且遍历深度上限为 10 层、文件条目上限为 10000 个；当超过上限时 THE GitHub_Client SHALL 截断并在结果中标记已截断。
4. IF 目标仓库不存在或非公开，THEN THE GitHub_Client SHALL 中止抓取并返回资源不存在错误，且不生成 Repository_Snapshot，且 Backend SHALL 通过 SSE_Stream 发射一条 error 类型的 Progress_Event。
5. IF GitHub API 返回速率限制响应（HTTP 403 且剩余额度为 0），THEN THE GitHub_Client SHALL 终止抓取并返回速率限制错误信息，且该信息 SHALL 包含以 ISO 8601 UTC 时间戳表示的额度重置时间。
6. WHERE 环境配置中提供了 GitHub 访问令牌，THE GitHub_Client SHALL 在请求头中携带该令牌以提升速率限制额度。
7. IF 单个 GitHub API 请求超过 15 秒未返回，THEN THE GitHub_Client SHALL 中止该请求并对其最多重试 2 次，且每次重试间隔至少 1 秒。
8. WHEN GitHub_Client 完成抓取，THE GitHub_Client SHALL 将抓取结果归一化为一个 Repository_Snapshot 对象供 Agent_Pipeline 使用。
9. IF 仓库默认分支不存在 README 文件，THEN THE GitHub_Client SHALL 将 Repository_Snapshot 中的 README 内容置为空字符串并继续执行后续抓取，不中止 Analysis_Session。
10. IF 单个 GitHub API 请求在 3 次尝试（1 次初始请求加 2 次重试）后仍未成功返回，THEN THE GitHub_Client SHALL 终止抓取并返回超时错误信息，且不生成 Repository_Snapshot。

### 需求 3：仓库数据序列化与解析（往返一致）

**User Story:** 作为开发者，我需要 Repository_Snapshot 能够在后端与传输/存储之间可靠地序列化与解析，以便进程间传递与调试时数据不失真。

#### 验收标准

1. WHEN Backend 序列化一个 Repository_Snapshot 对象，THE Backend SHALL 在 5 秒内产出一段包含该对象全部字段、采用 UTF-8 编码的 JSON 文本。
2. WHEN Backend 解析一段合法的 Repository_Snapshot JSON 文本，THE Backend SHALL 返回一个 Repository_Snapshot 对象，且其各字段值与序列化前相等。
3. IF 解析器接收到无法解析为 JSON 的语法非法文本，THEN THE Backend SHALL 返回一条指明语法非法原因的描述性解析错误，且不返回 Repository_Snapshot 对象。
4. IF 解析器接收到语法合法但缺失必需字段或字段类型不匹配的 JSON 文本，THEN THE Backend SHALL 返回一条指明缺失字段或类型不匹配原因的描述性解析错误，且不返回 Repository_Snapshot 对象。
5. FOR ALL 合法的 Repository_Snapshot 对象，先序列化再解析 SHALL 产生一个与原对象在字段存在性、字段类型与字段取值上均相等的 Repository_Snapshot 对象（往返属性）。

### 需求 4：多 Agent ReAct 协作流水线（核心）

**User Story:** 作为使用者，我希望仓库由多个具备自主推理与工具调用能力的 Agent 协作分析，而非固定线性处理，以便获得更深入、更可追溯的分析结论。

#### 验收标准

1. THE Agent_Pipeline SHALL 至少编排 Code_Auditor、Product_Value_Agent、Final_Judge 三个 Agent 角色。
2. WHEN 一个 Agent 在 Agent_Pipeline 中执行，THE Agent SHALL 运行一个 ReAct_Loop，在每一轮中先产生推理文本（Think），再决定调用 Agent_Tool（Act）或提交本角色结论。
3. WHEN 一个 Agent 决定调用 Agent_Tool，THE Agent SHALL 接收该 Agent_Tool 的执行结果（Observe）并将其纳入下一轮推理的上下文。
4. THE Agent_Pipeline SHALL 向 Agent 提供以下 Agent_Tool：读取目录结构、读取指定文件内容、读取 README、查询 Repository_Metadata。
5. WHEN 一个 Agent 调用读取指定文件内容的 Agent_Tool 且目标文件存在于 Repository_Snapshot，THE Agent_Tool SHALL 返回该文件的文本内容，且单次返回内容的字符数上限为 100000。
6. IF 一个 Agent 调用读取指定文件内容的 Agent_Tool 但目标文件不存在于 Repository_Snapshot，THEN THE Agent_Tool SHALL 返回一条"文件不存在"的结果，且 ReAct_Loop SHALL 继续执行。
7. WHILE 一个 Agent 的 ReAct_Loop 迭代轮数未达到配置的最大轮数上限（默认 8 轮，可配置范围为 1 至 20 轮），THE Agent SHALL 继续执行 ReAct_Loop。
8. IF 一个 Agent 的 ReAct_Loop 达到配置的最大轮数上限仍未提交结论，THEN THE Agent SHALL 基于已获得的观察结果合成一个本角色结论并终止循环。
9. WHEN Code_Auditor 提交结论，THE Code_Auditor SHALL 输出针对目录结构与核心代码质量的技术意见，且该意见至少包含 1 个优点与 1 个改进点。
10. WHEN Product_Value_Agent 提交结论，THE Product_Value_Agent SHALL 输出针对 README 清晰度、实用价值与开源活跃度三个维度的评估意见，且每个维度至少包含 1 条评估结论。
11. WHEN Code_Auditor 与 Product_Value_Agent 均已提交结论，THE Agent_Pipeline SHALL 将两者的结论作为输入传递给 Final_Judge。
12. WHEN Final_Judge 提交结论，THE Final_Judge SHALL 输出一个 0 到 100 之间的整数总分与一组包含 3 至 10 条的综合优化建议。
13. IF Final_Judge 产出的分数超出 0 到 100 的范围，THEN THE Agent_Pipeline SHALL 将该分数钳制到 0 到 100 的边界值。
14. THE Agent_Pipeline SHALL 通过 Event_Bus 为每个 Agent 的启动、每次工具调用、每次工具结果与每次结论提交发射对应的 Progress_Event。
15. IF 一个 Agent 调用读取指定文件内容的 Agent_Tool 且目标文件内容字符数超过 100000，THEN THE Agent_Tool SHALL 返回截断至 100000 字符的内容并在结果中标记已截断。
16. IF 一个 Agent 请求调用的工具名称不在可用 Agent_Tool 列表中，或工具调用参数非法，THEN THE Agent_Tool SHALL 返回一条指明错误原因的结果，且 ReAct_Loop SHALL 继续执行。

### 需求 5：Agent 进度的实时流式推送

**User Story:** 作为使用者，我希望实时看到每个 Agent 的思考过程与阶段性结论，以便无需等待整体完成即可跟踪进展并获得流畅体验。

#### 验收标准

1. WHEN 一个 Analysis_Session 开始，THE Backend SHALL 在 2 秒内为该 Analysis_Session 建立一条 SSE_Stream。
2. WHEN Event_Bus 发射一条 Progress_Event，THE Backend SHALL 通过对应 Analysis_Session 的 SSE_Stream 按发射顺序、在 1 秒内将该 Progress_Event 以事件的形式推送给 Frontend。
3. WHILE 一个 Agent 正在产生推理文本，THE Backend SHALL 在每产生一个增量片段时通过 SSE_Stream 在 1 秒内推送一条 thought 类型的 Progress_Event。
4. WHEN Frontend 接收到 thought 类型的 Progress_Event，THE Frontend SHALL 按接收顺序将增量片段逐段追加渲染到对应 Agent 的展示区域。
5. WHEN Frontend 接收到 tool_call 或 tool_result 类型的 Progress_Event，THE Frontend SHALL 在对应 Agent 的展示区域显示工具名称与结果摘要，且结果摘要超过 500 字符时截断显示。
6. WHEN Final_Judge 提交结论，THE Backend SHALL 通过 SSE_Stream 推送一条 final_report 类型的 Progress_Event，其数据为完整的 Health_Report。
7. IF Agent_Pipeline 在执行过程中发生未捕获异常，THEN THE Backend SHALL 通过 SSE_Stream 推送一条包含失败原因描述的 error 类型 Progress_Event 并关闭该 SSE_Stream。
8. WHEN Health_Report 已推送或 error 事件已推送，THE Backend SHALL 关闭该 Analysis_Session 的 SSE_Stream，且关闭后不再推送任何 Progress_Event。
9. WHILE 一个 SSE_Stream 处于打开状态且连续 15 秒无 Progress_Event 推送，THE Backend SHALL 通过该 SSE_Stream 发送一条心跳事件以保活连接。

### 需求 6：结构化健康评估报告

**User Story:** 作为使用者，我希望获得一份结构清晰的健康评估报告，以便快速理解仓库的整体状况与改进方向。

#### 验收标准

1. THE Health_Report SHALL 包含 Repository_Metadata 摘要、Code_Auditor 的技术意见、Product_Value_Agent 的产品价值意见、Final_Judge 的综合优化建议与总分五个部分。
2. THE Health_Report SHALL 包含一个取值为 0 到 100（含边界）之间整数的总分字段。
3. IF Final_Judge 产出的总分缺失、非整数或超出 0 到 100 范围，THEN THE Backend SHALL 将总分钳制或修正为 0 到 100 范围内的整数后再写入 Health_Report。
4. WHEN Frontend 接收到 final_report 类型的 Progress_Event，THE Frontend SHALL 渲染 Health_Report 的全部五个部分。
5. IF Frontend 接收到的 final_report 类型 Progress_Event 缺失五个部分中的任一部分，THEN THE Frontend SHALL 为缺失部分显示占位提示，并保留已成功接收部分的展示。
6. THE Frontend SHALL 将 Repository_Metadata 摘要中的 Star 数与 Fork 数以整数展示，并将编程语言分布以「语言名称 + 占比」列表展示且各占比之和为 100%。
7. THE Backend SHALL 提供一个序列化器与一个解析器用于 Health_Report 与 JSON 文本之间的互相转换。
8. IF 解析器接收到不符合 Health_Report 结构的 JSON 文本，THEN THE Backend SHALL 返回一条指明原因的描述性解析错误，且不返回 Health_Report 对象。
9. FOR ALL 合法的 Health_Report 对象（五个部分齐全且总分为 0 到 100 范围内整数），先序列化再解析 SHALL 产生一个与原对象字段值相等的 Health_Report 对象（往返属性）。

### 需求 7：外部 LLM 服务调用

**User Story:** 作为系统，我需要通过 HTTP 调用外部 LLM 服务驱动各 Agent 的推理，以便保持后端轻量且模型可替换。

#### 验收标准

1. THE Backend SHALL 通过 HTTP 调用 OpenAI 兼容的 LLM_Provider 接口执行 Agent 推理，且单次请求的连接与响应超时上限为 60 秒。
2. WHEN Backend 启动时，THE Backend SHALL 从环境配置读取 LLM_Provider 的基础地址、API 密钥与模型名称。
3. IF Backend 启动时 LLM_Provider 的基础地址、API 密钥或模型名称中任一项缺失或为空，THEN THE Backend SHALL 中止 Agent 推理相关功能的初始化并记录一条指示缺失配置项的错误。
4. WHILE LLM_Provider 支持流式响应，THE Backend SHALL 以流式方式接收推理输出并逐片段发射 thought 类型的 Progress_Event。
5. IF 对 LLM_Provider 的一次请求返回瞬态错误（HTTP 429、500、502、503 或 504），THEN THE Backend SHALL 以指数退避策略重试，初始退避间隔为 1 秒且每次重试后翻倍，最多重试 2 次。
6. IF 对 LLM_Provider 的一次请求返回非瞬态错误（HTTP 400、401、403 或 404），THEN THE Backend SHALL 立即停止该请求且不进行任何重试。
7. IF 对 LLM_Provider 的请求在重试耗尽后仍失败，THEN THE Backend SHALL 通过 SSE_Stream 发射一条 error 类型的 Progress_Event，且该事件包含指示失败原因的错误描述。

### 需求 8：前端交互体验与视觉风格

**User Story:** 作为使用者，我希望前端交互流畅、视觉风格与 artoo 一致，以便获得连贯、专业的使用体验。

#### 验收标准

1. THE Frontend SHALL 使用 React 18、TypeScript、Tailwind CSS v4 与 Vite 构建。
2. THE Frontend SHALL 沿用 artoo 的视觉风格，其配色、排版与组件样式与 artoo 的对应界面元素保持一致（同类元素的颜色、字体、字号、间距与形状可逐项比对且无差异）。
3. WHILE 一个 Analysis_Session 正在分析中，THE Frontend SHALL 每隔不超过 2 秒刷新并显示每个 Agent 的实时执行状态（等待中、执行中、已完成、失败之一）。
4. WHEN 一个 Agent 完成结论提交，THE Frontend SHALL 在 2 秒内更新该 Agent 的状态为已完成。
5. IF SSE_Stream 在 10 秒内未收到任何数据或事件，THEN THE Frontend SHALL 判定连接中断，并显示连接中断提示，同时提供重新发起分析的入口。
6. WHEN 判定 SSE_Stream 连接中断，THE Frontend SHALL 自动尝试重新连接，最多重试 3 次、每次间隔 3 秒；WHERE 3 次重试均失败，THE Frontend SHALL 保留连接中断提示与重新发起分析的入口。

### 需求 9：系统工程结构与运行说明

**User Story:** 作为开发者，我希望项目具备清晰的分层架构与本地运行说明，以便快速理解、运行与评审。

#### 验收标准

1. THE Reviewer_System SHALL 存放于工作区根目录下名为 `reviewer` 的独立目录。
2. THE Reviewer_System SHALL 采用前后端分离的分层结构，其中 Frontend 与 Backend 分属各自独立的子目录，且 Backend 至少划分为 API 层、Agent 流水线层、GitHub 客户端层与模型调用层四个相互隔离的模块。
3. THE Reviewer_System SHALL 在其目录内提供一份说明文档，该文档 SHALL 依次包含前端本地运行步骤、后端本地运行步骤，以及所需环境变量清单，且该清单 SHALL 至少列出 LLM_Provider 基础地址、LLM_Provider API 密钥、LLM_Provider 模型名称与 GitHub 访问令牌四项及各自用途。
4. IF Backend 启动时缺少 LLM_Provider 基础地址、LLM_Provider API 密钥或 LLM_Provider 模型名称中的任一必需环境变量，THEN THE Backend SHALL 逐项输出指明每个缺失环境变量名称的描述性提示，并终止启动而不进入服务监听状态。
5. WHERE GitHub 访问令牌等可选环境变量未配置，THE Backend SHALL 输出一条指明该可选项缺失及其影响的描述性提示，并继续完成启动。

### 需求 10：前后端测试

**User Story:** 作为开发者，我希望前后端具备完整且健壮的测试，以便保障系统质量与可维护性。

#### 验收标准

1. WHEN 执行 Repository_URL 校验与解析的单元测试套件，THE Backend SHALL 至少覆盖以下合法输入（带 .git 后缀的 HTTPS 地址、不带 .git 后缀的 HTTPS 地址、SSH 格式地址）与以下非法输入（空字符串、缺少主机名的地址、非 git 协议地址、超过 2048 个字符的地址），且所有断言全部通过时判定为成功。
2. WHEN 执行 Repository_Snapshot 序列化/解析的基于属性的测试（property-based test），THE Backend SHALL 对随机生成的合法 Repository_Snapshot 运行至少 100 个样例，并验证「序列化后再解析所得对象与原始对象相等」的往返属性在全部样例上成立。
3. WHEN 执行 Health_Report 序列化/解析的基于属性的测试（property-based test），THE Backend SHALL 对随机生成的合法 Health_Report 运行至少 100 个样例，并验证「序列化后再解析所得对象与原始对象相等」的往返属性在全部样例上成立。
4. WHEN 执行 Agent_Pipeline 编排逻辑的测试，THE Backend SHALL 使用 LLM_Provider 与 GitHub_Client 的模拟替身，并断言测试过程中未发起任何真实外部网络调用，同时验证编排步骤按预期顺序被调用。
5. WHEN 执行 Final_Judge 分数钳制逻辑的测试，THE Backend SHALL 覆盖低于 0 的输入（如 -1）、等于边界值 0、处于 0 到 100 区间内的输入（如 50）、等于边界值 100 以及高于 100 的输入（如 101），并断言输出分数被钳制在 0 到 100（含两端）区间内。
6. WHEN 执行 Repository_URL 输入校验的前端单元测试，THE Frontend SHALL 至少覆盖合法输入（符合 Repository_URL 格式的地址）与非法输入（空输入、格式错误的地址），并断言校验结果与预期一致。
7. WHEN 执行 SSE Progress_Event 渲染逻辑的测试，THE Frontend SHALL 分别覆盖 thought、tool_call、final_report 与 error 四种事件类型，并断言每种事件类型均渲染出与其类型对应的预期界面元素。
8. IF 任一测试用例断言失败或抛出未捕获异常，THEN THE 测试运行器 SHALL 将该测试判定为失败并输出指示失败原因与所在用例的错误信息。
