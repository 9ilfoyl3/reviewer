"""GitHub 客户端：抓取仓库数据并归一化为 Repository_Snapshot。

对应需求 2（GitHub 仓库数据抓取）中本任务（3.1）覆盖的部分：

- 抓取元数据 stars/forks/open_issues/languages/pushed_at，时间转 ISO 8601 UTC（需求 2.1）。
- 抓取默认分支 README；404 无 README 时置空字符串继续，不中止（需求 2.2、2.9）。
- 递归抓取目录树，深度上限 10 层、条目上限 10000，超限截断并标记 ``tree_truncated=True``（需求 2.3）。
- 环境提供 GITHUB_TOKEN 时请求头带 ``Authorization: Bearer <token>``（需求 2.6）。
- 归一化为 ``RepositorySnapshot``（需求 2.8）。

单请求超时、重试与 404/403 速率限制等错误降级（任务 3.2）集中在
:meth:`GitHubClient._request` 内实现（需求 2.4、2.5、2.7、2.10）：

- 单请求超时 15s；超时后最多重试 2 次、每次间隔 ≥1s；3 次尝试（1 次初始
  加 2 次重试）仍超时抛 :class:`GitHubTimeoutError`，不生成 Snapshot。
- 404 仓库不存在 / 非公开抛 :class:`GitHubNotFoundError`，不生成 Snapshot。
- 403 且 ``X-RateLimit-Remaining: 0`` 抛 :class:`GitHubRateLimitError`，
  错误含 ``X-RateLimit-Reset`` 转 ISO 8601 UTC 的重置时间。
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone

import httpx

from ..config import Settings, get_settings
from ..models.snapshot import (
    RepositoryMetadata,
    RepositorySnapshot,
    TreeEntry,
)
from .errors import (
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubTimeoutError,
)

# GitHub REST API 基础地址与推荐请求头版本。
GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"

# 目录树上限（需求 2.3）。
MAX_TREE_DEPTH = 10  # 遍历深度上限（层）
MAX_TREE_ENTRIES = 10000  # 文件 / 目录条目上限

# 超时与重试（需求 2.7、2.10）。
REQUEST_TIMEOUT_SECONDS = 15.0  # 单个请求超时上限
MAX_RETRIES = 2  # 超时后最多重试次数（合计 3 次尝试）
RETRY_INTERVAL_SECONDS = 1.0  # 每次重试的最小间隔


def _to_iso8601_utc(raw: str | None) -> str:
    """将 GitHub 返回的时间戳归一化为 ISO 8601 UTC 文本（需求 2.1）。

    GitHub 的时间形如 ``2023-01-01T00:00:00Z``。这里统一解析为带 UTC 时区的
    ``datetime`` 后再格式化，保证输出恒为 UTC。空值返回空字符串。
    """
    if not raw:
        return ""
    # 兼容以 Z 结尾的 UTC 记法。
    normalized = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        # 无法解析时原样返回，交由上层判断（不在 3.1 做错误降级）。
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _reset_to_iso8601_utc(raw: str | None) -> str:
    """将 ``X-RateLimit-Reset``（Unix 纪元秒）转为 ISO 8601 UTC 文本（需求 2.5）。

    GitHub 以 UTC 纪元秒返回额度重置时间。无法解析时返回空字符串。
    """
    if not raw:
        return ""
    try:
        epoch = int(raw)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _tree_depth(path: str) -> int:
    """由路径计算目录层级：``a`` → 1，``a/b`` → 2，以此类推。"""
    if not path:
        return 0
    return path.count("/") + 1


class GitHubClient:
    """基于 ``httpx.AsyncClient`` 的 GitHub 数据抓取与归一化客户端。

    通过异步上下文管理器使用，复用底层连接池::

        async with GitHubClient() as client:
            snapshot = await client.fetch_snapshot("owner", "repo")
    """

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        # 允许注入 AsyncClient 便于测试；否则自行创建并负责关闭。
        self._client = client
        self._owns_client = client is None

    # ---- 生命周期 ----
    async def __aenter__(self) -> "GitHubClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=GITHUB_API_BASE, headers=self._build_headers()
            )
            self._owns_client = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_headers(self) -> dict[str, str]:
        """构造请求头；配置了 GITHUB_TOKEN 时携带鉴权（需求 2.6）。"""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        token = (self._settings.github_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _request(
        self,
        url: str,
        *,
        params: dict | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response:
        """执行单个 GitHub API GET 请求，是所有抓取的统一入口。

        在此集中实现超时、重试与错误降级（需求 2.4、2.5、2.7、2.10）：

        - 单请求超时 15s；超时后最多重试 2 次、每次间隔 ≥1s；3 次尝试仍超时
          抛 :class:`GitHubTimeoutError`（需求 2.7、2.10）。
        - 404 → 抛 :class:`GitHubNotFoundError`（需求 2.4）；当
          ``allow_not_found=True`` 时改为返回响应交由调用方处理（README 无
          文件属于正常降级，需求 2.9）。
        - 403 且 ``X-RateLimit-Remaining: 0`` → 抛 :class:`GitHubRateLimitError`，
          含重置时间（需求 2.5）。

        Args:
            allow_not_found: 为 True 时不对 404 抛错，而是返回响应，供
                :meth:`_get_readme` 区分"仓库无 README"这一正常降级场景。
        """
        assert self._client is not None, "GitHubClient 需在 async with 上下文中使用"

        # 超时重试循环：合计最多 1 + MAX_RETRIES 次尝试（需求 2.7、2.10）。
        last_timeout: httpx.TimeoutException | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                # 确保鉴权头存在（当外部注入 client 未带 header 时补齐）。
                resp = await self._client.get(
                    url,
                    params=params,
                    headers=self._build_headers(),
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except httpx.TimeoutException as exc:
                last_timeout = exc
                # 仍有剩余重试次数时，间隔 ≥1s 后重试（需求 2.7）。
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_INTERVAL_SECONDS)
                    continue
                # 3 次尝试仍超时：终止抓取、不生成 Snapshot（需求 2.10）。
                raise GitHubTimeoutError(
                    f"GitHub API 请求超时：{url} 在 {MAX_RETRIES + 1} 次尝试"
                    f"（每次 {REQUEST_TIMEOUT_SECONDS:g}s）后仍未成功返回。"
                ) from last_timeout

            # 请求成功返回，进行错误分流（需求 2.4、2.5）。
            self._raise_for_error(resp, allow_not_found=allow_not_found)
            return resp

        # 理论不可达：循环内要么 return 要么 raise。
        raise GitHubTimeoutError(f"GitHub API 请求超时：{url}")

    @staticmethod
    def _raise_for_error(resp: httpx.Response, *, allow_not_found: bool) -> None:
        """对 GitHub 响应做错误分流（需求 2.4、2.5）。

        - 403 且 ``X-RateLimit-Remaining: 0`` → 速率限制错误，含 ISO 8601 UTC
          重置时间（需求 2.5）。
        - 404 且非 ``allow_not_found`` → 资源不存在错误（需求 2.4）。
        - 其余状态码（含 README 的 404 降级）不在此抛错，交由调用方处理。
        """
        # 速率限制优先判断：403 且剩余额度为 0（需求 2.5）。
        if resp.status_code == httpx.codes.FORBIDDEN:
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining is not None and remaining.strip() == "0":
                reset_at = _reset_to_iso8601_utc(resp.headers.get("X-RateLimit-Reset"))
                message = "GitHub API 速率限制已耗尽（剩余额度为 0）"
                if reset_at:
                    message += f"，额度将于 {reset_at}（UTC）重置"
                raise GitHubRateLimitError(message + "。", reset_at=reset_at)

        # 仓库不存在 / 非公开（需求 2.4）。
        if resp.status_code == httpx.codes.NOT_FOUND and not allow_not_found:
            raise GitHubNotFoundError(
                "目标仓库不存在或非公开（HTTP 404），已中止抓取、不生成 Repository_Snapshot。"
            )

    # ---- 元数据（需求 2.1） ----
    async def _get_metadata(self, owner: str, repo: str) -> RepositoryMetadata:
        """抓取仓库元数据并归一化时间为 ISO 8601 UTC。"""
        repo_resp = await self._request(f"/repos/{owner}/{repo}")
        repo_data = repo_resp.json()

        lang_resp = await self._request(f"/repos/{owner}/{repo}/languages")
        languages: dict[str, int] = lang_resp.json()

        return RepositoryMetadata(
            owner=owner,
            repo=repo,
            stars=int(repo_data.get("stargazers_count", 0)),
            forks=int(repo_data.get("forks_count", 0)),
            open_issues=int(repo_data.get("open_issues_count", 0)),
            languages={str(k): int(v) for k, v in languages.items()},
            last_commit_at=_to_iso8601_utc(repo_data.get("pushed_at")),
            default_branch=str(repo_data.get("default_branch") or "main"),
        )

    # ---- README（需求 2.2、2.9） ----
    async def _get_readme(self, owner: str, repo: str) -> str:
        """抓取默认分支 README 文本；404 无 README 时返回空字符串（需求 2.9）。"""
        # README 的 404 是"仓库无 README"的正常降级，不应触发资源不存在错误，
        # 故以 allow_not_found=True 拿到响应后自行判空（需求 2.9）。
        resp = await self._request(
            f"/repos/{owner}/{repo}/readme", allow_not_found=True
        )
        # 无 README 时不中止，置空字符串继续（需求 2.9）。
        if resp.status_code == httpx.codes.NOT_FOUND:
            return ""
        data = resp.json()
        content = data.get("content", "")
        encoding = data.get("encoding", "base64")
        if encoding == "base64":
            # GitHub 的 base64 内容按行折叠，需去除换行后解码。
            raw = base64.b64decode(content.replace("\n", "")) if content else b""
            return raw.decode("utf-8", errors="replace")
        return str(content)

    # ---- 目录树（需求 2.3） ----
    async def _get_tree(
        self, owner: str, repo: str, branch: str
    ) -> tuple[list[TreeEntry], bool]:
        """递归抓取目录树并按深度 / 条目上限截断。

        返回 ``(entries, truncated)``：当因深度超限、条目超限或 GitHub 自身
        标记 ``truncated`` 而丢弃任何条目时，``truncated`` 为 True（需求 2.3）。
        """
        resp = await self._request(
            f"/repos/{owner}/{repo}/git/trees/{branch}", params={"recursive": "1"}
        )
        data = resp.json()
        raw_entries = data.get("tree", []) or []
        # GitHub 在树过大时自身会返回 truncated=True。
        truncated = bool(data.get("truncated", False))

        entries: list[TreeEntry] = []
        for item in raw_entries:
            path = str(item.get("path", ""))
            depth = _tree_depth(path)
            # 深度超限：丢弃并标记截断（需求 2.3）。
            if depth > MAX_TREE_DEPTH:
                truncated = True
                continue
            # 条目超限：停止收集并标记截断（需求 2.3）。
            if len(entries) >= MAX_TREE_ENTRIES:
                truncated = True
                break
            # GitHub 树类型：blob → file，tree → dir。
            entry_type = "dir" if item.get("type") == "tree" else "file"
            entries.append(TreeEntry(path=path, type=entry_type, depth=depth))

        return entries, truncated

    # ---- 归一化入口（需求 2.8） ----
    async def fetch_snapshot(self, owner: str, repo: str) -> RepositorySnapshot:
        """抓取仓库全部数据并归一化为 Repository_Snapshot（需求 2.8）。"""
        metadata = await self._get_metadata(owner, repo)
        readme = await self._get_readme(owner, repo)
        tree, tree_truncated = await self._get_tree(
            owner, repo, metadata.default_branch
        )

        return RepositorySnapshot(
            metadata=metadata,
            readme=readme,
            tree=tree,
            tree_truncated=tree_truncated,
            representative_files={},
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
