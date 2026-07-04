"""GitHub 客户端层。

基于 httpx.AsyncClient 抓取仓库元数据 / README / 目录树，
并归一化为 Repository_Snapshot，与其它层相互隔离。
"""

from .client import GitHubClient
from .errors import (
    GitHubClientError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubTimeoutError,
)

__all__ = [
    "GitHubClient",
    "GitHubClientError",
    "GitHubNotFoundError",
    "GitHubRateLimitError",
    "GitHubTimeoutError",
]
