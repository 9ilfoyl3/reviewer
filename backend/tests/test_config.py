"""配置启动期 fail-fast 校验单元测试（任务 1.3）。

覆盖：
  - 必需变量缺失时逐项报错并终止（需求 7.3、9.4）
  - 可选变量（GITHUB_TOKEN）缺失时提示并继续（需求 9.5）
  - AGENT_MAX_ITERATIONS 越界/非法值处理（需求 4.7）
"""

import logging

import pytest

from app.config import REQUIRED_ENV_VARS, Settings, validate_settings


def _valid_settings(**overrides) -> Settings:
    """构造一个必需项齐全的 Settings，便于按需覆盖单个字段。"""
    base = {
        "llm_base_url": "https://api.example.com/v1",
        "llm_api_key": "sk-test",
        "llm_model": "gpt-4o-mini",
        "github_token": "ghp_test",
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# 必需变量缺失：逐项报错并终止（需求 7.3、9.4）
# ---------------------------------------------------------------------------


def test_required_vars_present_passes():
    """必需项齐全时校验通过，不抛异常。"""
    validate_settings(_valid_settings())


@pytest.mark.parametrize(
    "field_name, env_name",
    [
        ("llm_base_url", "LLM_BASE_URL"),
        ("llm_api_key", "LLM_API_KEY"),
        ("llm_model", "LLM_MODEL"),
    ],
)
def test_single_required_var_missing_raises(field_name, env_name):
    """任一必需项为空时抛 RuntimeError，且错误信息含该变量名。"""
    settings = _valid_settings(**{field_name: ""})
    with pytest.raises(RuntimeError) as exc_info:
        validate_settings(settings)
    assert env_name in str(exc_info.value)


def test_whitespace_only_required_var_treated_as_missing():
    """仅含空白字符的必需项视为缺失。"""
    settings = _valid_settings(llm_api_key="   ")
    with pytest.raises(RuntimeError) as exc_info:
        validate_settings(settings)
    assert "LLM_API_KEY" in str(exc_info.value)


def test_multiple_required_vars_missing_reports_each_item(caplog):
    """多个必需项缺失时逐项打印每个缺失变量名（需求 9.4），并统一报错终止。"""
    settings = _valid_settings(llm_base_url="", llm_api_key="", llm_model="")
    with caplog.at_level(logging.ERROR, logger="app.config"):
        with pytest.raises(RuntimeError) as exc_info:
            validate_settings(settings)

    # 逐项报错：每个缺失变量名都应出现在错误日志中
    error_text = "\n".join(rec.getMessage() for rec in caplog.records if rec.levelno == logging.ERROR)
    for env_name in REQUIRED_ENV_VARS:
        assert env_name in error_text

    # 每个缺失项对应一条 error 日志（逐项而非只报第一个）
    error_records = [rec for rec in caplog.records if rec.levelno == logging.ERROR]
    assert len(error_records) == len(REQUIRED_ENV_VARS)

    # 终止启动的异常信息也应包含全部缺失变量名
    for env_name in REQUIRED_ENV_VARS:
        assert env_name in str(exc_info.value)


# ---------------------------------------------------------------------------
# 可选变量缺失：提示并继续（需求 9.5）
# ---------------------------------------------------------------------------


def test_optional_github_token_missing_warns_and_continues(caplog):
    """GITHUB_TOKEN 缺失时仅打印 warning 提示并继续，不抛异常。"""
    settings = _valid_settings(github_token="")
    with caplog.at_level(logging.WARNING, logger="app.config"):
        validate_settings(settings)  # 不应抛异常

    warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "GITHUB_TOKEN" in warnings[0].getMessage()


def test_optional_github_token_present_no_warning(caplog):
    """GITHUB_TOKEN 已配置时不产生缺失提示。"""
    settings = _valid_settings(github_token="ghp_real")
    with caplog.at_level(logging.WARNING, logger="app.config"):
        validate_settings(settings)

    messages = [rec.getMessage() for rec in caplog.records]
    assert not any("GITHUB_TOKEN" in m for m in messages)


# ---------------------------------------------------------------------------
# AGENT_MAX_ITERATIONS 越界处理（需求 4.7）
# ---------------------------------------------------------------------------


def test_agent_max_iterations_default():
    """未提供时使用默认值 8。"""
    assert _valid_settings().agent_max_iterations == 8


@pytest.mark.parametrize(
    "raw, expected",
    [
        (0, 1),        # 低于下界 → 钳制到 1
        (-5, 1),       # 负数 → 钳制到 1
        (1, 1),        # 下界
        (8, 8),        # 区间内
        (20, 20),      # 上界
        (21, 20),      # 超上界 → 钳制到 20
        (1000, 20),    # 远超上界 → 钳制到 20
    ],
)
def test_agent_max_iterations_clamped(raw, expected):
    """越界的 AGENT_MAX_ITERATIONS 被钳制到 [1, 20]。"""
    assert _valid_settings(agent_max_iterations=raw).agent_max_iterations == expected


@pytest.mark.parametrize("raw", ["10", "3"])
def test_agent_max_iterations_numeric_string_parsed(raw):
    """数字字符串可被解析并钳制。"""
    assert _valid_settings(agent_max_iterations=raw).agent_max_iterations == int(raw)


@pytest.mark.parametrize("raw", ["abc", "", None])
def test_agent_max_iterations_non_int_falls_back_to_default(raw):
    """无法解析为整数时回退到默认值 8，而非导致启动失败。"""
    assert _valid_settings(agent_max_iterations=raw).agent_max_iterations == 8
