.PHONY: help install install-backend install-frontend \
	dev dev-infra dev-infra-down dev-api dev-worker dev-frontend \
	test test-backend test-frontend \
	up up-build down logs ps clean

# Python 解释器（可通过 make install-backend PYTHON=python3.12 覆盖）
PYTHON ?= python3
# 后端虚拟环境目录
VENV := backend/.venv
# compose 文件
COMPOSE := docker-compose.yml
COMPOSE_DEV := docker-compose.dev.yml

# 默认目标：打印帮助
help:
	@echo "Reviewer —— 多 Agent 仓库体检工具"
	@echo ""
	@echo "安装依赖："
	@echo "  make install            安装前后端依赖（后端建 .venv + pip，前端 npm）"
	@echo "  make install-backend    仅安装后端依赖"
	@echo "  make install-frontend   仅安装前端依赖"
	@echo ""
	@echo "本地开发（Redis + PostgreSQL 用 docker，前后端跑在宿主机热重载）："
	@echo "  make dev                一键启动 Redis + PostgreSQL + API + Worker + 前端（Ctrl-C 全部停止）"
	@echo "  make dev-infra          仅启动基础设施 Redis + PostgreSQL（docker）"
	@echo "  make dev-infra-down     停止基础设施 Redis + PostgreSQL"
	@echo "  make dev-api            单独启动 API 进程（uvicorn 热重载，:8100）"
	@echo "  make dev-worker         单独启动 Worker 进程（可多开水平扩展）"
	@echo "  make dev-frontend       单独启动前端 dev server（vite，:3100）"
	@echo ""
	@echo "测试："
	@echo "  make test               运行前后端全部测试"
	@echo "  make test-backend       后端 pytest"
	@echo "  make test-frontend      前端 vitest"
	@echo ""
	@echo "部署（docker compose 一体化：redis+postgres+api+worker+frontend）："
	@echo "  make up                 后台启动全部服务"
	@echo "  make up-build           重新构建镜像并后台启动"
	@echo "  make down               停止并移除全部服务"
	@echo "  make logs               跟随查看全部服务日志"
	@echo "  make ps                 查看服务状态"
	@echo "  make clean              停服并清理卷、本地 .venv 与前端依赖"

# ============================================================
# 安装依赖
# ============================================================
install: install-backend install-frontend

install-backend:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r backend/requirements.txt
	@echo "后端依赖安装完成。若尚未配置，请 cp backend/.env.example backend/.env 并填写 LLM_* 三项。"

install-frontend:
	cd frontend && npm install

# ============================================================
# 本地开发（Redis 用 docker，应用跑在宿主机热重载）
# ============================================================
# 一键启动：先确保 Redis + PostgreSQL 就绪，再并行拉起 API + Worker + 前端三个进程。
# 三者在同一终端后台运行、日志汇聚到当前终端；按 Ctrl-C（或任一进程退出）时，
# 通过 trap 统一终止整个进程组，避免残留孤儿进程。
dev: dev-infra
	@echo "启动 API + Worker + 前端（Ctrl-C 全部停止）..."
	@set -m; \
	trap 'echo; echo "正在停止全部开发进程..."; kill 0' INT TERM EXIT; \
	( cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload ) & \
	( cd backend && .venv/bin/python -m app.worker_main ) & \
	( cd frontend && npm run dev ) & \
	wait

# 仅启动基础设施 Redis（队列 + Pub/Sub + 会话状态）+ PostgreSQL（历史 + 模型配置）
dev-infra:
	@echo "启动基础设施 Redis + PostgreSQL（docker）..."
	docker compose -f $(COMPOSE_DEV) up -d
	@echo "Redis: localhost:6379    PostgreSQL: localhost:5432"

dev-infra-down:
	docker compose -f $(COMPOSE_DEV) down

# API 进程：校验/入队/SSE 订阅转发（不执行抓取或推理）。依赖 dev-infra 的 Redis。
dev-api:
	cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload

# Worker 进程：消费任务、执行 GitHub 抓取 + 多 Agent 流水线。可在多个终端重复以水平扩展。
dev-worker:
	cd backend && .venv/bin/python -m app.worker_main

# 前端 dev server：默认 :3100，/api 反代到 http://localhost:8100
dev-frontend:
	cd frontend && npm run dev

# ============================================================
# 测试
# ============================================================
test: test-backend test-frontend

test-backend:
	cd backend && .venv/bin/pytest -q

test-frontend:
	cd frontend && npm run test

# ============================================================
# 部署（docker compose 一体化）
# ============================================================
up:
	docker compose -f $(COMPOSE) up -d

up-build:
	docker compose -f $(COMPOSE) up -d --build

down:
	docker compose -f $(COMPOSE) down

logs:
	docker compose -f $(COMPOSE) logs -f

ps:
	docker compose -f $(COMPOSE) ps

# ============================================================
# 清理
# ============================================================
clean:
	-docker compose -f $(COMPOSE) down -v
	-docker compose -f $(COMPOSE_DEV) down -v
	rm -rf $(VENV) frontend/node_modules frontend/dist
	@echo "已清理 compose 卷、后端 .venv 与前端依赖/产物。"
