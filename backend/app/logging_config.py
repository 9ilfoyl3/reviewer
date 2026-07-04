"""日志配置模块

为 Reviewer 后端提供统一的日志初始化。API 进程与 Worker 进程各自以不同
`service_name` 调用 `setup_logging`，输出到控制台（便于 docker logs / 本地观察）。

设计原则：保持简单、数据流向清晰，不引入按文件切分等重型机制；如需落盘，
可通过 LOG_DIR 环境变量启用文件输出。
"""

import logging
import os
import sys
from pathlib import Path

# 默认日志格式：时间 + 级别 + logger 名 + 进程 + 消息
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s (pid=%(process)d): %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 降低第三方库噪声日志级别
_NOISY_LOGGERS = ("httpx", "httpcore", "uvicorn.access", "asyncio")


def setup_logging(service_name: str = "reviewer", level: int = logging.INFO) -> None:
    """配置根日志器。

    Args:
        service_name: 服务标识（如 "api" / "worker"），用于文件日志子目录名。
        level: 日志级别，默认 INFO。可通过 LOG_LEVEL 环境变量覆盖。
    """
    # 环境变量覆盖日志级别（如 LOG_LEVEL=DEBUG）
    env_level = os.environ.get("LOG_LEVEL", "").upper()
    if env_level and hasattr(logging, env_level):
        level = getattr(logging, env_level)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # 清除已有 handler，避免重复初始化时日志重复输出
    root_logger.handlers.clear()

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)

    # 可选文件输出：设置 LOG_DIR 时启用，落盘到 {LOG_DIR}/{service_name}.log
    log_dir = os.environ.get("LOG_DIR", "")
    if log_dir:
        dir_path = Path(log_dir)
        dir_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            dir_path / f"{service_name}.log", encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)

    # 降低第三方库日志级别
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("日志已配置: service=%s, level=%s", service_name, logging.getLevelName(level))
