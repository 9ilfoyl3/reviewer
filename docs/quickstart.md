# 开发态启动文档

本文档说明如何在 **macOS** 与 **Windows** 上启动 Reviewer 的开发环境。
两个平台的架构完全一致，都需要同时运行四个部分：

| 组成 | 说明 | 默认端口 |
| --- | --- | --- |
| 基础设施 | Redis（队列 + Pub/Sub + 会话状态）+ PostgreSQL（历史 + 模型配置） | 6379 / 5432 |
| API 进程 | `uvicorn app.main:app`，校验 / 入队 / SSE 转发 | 8100 |
| Worker 进程 | `python -m app.worker_main`，抓取 + 多 Agent 流水线，可多开 | — |
| 前端 dev server | `vite`，`/api` 反代到 `:8100` | 3100 |

差异仅在于：**macOS 用 Makefile 一键驱动**，Windows 没有 `make`，用等价的原生命令。

## 前置依赖（两平台通用）

- Python 3.11+
- Node.js 18+ 与 npm
- Docker Desktop（用于起 Redis + PostgreSQL；Windows 建议启用 WSL2 后端）
- 一个 OpenAI 兼容的 LLM 服务（提供 `/v1/chat/completions`）

首次启动前，先准备好 LLM 配置（三项必需，缺失会让后端 fail-fast 退出）：

- `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`

完整环境变量清单见 [`../README.md`](../README.md#环境变量清单) 与 [`../backend/.env.example`](../backend/.env.example)。

---

## macOS（使用 Makefile）

在 `reviewer/` 目录下操作，`make help` 可查看全部命令。

### 一键启动（推荐）

```bash
make install                             # 安装前后端依赖（后端建 backend/.venv，前端 npm install）
cp backend/.env.example backend/.env     # 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
make dev                                 # 起 Redis + PostgreSQL + API + Worker + 前端，Ctrl-C 全部停止
```

`make dev` 会先拉起基础设施，再在同一终端并行运行 API（:8100）、Worker、前端（:3100），
日志汇聚到当前终端，按 Ctrl-C 时统一停止全部进程。启动后访问 http://localhost:3100 。

### 分终端控制（便于分别看日志 / 多开 Worker）

```bash
make dev-infra       # docker 起 Redis + PostgreSQL
make dev-api         # 终端 1：API 进程（:8100，热重载）
make dev-worker      # 终端 2：Worker 进程（重复执行可水平扩展）
make dev-frontend    # 终端 3：前端 dev server（:3100）
```

### 测试与清理

```bash
make test            # 前后端全部测试
make test-backend    # 仅后端 pytest
make test-frontend   # 仅前端 vitest
make dev-infra-down  # 停止基础设施
make clean           # 停服并清理卷、.venv 与前端依赖
```

---

## Windows（PowerShell 原生命令）

Windows 无 `make`，逐步执行以下命令。以下均在 PowerShell 中、`reviewer\` 目录下操作。

### 1. 启动基础设施（Redis + PostgreSQL）

```powershell
docker compose -f docker-compose.dev.yml up -d
# Redis: localhost:6379    PostgreSQL: localhost:5432
```

### 2. 安装后端依赖并配置

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
```

> 若 PowerShell 提示脚本被禁止运行（无法执行 `Activate.ps1`），当前会话放开一次即可：
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

### 3. 启动 API 进程（终端 1）

在 `backend` 目录、已激活 `.venv` 的窗口中：

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

### 4. 启动 Worker 进程（终端 2）

新开一个 PowerShell，进入 `backend` 并激活同一虚拟环境后：

```powershell
cd backend
.venv\Scripts\Activate.ps1
python -m app.worker_main
```

> Worker 可水平扩展：再开窗口重复本命令即可，同一 Redis Consumer Group 会自动分配任务。

### 5. 启动前端 dev server（终端 3）

```powershell
cd frontend
npm install          # 首次运行需要
npm run dev
```

打开 http://localhost:3100 ，输入公开 GitHub 仓库 URL 即可开始评估。

### 6. 测试（可选）

```powershell
# 后端（backend 目录，已激活 .venv）
pytest -q
# 前端（frontend 目录）
npm run test
```

### 停止

- 前后端进程：各窗口按 Ctrl-C。
- 基础设施：`docker compose -f docker-compose.dev.yml down`

---

## 端口 / 反代覆盖（两平台通用）

需要避免端口冲突时，可用环境变量覆盖默认值：

| 变量 | 用途 | 默认 |
| --- | --- | --- |
| `FRONTEND_PORT` | 前端 dev server 监听端口 | 3100 |
| `BACKEND_PROXY_TARGET` | 前端 `/api` 反代的后端地址 | `http://localhost:8100` |

设置方式：

```bash
# macOS
FRONTEND_PORT=3200 BACKEND_PROXY_TARGET=http://localhost:8200 npm run dev
```

```powershell
# Windows PowerShell（仅对当前会话生效）
$env:FRONTEND_PORT = "3200"
$env:BACKEND_PROXY_TARGET = "http://localhost:8200"
npm run dev
```

API 进程端口通过 `uvicorn ... --port <端口>` 指定。

---

## 常见问题

- **后端启动即退出并打印缺失变量**：`LLM_BASE_URL / LLM_API_KEY / LLM_MODEL` 三项必须填写，
  这是 fail-fast 行为，补齐 `backend/.env` 后重启即可。
- **前端能打开但评估无响应 / SSE 无数据**：确认 API（:8100）与 **至少一个 Worker** 都在运行，
  且基础设施 Redis 可连通。
- **GitHub 抓取偶发限流**：匿名访问额度较低，配置 `GITHUB_TOKEN` 可显著提升速率上限。
- **Windows 下 `docker compose` 无法连接**：确认 Docker Desktop 已启动，并已启用 WSL2 集成。
