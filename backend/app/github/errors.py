"""GitHub 客户端抓取错误（需求 2.4、2.5、2.7、2.10）。

任务 3.2 的错误降级把 GitHub API 的异常情况收敛为语义化异常：抓取失败时
抛出这些异常并**不生成 Repository_Snapshot**，交由 Worker 层转为 error 事件。

- :class:`GitHubNotFoundError`  ：仓库不存在 / 非公开（HTTP 404，需求 2.4）。
- :class:`GitHubRateLimitError` ：速率限制（HTTP 403 且剩余额度为 0，需求 2.5）。
- :class:`GitHubTimeoutError`   ：3 次尝试仍超时（需求 2.7、2.10）。
"""

from __future__ import annotations


class GitHubClientError(Exception):
    """GitHub 抓取失败的基类错误。"""


class GitHubNotFoundError(GitHubClientError):
    """目标仓库不存在或非公开（HTTP 404）。

    对应需求 2.4：中止抓取并返回资源不存在错误，且不生成 Repository_Snapshot。
    """


class GitHubRateLimitError(GitHubClientError):
    """GitHub API 速率限制（HTTP 403 且 ``X-RateLimit-Remaining: 0``）。

    对应需求 2.5：终止抓取并返回速率限制错误，错误信息含以 ISO 8601 UTC
    表示的额度重置时间。

    Attributes:
        reset_at: 由 ``X-RateLimit-Reset`` 转换得到的 ISO 8601 UTC 重置时间；
            无法解析时为空字符串。
    """

    def __init__(self, message: str, reset_at: str = "") -> None:
        super().__init__(message)
        self.reset_at = reset_at


class GitHubTimeoutError(GitHubClientError):
    """单个 GitHub API 请求在 3 次尝试后仍超时（需求 2.7、2.10）。

    对应需求 2.10：1 次初始请求加 2 次重试后仍未成功返回，终止抓取并返回
    超时错误，且不生成 Repository_Snapshot。
    """
