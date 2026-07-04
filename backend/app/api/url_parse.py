"""后端仓库 URL 校验与 owner/repo 解析（需求 1.4、1.5）。

对应设计文档 API 层「``POST /api/analysis`` 校验与 URL 解析」。API 层在创建
Analysis_Session 前调用 :func:`parse_repo_url` 校验用户提交的 Repository_URL 并
解析出 ``owner`` 与 ``repo`` 两个非空标识；解析失败时抛出
:class:`RepoUrlParseError`，由 API 层转为 HTTP 400 且**不创建会话**（需求 1.5）。

支持的合法格式（需求 10.1）：

- 带 ``.git`` 后缀的 HTTPS：``https://github.com/{owner}/{repo}.git``
- 不带 ``.git`` 后缀的 HTTPS：``https://github.com/{owner}/{repo}``
- SSH 格式：``git@github.com:{owner}/{repo}.git`` 或
  ``ssh://git@github.com/{owner}/{repo}.git``

拒绝的非法输入（需求 10.1）：

- 空字符串（或仅空白）
- 缺少主机名的地址（如 ``https:///owner/repo``）
- 非 git 协议地址（如 ``ftp://...``、``http://...``）
- 超过 2048 个字符的地址

``owner`` 与 ``repo`` 均须非空，且仅由字母、数字、连字符、下划线或点号组成
（需求 1.2）。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Repository_URL 最大长度（需求 1.1、1.3、10.1）。
MAX_URL_LENGTH = 2048

# GitHub 主机名（大小写不敏感比较）。
_GITHUB_HOST = "github.com"

# owner / repo 合法字符：字母、数字、连字符、下划线、点号，且非空（需求 1.2）。
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# 允许的 URL 协议（scheme）；其余一律视为“非 git 协议”而拒绝（需求 10.1）。
_ALLOWED_SCHEMES = ("https", "ssh", "git")

# SSH scp 简写形式：``git@github.com:owner/repo(.git)?``（无 scheme，以冒号分隔主机与路径）。
_SCP_SSH_RE = re.compile(
    r"^git@(?P<host>[^:/]+):(?P<path>.+)$",
)


class RepoUrlParseError(ValueError):
    """Repository_URL 校验 / 解析失败时抛出的描述性错误（需求 1.5）。

    错误信息指明具体的失败原因（空、超长、缺主机名、非 git 协议、格式非法等），
    供 API 层返回 HTTP 400 时携带原因。
    """


def _strip_git_suffix(repo: str) -> str:
    """去除 repo 末尾的 ``.git`` 后缀（若有）。"""
    if repo.endswith(".git"):
        return repo[: -len(".git")]
    return repo


def _split_owner_repo(path: str) -> tuple[str, str]:
    """从路径片段解析并校验 ``owner`` 与 ``repo``。

    Args:
        path: 主机名之后的路径部分，形如 ``owner/repo`` 或 ``owner/repo.git``，
            可能带前导 / 尾随斜杠。

    Returns:
        ``(owner, repo)`` 二元组。

    Raises:
        RepoUrlParseError: 路径不含恰好两段、或 owner/repo 含非法字符 / 为空。
    """
    # 去除前后斜杠后按 / 切分，剔除空段（容忍多余斜杠）。
    segments = [seg for seg in path.strip("/").split("/") if seg != ""]
    if len(segments) != 2:
        raise RepoUrlParseError(
            "URL 格式非法：应为 https://github.com/{owner}/{repo} 形式，"
            "需恰好包含 owner 与 repo 两段路径。"
        )

    owner, repo = segments[0], segments[1]
    repo = _strip_git_suffix(repo)

    for label, value in (("owner", owner), ("repo", repo)):
        if not value:
            raise RepoUrlParseError(f"URL 格式非法：{label} 不能为空。")
        if not _SEGMENT_RE.match(value):
            raise RepoUrlParseError(
                f"URL 格式非法：{label} '{value}' 含非法字符，"
                "仅允许字母、数字、连字符、下划线与点号。"
            )

    return owner, repo


def parse_repo_url(url: str) -> tuple[str, str]:
    """校验 Repository_URL 并解析出 ``owner`` 与 ``repo``（需求 1.4、1.5）。

    Args:
        url: 用户提交的仓库 URL。

    Returns:
        ``(owner, repo)`` 二元组，二者均非空且仅含合法字符。

    Raises:
        RepoUrlParseError: URL 为空、超长、缺主机名、非 git 协议或格式非法。
    """
    # 空 / 仅空白（需求 10.1）。
    if url is None or not str(url).strip():
        raise RepoUrlParseError("URL 不能为空。")

    url = str(url).strip()

    # 超长（需求 10.1）。
    if len(url) > MAX_URL_LENGTH:
        raise RepoUrlParseError(
            f"URL 长度 {len(url)} 超过上限 {MAX_URL_LENGTH} 个字符。"
        )

    # SSH scp 简写形式：git@github.com:owner/repo(.git)?（无 scheme）。
    scp_match = _SCP_SSH_RE.match(url)
    if scp_match:
        host = scp_match.group("host")
        if host.lower() != _GITHUB_HOST:
            raise RepoUrlParseError(
                f"URL 主机名非法：仅支持 {_GITHUB_HOST}，实际为 '{host}'。"
            )
        return _split_owner_repo(scp_match.group("path"))

    # 其余按带 scheme 的 URL 解析。
    parts = urlsplit(url)

    # 缺少 scheme 视为非 git 协议 / 格式非法（需求 10.1）。
    if not parts.scheme:
        raise RepoUrlParseError(
            "URL 缺少协议：应以 https:// 或 ssh:// 等 git 协议开头。"
        )

    # 非 git 协议（需求 10.1）。
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise RepoUrlParseError(
            f"URL 协议非法：'{parts.scheme}' 不是受支持的 git 协议"
            f"（仅支持 {', '.join(_ALLOWED_SCHEMES)}）。"
        )

    # 缺少主机名（需求 10.1）。例如 https:///owner/repo。
    host = parts.hostname
    if not host:
        raise RepoUrlParseError("URL 缺少主机名。")

    if host.lower() != _GITHUB_HOST:
        raise RepoUrlParseError(
            f"URL 主机名非法：仅支持 {_GITHUB_HOST}，实际为 '{host}'。"
        )

    return _split_owner_repo(parts.path)
