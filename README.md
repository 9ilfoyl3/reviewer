# Reviewer — 多 Agent 协作的 GitHub 仓库评估工具

Reviewer 接收一个公开 GitHub 仓库 URL，抓取仓库元数据 / README / 代码结构，交由一条具备 ReAct（Think → Act → Observe + 工具调用）特征的多 Agent 流水线（Code_Auditor 代码审计、Product_Value_Agent 产品价值、Final_Judge 总分裁判）协作分析，最终产出一份 0–100 分的结构化健康评估报告，全过程通过 SSE 实时流式推送到前端。

## 架构与目录

前后端分离：

```
reviewer/
├── backend/    # FastAPI + Uvicorn（API 进程）+ 独立 Worker 进程；httpx + redis + Pydantic v2
└── frontend/   # React 18 + TypeScript + Tailwind CSS v4 + Vite + shadcn/ui
```

后端进程职责分离：

- **API 进程**（`app.main:app`）：校验 URL、解析 owner/repo、创建会话状态、任务入队，并为 SSE 连接订阅 Redis Pub/Sub 并转发事件。**不执行**任何 GitHub 抓取或 Agent 推理。
- **Worker 进程**（`app.worker_main`）：从 Redis Stream 消费任务，执行 GitHub 抓取 + 多 Agent 流水线，把进度事件发布到 Redis Pub/Sub，并更新会话状态。无状态，可水平扩展多副本。

两者仅通过 **Redis**（任务队列 Stream + 事件总线 Pub/Sub + 会话状态 Hash）交互运行态数据，因此运行本系统需要一个可用的 Redis 实例。

需长期留存的数据（**评估历史** `review_records` 与 **模型配置** `model_configs`）持久化到 **PostgreSQL**（SQLAlchemy async + asyncpg）。表结构在 API / Worker 进程启动时自动创建，无需手动迁移。运行态（Redis）与持久态（PostgreSQL）职责分离、数据流向清晰。

## 前置依赖

- Python 3.11+
- Node.js 18+ 与 npm
- Redis 6+（本地或远程均可，通过 `REDIS_URL` 指定）
- PostgreSQL 14+（评估历史与模型配置持久化，通过 `DATABASE_URL` 指定）
- 一个 OpenAI 兼容的 LLM 服务（提供 `/v1/chat/completions` 接口）
- Docker + Docker Compose（用于一键起 Redis + PostgreSQL 或整套部署，可选）

## 功能一览

- **仓库评估**：输入公开 GitHub 仓库地址，多 Agent 协作实时评估并生成健康报告。
- **评估历史（侧边栏）**：每次评估持久化到 PostgreSQL，左侧边栏按仓库分组，
  「每个仓库一段独立历史」，可点开回看任意一次的完整报告，或删除记录。
- **模型配置页**：前端可直接增删改 LLM 模型（简化版：名称 / Base URL / 模型 /
  API Key / 是否默认）并测试连通性。Worker 优先使用「默认」模型驱动推理，
  未配置时回退到环境变量中的 `LLM_*`。

## 快速上手（Makefile）

项目根目录 `reviewer/` 下提供 Makefile，`make help` 查看全部命令。两种典型用法：

**A. 本地开发（Redis 用 Docker，前后端跑在宿主机热重载）**

```bash
make install         # 安装前后端依赖（后端建 backend/.venv，前端 npm install）
cp backend/.env.example backend/.env   # 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
make dev             # 一键：起 Redis + API + Worker + 前端（Ctrl-C 全部停止）
```

`make dev` 会自动先拉起 Redis，再在同一终端并行运行 API（:8100）、Worker、前端（:3100），日志汇聚到当前终端，按 Ctrl-C 时统一停止全部进程。

若想分终端单独控制（便于看各自日志、多开 Worker），也可用分离目标：

```bash
make dev-infra       # docker 起 Redis（localhost:6379）
make dev-api         # 终端 1：API 进程（:8100，热重载）
make dev-worker      # 终端 2：Worker 进程（可多开水平扩展）
make dev-frontend    # 终端 3：前端 dev server（:3100）
```

**B. 一体化部署（Docker Compose 起 redis + api + worker + frontend）**

```bash
cp .env.example .env  # 在 reviewer/ 根目录填写 LLM_* 三项
make up-build         # 构建镜像并后台启动全部服务
# 访问 http://localhost:3100
```

测试：`make test`（前后端全部）、`make test-backend`、`make test-frontend`。

## 快速上手（Windows）

Windows 默认没有 `make`，推荐两种方式，任选其一。

**A. 一体化部署（最简单，Docker Desktop）**

安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/) 后，在 PowerShell 中于 `reviewer\` 目录执行：

```powershell
Copy-Item .env.example .env          # 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
docker compose up -d --build         # 构建镜像并后台启动 redis + postgres + api + worker + frontend
# 访问 http://localhost:3100
```

**B. 本地开发（基础设施用 Docker，前后端跑在宿主机热重载）**

```powershell
# 基础设施：Redis + PostgreSQL
docker compose -f docker-compose.dev.yml up -d

# 后端依赖与配置
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env          # 填写 LLM_* 三项

# 三个终端分别启动 API / Worker / 前端
# 终端 1（后端目录，已激活 .venv）：
.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
# 终端 2（后端目录，已激活 .venv）：
python -m app.worker_main
# 终端 3（前端目录）：
cd frontend; npm install; npm run dev
```

> 完整的分步说明、常见问题与 macOS 对照命令见 [开发态启动文档](./docs/quickstart.md)。

> 下文「后端 / 前端本地运行步骤」是不使用 Makefile 时的等价手动命令，末尾「Docker 部署」一节说明 compose 细节。

## 后端本地运行步骤

后端需要 **同时启动三个部分**：基础设施（Redis + PostgreSQL）、API 进程、Worker 进程。

1. 准备 Redis 与 PostgreSQL（推荐用 `make dev-infra` 一键起，或手动）：

   ```bash
   # 一键（docker）：起 Redis + PostgreSQL
   make dev-infra

   # 或手动分别启动
   docker run -d --name reviewer-redis -p 6379:6379 redis:7
   docker run -d --name reviewer-postgres -p 5432:5432 \
     -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=reviewer \
     postgres:16
   ```

   > 表结构（`review_records` / `model_configs`）在 API / Worker 启动时自动创建，无需手动建表。

2. 创建虚拟环境并安装依赖：

   ```bash
   cd reviewer/backend
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. 配置环境变量：复制 `backend/.env.example` 为 `backend/.env` 并填写（至少填写三项必需的 LLM 配置，见下方「环境变量清单」）：

   ```bash
   cp .env.example .env
   ```

   > 若缺少任一必需的 LLM 配置项，API / Worker 进程启动时会逐项打印缺失的环境变量名称并终止启动（fail-fast，不进入服务监听状态）。

4. 启动 **API 进程**（默认监听 8100，与前端 `/api` 反代目标一致）：

   ```bash
   # 在 reviewer/backend 目录下，虚拟环境已激活
   uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
   ```

5. **另开一个终端**，激活同一虚拟环境后启动 **Worker 进程**：

   ```bash
   cd reviewer/backend
   source .venv/bin/activate
   python -m app.worker_main
   ```

   Worker 可水平扩展：在多个终端重复本命令即可启动多个副本，同一 Consumer Group 下 Redis 会自动分配任务，无需改代码。

6. （可选）运行后端测试：

   ```bash
   pytest
   ```

## 前端本地运行步骤

1. 安装依赖：

   ```bash
   cd reviewer/frontend
   npm install
   ```

2. 启动开发服务器（默认监听 3100，`/api` 反代到 `http://localhost:8100`）：

   ```bash
   npm run dev
   ```

   端口与反代目标可通过环境变量覆盖：

   - `FRONTEND_PORT`：前端 dev server 监听端口（默认 3100）
   - `BACKEND_PROXY_TARGET`：`/api` 反代的后端地址（默认 `http://localhost:8100`）

3. 打开浏览器访问 `http://localhost:3100`，输入公开 GitHub 仓库 URL 即可开始评估。

4. （可选）构建与运行前端测试：

   ```bash
   npm run build     # 生产构建
   npm run test      # vitest 单次运行
   ```

## 环境变量清单

后端环境变量样例见 [`backend/.env.example`](./backend/.env.example)，根目录 [`.env.example`](./.env.example) 汇总同一份清单以便快速查阅。

| 变量 | 必需 | 用途 |
| --- | --- | --- |
| `LLM_BASE_URL` | 是 | LLM_Provider 基础地址（OpenAI 兼容），例如 `https://api.openai.com/v1`。驱动各 Agent 推理。缺失/为空则后端 fail-fast 拒绝启动。 |
| `LLM_API_KEY` | 是 | LLM_Provider API 密钥，用于向 LLM 服务鉴权。缺失/为空则后端 fail-fast 拒绝启动。 |
| `LLM_MODEL` | 是 | LLM_Provider 模型名称，例如 `gpt-4o-mini`，指定推理所用模型。缺失/为空则后端 fail-fast 拒绝启动。 |
| `GITHUB_TOKEN` | 否 | GitHub 访问令牌。配置后请求携带该令牌以提升 GitHub API 速率限制额度；缺失时以匿名方式访问，额度较低，可能影响大仓库抓取（后端启动时会打印提示并继续）。 |
| `REDIS_URL` | 否 | Redis 连接地址，用于任务队列（Stream）+ 事件总线（Pub/Sub）+ 会话状态（Hash）。默认 `redis://localhost:6379/0`。 |
| `DATABASE_URL` | 否 | PostgreSQL 连接串（asyncpg 驱动），用于持久化评估历史与模型配置。默认 `postgresql+asyncpg://postgres:postgres@localhost:5432/reviewer`。 |
| `REVIEW_MAX_CONCURRENT` | 否 | 单个 Worker 进程的最大并发评估数（信号量上限），避免打爆下游连接与内存。默认 `4`。 |
| `AGENT_MAX_ITERATIONS` | 否 | 单个 Agent 的 ReAct 循环最大轮数，合法范围 1–20，越界值会被钳制到边界。默认 `8`。 |

## 端口约定

| 服务 | 默认端口 | 覆盖方式 |
| --- | --- | --- |
| 前端 dev server | 3100 | `FRONTEND_PORT` |
| 后端 API 进程 | 8100 | `uvicorn ... --port` / `BACKEND_PROXY_TARGET`（前端反代） |
| Redis | 6379 | `REDIS_URL` |
| PostgreSQL | 5432 | `DATABASE_URL` |

## Docker 部署

系统提供两个 compose 文件与配套镜像：

- `docker-compose.dev.yml`：**仅**基础设施 Redis，供本地开发时前后端跑在宿主机（`make dev-infra`）。
- `docker-compose.yml`：一体化编排 **redis + api + worker + frontend**，用于整套部署。

镜像：

- `backend/Dockerfile`：后端镜像，API 进程与 Worker 进程**共用同一镜像**、以不同 `command` 区分角色（`uvicorn app.main:app` vs `python -m app.worker_main`）。
- `frontend/Dockerfile`：多阶段构建，node 产出静态资源后交由 nginx 托管；`frontend/nginx.conf` 把 `/api` 反代到 `api:8100`，并对 SSE 关闭代理缓冲（`proxy_buffering off`）、放宽读超时以保证事件逐帧实时推送。

一体化部署步骤：

```bash
cd reviewer
cp .env.example .env          # 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（缺失则 api/worker fail-fast 退出）
docker compose up -d --build  # 或 make up-build
```

- 前端：`http://localhost:3100`
- API：`http://localhost:8100`
- **水平扩展 Worker**（同一 Redis Consumer Group 自动分配任务，无需改代码）：

  ```bash
  docker compose up -d --scale worker=3
  ```

常用命令：`make up` / `make down` / `make logs` / `make ps`；`make clean` 停服并清理卷与本地依赖。

环境变量由 compose 从 `reviewer/.env` 读取并注入容器；容器内 `REDIS_URL` 固定指向服务名 `redis://redis:6379/0`（覆盖 `.env` 中的宿主机地址）。
