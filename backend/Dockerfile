# Reviewer 后端镜像（API 进程与 Worker 进程共用）。
#
# 同一镜像通过不同启动命令区分角色（设计里的进程分离）：
#   - API 进程   ：uvicorn app.main:app
#   - Worker 进程：python -m app.worker_main
# 具体命令由 docker-compose 的各服务 command 指定，可水平扩展多个 Worker 副本。

FROM python:3.11-slim

# 不生成 .pyc、日志实时刷新，便于容器内观测
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖，利用层缓存（requirements 不变时跳过重装）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再拷贝应用代码
COPY app ./app

# API 进程默认端口（Worker 进程不监听端口，此暴露仅对 API 生效）
EXPOSE 8100

# 默认以 API 进程启动；Worker 服务在 compose 中用 command 覆盖为
# ["python", "-m", "app.worker_main"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
