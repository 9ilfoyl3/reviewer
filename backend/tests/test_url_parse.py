"""Repository_URL 校验与解析单元测试及往返属性测试（任务 8.2）。

# Feature: reviewer, Property 4: URL 解析往返
# Validates: Requirements 1.4, 1.5

单元测试覆盖合法输入（带 .git HTTPS、不带 .git HTTPS、SSH 格式）与非法输入
（空字符串、缺主机名、非 git 协议、超 2048 字符）；属性测试使用 hypothesis
（≥100 样例）验证：由合法 owner/repo 组装的 URL 解析可无损还原，且任意非法
字符串解析失败（抛出 RepoUrlParseError）而不产出会话（不返回 owner/repo）。
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.api.url_parse import (
    MAX_URL_LENGTH,
    RepoUrlParseError,
    parse_repo_url,
)

# ---------------------------------------------------------------------------
# 单元测试：合法输入（需求 10.1 —— 三类合法格式）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # 带 .git 后缀的 HTTPS 地址
        ("https://github.com/octocat/hello-world.git", ("octocat", "hello-world")),
        # 不带 .git 后缀的 HTTPS 地址
        ("https://github.com/octocat/hello-world", ("octocat", "hello-world")),
        # SSH scp 简写形式
        ("git@github.com:octocat/hello-world.git", ("octocat", "hello-world")),
        # SSH scp 简写形式（不带 .git）
        ("git@github.com:octocat/hello-world", ("octocat", "hello-world")),
        # ssh:// 显式协议形式
        ("ssh://git@github.com/octocat/hello-world.git", ("octocat", "hello-world")),
        # owner/repo 含合法特殊字符（连字符、下划线、点号、数字）
        ("https://github.com/My_Org-1/repo.name.js", ("My_Org-1", "repo.name.js")),
        # 尾随斜杠应被容忍
        ("https://github.com/octocat/hello-world/", ("octocat", "hello-world")),
        # 主机名大小写不敏感
        ("https://GitHub.com/octocat/hello-world", ("octocat", "hello-world")),
    ],
)
def test_parse_repo_url_accepts_valid_inputs(url, expected):
    """合法 URL 应解析出正确的 (owner, repo)（需求 1.4、10.1）。"""
    assert parse_repo_url(url) == expected


# ---------------------------------------------------------------------------
# 单元测试：非法输入（需求 10.1 —— 四类非法输入 + 其它格式错误）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "",                       # 空字符串
        "   ",                    # 仅空白
        "https:///octocat/repo",  # 缺少主机名
        "ftp://github.com/octocat/repo",   # 非 git 协议
        "http://github.com/octocat/repo",  # 非 git 协议（http 非受支持协议）
        "https://x.com/" + "a" * MAX_URL_LENGTH,  # 超过 2048 字符
        "octocat/hello-world",    # 缺少协议
        "https://github.com/octocat",             # 路径段不足
        "https://github.com/octocat/repo/extra",  # 路径段过多
        "https://gitlab.com/octocat/repo",         # 主机名非 github.com
        "https://github.com//repo",                # owner 为空
        "https://github.com/oct at/repo",          # owner 含非法字符（空格）
    ],
)
def test_parse_repo_url_rejects_invalid_inputs(url):
    """非法 URL 应抛出 RepoUrlParseError（需求 1.5）。"""
    with pytest.raises(RepoUrlParseError):
        parse_repo_url(url)


def test_parse_repo_url_over_length_boundary():
    """恰好超过上限一个字符即视为超长（需求 10.1 边界）。"""
    # 构造总长度为 MAX_URL_LENGTH + 1 的 URL。
    prefix = "https://github.com/octocat/"
    repo = "a" * (MAX_URL_LENGTH + 1 - len(prefix))
    url = prefix + repo
    assert len(url) == MAX_URL_LENGTH + 1
    with pytest.raises(RepoUrlParseError):
        parse_repo_url(url)


def test_parse_error_does_not_return_owner_repo():
    """解析失败路径不产出 owner/repo——异常应在返回前抛出（需求 1.5）。"""
    result = None
    with pytest.raises(RepoUrlParseError):
        result = parse_repo_url("ftp://github.com/octocat/repo")
    assert result is None


# ---------------------------------------------------------------------------
# 属性测试：Property 4 —— URL 解析往返
# ---------------------------------------------------------------------------

# owner / repo 合法字符：字母、数字、连字符、下划线、点号（需求 1.2）。
_segment_chars = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-",
    min_size=1,
    max_size=40,
)

# repo 段还需避免恰好以 ".git" 结尾——否则组装 "{repo}.git" 后解析会剥离两次，
# 导致还原不相等。这属于 URL 层面的固有歧义，生成器智能约束以聚焦往返属性本身。
_owner = _segment_chars
_repo = _segment_chars.filter(lambda s: not s.endswith(".git"))

# 三类合法 URL 组装模板。
_url_templates = [
    lambda o, r: f"https://github.com/{o}/{r}.git",
    lambda o, r: f"https://github.com/{o}/{r}",
    lambda o, r: f"git@github.com:{o}/{r}.git",
    lambda o, r: f"ssh://git@github.com/{o}/{r}.git",
]


@settings(max_examples=200)
@given(
    owner=_owner,
    repo=_repo,
    template=st.sampled_from(_url_templates),
)
def test_valid_url_roundtrip(owner: str, repo: str, template):
    """合法 owner/repo 组装的 URL 解析还原相等（需求 1.4）。"""
    url = template(owner, repo)
    assert parse_repo_url(url) == (owner, repo)


# 非法字符串生成器：任意文本，过滤掉极少数可能偶然合法的样例。
_invalid_text = st.text(max_size=100).filter(
    lambda s: not (
        s.strip()
        and "github.com" in s.lower()
        and "://" in s
    )
    and not s.strip().startswith("git@github.com:")
)


@settings(max_examples=200)
@given(bad=_invalid_text)
def test_invalid_url_never_produces_session(bad: str):
    """非法字符串解析失败（抛异常）而不产出 owner/repo（需求 1.5）。"""
    result = None
    with pytest.raises(RepoUrlParseError):
        result = parse_repo_url(bad)
    assert result is None
