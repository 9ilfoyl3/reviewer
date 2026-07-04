"""GitHub 客户端单元测试（任务 3.4）。

使用 ``httpx.MockTransport`` 注入替身响应，全程不发起任何真实网络调用，覆盖：
  - 字段归一化：stars/forks/open_issues/languages/pushed_at，时间转 ISO 8601 UTC（需求 2.1）
  - 404 仓库不存在/非公开 → GitHubNotFoundError，不生成 Snapshot（需求 2.4）
  - 403 且 ``X-RateLimit-Remaining: 0`` → GitHubRateLimitError，含 ISO 8601 UTC 重置时间（需求 2.5）
  - 单请求超时 15s；超时后最多重试 2 次、每次间隔 ≥1s；3 次尝试仍超时抛
    GitHubTimeoutError（需求 2.7、2.10）
  - 无 README（404）→ 置空字符串继续、不中止（需求 2.9）

_Requirements: 2.1, 2.4, 2.5, 2.7, 2.9, 2.10_
"""

import asyncio

import httpx
import pytest

from app.config import Settings
from app.github.client import (
    MAX_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_INTERVAL_SECONDS,
    GitHubClient,
)
from app.github.errors import (
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubTimeoutError,
)


def _make_client(handler) -> GitHubClient:
    """构造一个使用 MockTransport 替身的 GitHubClient（不触网）。

    ``handler`` 接收 ``httpx.Request`` 返回 ``httpx.Response``，也可在其内抛出
    ``httpx.TimeoutException`` 以模拟连接层超时。注入的 AsyncClient 由测试自行
    管理，不需要进入 ``async with`` 上下文即可调用内部抓取方法。
    """
    transport = httpx.MockTransport(handler)
    ac = httpx.AsyncClient(transport=transport, base_url="https://api.github.com")
    # 使用空配置（无 GITHUB_TOKEN），避免依赖真实环境变量。
    return GitHubClient(settings=Settings(), client=ac)


@pytest.fixture
def no_sleep(monkeypatch):
    """拦截 ``asyncio.sleep``，记录重试间隔时长并跳过真实等待。

    返回记录间隔时长的列表，供断言重试次数与间隔。
    """
    calls: list[float] = []

    async def fake_sleep(delay):
        calls.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return calls


# ---------------------------------------------------------------------------
# 字段归一化（需求 2.1）
# ---------------------------------------------------------------------------


def test_metadata_field_normalization():
    """元数据字段正确归一化，pushed_at 转为 ISO 8601 UTC（需求 2.1）。"""
    repo_payload = {
        "stargazers_count": 1234,
        "forks_count": 56,
        "open_issues_count": 7,
        "pushed_at": "2023-06-15T08:30:00Z",
        "default_branch": "develop",
    }
    languages_payload = {"Python": 100000, "TypeScript": 25000}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/languages"):
            return httpx.Response(200, json=languages_payload)
        return httpx.Response(200, json=repo_payload)

    client = _make_client(handler)

    async def _run():
        try:
            return await client._get_metadata("owner", "repo")
        finally:
            await client._client.aclose()

    metadata = asyncio.run(_run())

    assert metadata.owner == "owner"
    assert metadata.repo == "repo"
    assert metadata.stars == 1234
    assert metadata.forks == 56
    assert metadata.open_issues == 7
    assert metadata.languages == {"Python": 100000, "TypeScript": 25000}
    assert metadata.default_branch == "develop"
    # 时间归一化为带 UTC 偏移的 ISO 8601 文本。
    assert metadata.last_commit_at == "2023-06-15T08:30:00+00:00"


def test_metadata_defaults_when_fields_absent():
    """缺失的计数字段归一化为 0，缺失默认分支回退到 main（需求 2.1）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/languages"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    client = _make_client(handler)

    async def _run():
        try:
            return await client._get_metadata("owner", "repo")
        finally:
            await client._client.aclose()

    metadata = asyncio.run(_run())

    assert metadata.stars == 0
    assert metadata.forks == 0
    assert metadata.open_issues == 0
    assert metadata.languages == {}
    assert metadata.default_branch == "main"
    assert metadata.last_commit_at == ""


# ---------------------------------------------------------------------------
# 404 仓库不存在/非公开（需求 2.4）
# ---------------------------------------------------------------------------


def test_not_found_raises_and_no_snapshot():
    """仓库 404 → 抛 GitHubNotFoundError，不生成 Snapshot（需求 2.4）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = _make_client(handler)

    async def _run():
        try:
            with pytest.raises(GitHubNotFoundError):
                await client.fetch_snapshot("owner", "missing")
        finally:
            await client._client.aclose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 403 速率限制含重置时间（需求 2.5）
# ---------------------------------------------------------------------------


def test_rate_limit_raises_with_reset_time():
    """403 且剩余额度为 0 → 抛 GitHubRateLimitError，含 ISO 8601 UTC 重置时间（需求 2.5）。"""
    # 2023-01-01T00:00:00Z 对应的 Unix 纪元秒。
    reset_epoch = 1672531200

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_epoch),
            },
        )

    client = _make_client(handler)

    async def _run():
        try:
            with pytest.raises(GitHubRateLimitError) as exc_info:
                await client.fetch_snapshot("owner", "repo")
            return exc_info.value
        finally:
            await client._client.aclose()

    error = asyncio.run(_run())

    assert error.reset_at == "2023-01-01T00:00:00+00:00"
    # 重置时间也应出现在错误信息中。
    assert "2023-01-01T00:00:00+00:00" in str(error)


def test_forbidden_with_remaining_quota_not_rate_limit():
    """403 但剩余额度非 0 → 不视为速率限制，不抛 GitHubRateLimitError（需求 2.5）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "forbidden"},
            headers={"X-RateLimit-Remaining": "12"},
        )

    client = _make_client(handler)

    async def _run():
        try:
            # 剩余额度非 0 时不触发速率限制分流，_request 正常返回响应。
            return await client._request("/repos/owner/repo")
        finally:
            await client._client.aclose()

    resp = asyncio.run(_run())
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 超时重试次数与间隔（需求 2.7、2.10）
# ---------------------------------------------------------------------------


def test_timeout_retries_then_raises(no_sleep):
    """持续超时 → 合计 3 次尝试后抛 GitHubTimeoutError，重试 2 次、每次间隔 ≥1s（需求 2.7、2.10）。"""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ConnectTimeout("connect timeout", request=request)

    client = _make_client(handler)

    async def _run():
        try:
            with pytest.raises(GitHubTimeoutError):
                await client._request("/repos/owner/repo")
        finally:
            await client._client.aclose()

    asyncio.run(_run())

    # 1 次初始请求 + 2 次重试 = 3 次尝试。
    assert attempts["count"] == MAX_RETRIES + 1 == 3
    # 两次重试间隔，每次均 ≥1s。
    assert no_sleep == [RETRY_INTERVAL_SECONDS, RETRY_INTERVAL_SECONDS]
    assert all(interval >= 1.0 for interval in no_sleep)


def test_timeout_then_success_recovers(no_sleep):
    """首次超时、重试后成功：不再继续尝试，正常返回响应（需求 2.7）。"""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ReadTimeout("read timeout", request=request)
        return httpx.Response(200, json={"ok": True})

    client = _make_client(handler)

    async def _run():
        try:
            resp = await client._request("/repos/owner/repo")
            return resp
        finally:
            await client._client.aclose()

    resp = asyncio.run(_run())

    assert resp.status_code == 200
    # 第 1 次超时 + 第 2 次成功 = 2 次尝试，1 次重试间隔。
    assert attempts["count"] == 2
    assert no_sleep == [RETRY_INTERVAL_SECONDS]


def test_request_timeout_is_15s():
    """单请求超时上限为 15s（需求 2.7）。"""
    assert REQUEST_TIMEOUT_SECONDS == 15.0


# ---------------------------------------------------------------------------
# 无 README 置空继续（需求 2.9）
# ---------------------------------------------------------------------------


def test_missing_readme_returns_empty_string():
    """README 404 → 返回空字符串、不中止（需求 2.9）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = _make_client(handler)

    async def _run():
        try:
            return await client._get_readme("owner", "repo")
        finally:
            await client._client.aclose()

    readme = asyncio.run(_run())
    assert readme == ""


def test_readme_base64_decoded():
    """README 存在时正确 base64 解码为文本（需求 2.2、2.9 的正常路径）。"""
    import base64

    content = "# 项目标题\n\n这是一个测试 README。"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    # GitHub 会将 base64 内容按行折叠，这里插入换行以贴近真实响应。
    folded = "\n".join([encoded[i : i + 60] for i in range(0, len(encoded), 60)])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": folded, "encoding": "base64"})

    client = _make_client(handler)

    async def _run():
        try:
            return await client._get_readme("owner", "repo")
        finally:
            await client._client.aclose()

    readme = asyncio.run(_run())
    assert readme == content


def test_fetch_snapshot_continues_without_readme():
    """整体抓取时无 README 不中止：Snapshot 的 readme 为空字符串（需求 2.9）。"""
    repo_payload = {
        "stargazers_count": 10,
        "forks_count": 2,
        "open_issues_count": 1,
        "pushed_at": "2024-01-02T03:04:05Z",
        "default_branch": "main",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/languages"):
            return httpx.Response(200, json={"Python": 500})
        if path.endswith("/readme"):
            return httpx.Response(404, json={"message": "Not Found"})
        if "/git/trees/" in path:
            return httpx.Response(200, json={"tree": [], "truncated": False})
        # /repos/{owner}/{repo}
        return httpx.Response(200, json=repo_payload)

    client = _make_client(handler)

    async def _run():
        try:
            return await client.fetch_snapshot("owner", "repo")
        finally:
            await client._client.aclose()

    snapshot = asyncio.run(_run())

    assert snapshot.readme == ""
    assert snapshot.metadata.stars == 10
    assert snapshot.metadata.last_commit_at == "2024-01-02T03:04:05+00:00"
    assert snapshot.tree == []
    assert snapshot.tree_truncated is False
