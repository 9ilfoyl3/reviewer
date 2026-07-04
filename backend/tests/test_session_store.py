"""Analysis_Session 状态模型单元测试（任务 2.6）。"""

import pytest
from pydantic import ValidationError

from app.queue.session_store import AnalysisSession, SessionStatus


def test_session_status_values():
    """SessionStatus 枚举包含四个状态且取值为对应字符串。"""
    assert SessionStatus.QUEUED.value == "queued"
    assert SessionStatus.RUNNING.value == "running"
    assert SessionStatus.COMPLETED.value == "completed"
    assert SessionStatus.FAILED.value == "failed"
    assert {s.value for s in SessionStatus} == {
        "queued",
        "running",
        "completed",
        "failed",
    }


def test_session_status_is_str_enum():
    """SessionStatus 为 str 枚举，可直接与字符串比较。"""
    assert SessionStatus.QUEUED == "queued"


def test_analysis_session_defaults_error_none():
    """error 字段默认为 None。"""
    session = AnalysisSession(
        session_id="s1",
        repo_url="https://github.com/owner/repo",
        owner="owner",
        repo="repo",
        status=SessionStatus.QUEUED,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
    assert session.error is None
    assert session.status is SessionStatus.QUEUED


def test_analysis_session_accepts_error_and_status_string():
    """status 可用字符串赋值，error 可携带失败原因。"""
    session = AnalysisSession(
        session_id="s2",
        repo_url="https://github.com/owner/repo",
        owner="owner",
        repo="repo",
        status="failed",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:05Z",
        error="抓取超时",
    )
    assert session.status is SessionStatus.FAILED
    assert session.error == "抓取超时"


def test_analysis_session_missing_required_field_raises():
    """缺失必需字段时抛出 ValidationError。"""
    with pytest.raises(ValidationError):
        AnalysisSession(
            session_id="s3",
            repo_url="https://github.com/owner/repo",
            owner="owner",
            repo="repo",
            status=SessionStatus.QUEUED,
            created_at="2024-01-01T00:00:00Z",
            # 缺 updated_at
        )
