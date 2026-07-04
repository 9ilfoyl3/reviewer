"""Analysis_Session 状态模型。

定义一次仓库体检任务（Analysis_Session）的状态枚举与数据模型。
状态机流转（详见 design.md「Analysis_Session 状态机」）：

    queued --> running --> completed
                       \\--> failed
    queued --> failed（入队后超时未被消费的孤儿回收）

本模块包含：
- SessionStatus / AnalysisSession：状态枚举与 Pydantic 数据模型（任务 2.6）。
- SessionStore：基于 Redis Hash `reviewer:session:{sid}` 的会话创建、状态流转
  与超时巡检存储逻辑（任务 5.2）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum

import redis.asyncio as aioredis
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """当前时间的 ISO 8601 UTC 时间戳（秒级，带 Z 后缀）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> datetime | None:
    """解析 ISO 8601 UTC 时间戳为带时区的 datetime；无法解析时返回 None。"""
    if not ts:
        return None
    try:
        # 兼容以 Z 结尾的 UTC 表示
        normalized = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class SessionStatus(str, Enum):
    """Analysis_Session 的生命周期状态。

    - QUEUED：API 已创建会话并入队，等待 Worker 消费。
    - RUNNING：Worker 已取到任务并开始执行抓取与流水线。
    - COMPLETED：final_report 已推送，体检成功完成。
    - FAILED：抓取失败 / 流水线异常 / LLM 耗尽 / 孤儿超时回收。
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisSession(BaseModel):
    """一次仓库体检任务的会话状态。

    会话状态存于 Redis Hash `reviewer:session:{session_id}`（存储逻辑见任务 5.2）。
    """

    session_id: str
    repo_url: str
    owner: str
    repo: str
    status: SessionStatus
    created_at: str
    updated_at: str
    error: str | None = None


# 允许的状态流转（source -> 可达 targets），实现 design.md 状态机约束：
#   queued  -> running / failed
#   running -> completed / failed
#   completed / failed 为终态，不可再流转。
_ALLOWED_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.QUEUED: {SessionStatus.RUNNING, SessionStatus.FAILED},
    SessionStatus.RUNNING: {SessionStatus.COMPLETED, SessionStatus.FAILED},
    SessionStatus.COMPLETED: set(),
    SessionStatus.FAILED: set(),
}


class SessionNotFoundError(Exception):
    """请求的 Analysis_Session 在 Redis 中不存在。"""


class InvalidStateTransitionError(Exception):
    """尝试进行不被状态机允许的状态流转。"""


class SessionStore:
    """基于 Redis Hash 的 Analysis_Session 状态存储（任务 5.2）。

    每个会话以一个 Redis Hash 存储，键为 `reviewer:session:{session_id}`，
    字段与 `AnalysisSession` 模型一一对应。提供会话创建、读取、状态流转与
    running 会话超时巡检（置 failed）能力。

    该存储被 API 进程（创建会话、置 queued）与 Worker 进程（置 running /
    completed / failed）共享，二者仅通过 Redis 交互，无共享内存。
    """

    KEY_PREFIX = "reviewer:session:"

    def __init__(self, redis: aioredis.Redis) -> None:
        """使用一个已连接的 redis.asyncio 客户端构造存储。

        Args:
            redis: `redis.asyncio.Redis` 实例。应以 `decode_responses=True`
                创建，使读取的 Hash 字段为 str 而非 bytes。
        """
        self._redis = redis

    @classmethod
    def from_url(cls, redis_url: str) -> "SessionStore":
        """从 Redis URL 构造存储（便于在 API / Worker 进程入口使用）。"""
        client = aioredis.from_url(redis_url, decode_responses=True)
        return cls(client)

    def _key(self, session_id: str) -> str:
        return f"{self.KEY_PREFIX}{session_id}"

    @staticmethod
    def _to_hash(session: AnalysisSession) -> dict[str, str]:
        """将会话模型转为 Redis Hash 字段映射（None 用空串表示）。"""
        return {
            "session_id": session.session_id,
            "repo_url": session.repo_url,
            "owner": session.owner,
            "repo": session.repo,
            "status": session.status.value,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "error": session.error or "",
        }

    @staticmethod
    def _from_hash(data: dict[str, str]) -> AnalysisSession:
        """将 Redis Hash 字段映射还原为会话模型（空串 error 视为 None）。"""
        error = data.get("error") or None
        return AnalysisSession(
            session_id=data["session_id"],
            repo_url=data["repo_url"],
            owner=data["owner"],
            repo=data["repo"],
            status=SessionStatus(data["status"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            error=error,
        )

    async def create_session(
        self,
        *,
        session_id: str,
        repo_url: str,
        owner: str,
        repo: str,
    ) -> AnalysisSession:
        """创建一个初始状态为 QUEUED 的会话并写入 Redis。

        对应状态机入口 `[*] --> queued`（API 创建会话 + 入队）。

        Args:
            session_id: 会话唯一标识。
            repo_url: 用户提交的仓库 URL。
            owner / repo: 从 URL 解析出的仓库归属与名称。

        Returns:
            新建的 AnalysisSession（status=QUEUED）。
        """
        now = _utc_now_iso()
        session = AnalysisSession(
            session_id=session_id,
            repo_url=repo_url,
            owner=owner,
            repo=repo,
            status=SessionStatus.QUEUED,
            created_at=now,
            updated_at=now,
            error=None,
        )
        await self._redis.hset(self._key(session_id), mapping=self._to_hash(session))
        logger.debug("已创建会话 %s（queued）", session_id)
        return session

    async def get_session(self, session_id: str) -> AnalysisSession | None:
        """读取会话；不存在时返回 None。"""
        data = await self._redis.hgetall(self._key(session_id))
        if not data:
            return None
        return self._from_hash(data)

    async def _require_session(self, session_id: str) -> AnalysisSession:
        session = await self.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(f"会话不存在：{session_id}")
        return session

    async def update_status(
        self,
        session_id: str,
        status: SessionStatus,
        *,
        error: str | None = None,
    ) -> AnalysisSession:
        """按状态机约束流转会话状态并持久化。

        Args:
            session_id: 目标会话。
            status: 目标状态。
            error: 置为 FAILED 时的失败原因描述（可选）。

        Returns:
            更新后的 AnalysisSession。

        Raises:
            SessionNotFoundError: 会话不存在。
            InvalidStateTransitionError: 目标状态不被当前状态允许（含从终态再流转）。
        """
        session = await self._require_session(session_id)
        current = session.status

        if status == current:
            # 幂等：重复置为当前状态视为无操作，仅在需要时补充 error。
            allowed = True
        else:
            allowed = status in _ALLOWED_TRANSITIONS.get(current, set())

        if not allowed:
            raise InvalidStateTransitionError(
                f"非法状态流转：{current.value} -> {status.value}（会话 {session_id}）"
            )

        session.status = status
        session.updated_at = _utc_now_iso()
        if error is not None:
            session.error = error

        await self._redis.hset(self._key(session_id), mapping=self._to_hash(session))
        logger.debug("会话 %s 状态流转 %s -> %s", session_id, current.value, status.value)
        return session

    async def mark_running(self, session_id: str) -> AnalysisSession:
        """Worker 取到任务：queued -> running。"""
        return await self.update_status(session_id, SessionStatus.RUNNING)

    async def mark_completed(self, session_id: str) -> AnalysisSession:
        """final_report 已推送：running -> completed。"""
        return await self.update_status(session_id, SessionStatus.COMPLETED)

    async def mark_failed(self, session_id: str, error: str) -> AnalysisSession:
        """抓取失败 / 流水线异常 / LLM 耗尽：-> failed。"""
        return await self.update_status(session_id, SessionStatus.FAILED, error=error)

    async def scan_timeouts(self, timeout_seconds: float) -> list[str]:
        """巡检 running 超时会话并置 failed（孤儿回收，需求映射见 design.md）。

        遍历所有会话键，对处于 RUNNING 且 `updated_at` 距今超过
        `timeout_seconds` 的会话，将其置为 FAILED 并写入超时失败原因。

        Args:
            timeout_seconds: running 状态允许的最长停留时长（秒）。

        Returns:
            本次巡检被置 failed 的会话 ID 列表。
        """
        now = datetime.now(timezone.utc)
        timed_out: list[str] = []

        async for key in self._redis.scan_iter(match=f"{self.KEY_PREFIX}*"):
            data = await self._redis.hgetall(key)
            if not data or data.get("status") != SessionStatus.RUNNING.value:
                continue

            updated = _parse_iso(data.get("updated_at", ""))
            if updated is None:
                continue

            elapsed = (now - updated).total_seconds()
            if elapsed <= timeout_seconds:
                continue

            session_id = data.get("session_id", "")
            try:
                await self.update_status(
                    session_id,
                    SessionStatus.FAILED,
                    error=f"会话执行超时（超过 {timeout_seconds:.0f}s 未完成），已由巡检置为 failed。",
                )
                timed_out.append(session_id)
            except (SessionNotFoundError, InvalidStateTransitionError) as exc:
                # 巡检期间会话可能已被并发流转为终态，跳过即可。
                logger.debug("超时巡检跳过会话 %s：%s", session_id, exc)

        if timed_out:
            logger.info("超时巡检将 %d 个 running 会话置为 failed：%s", len(timed_out), timed_out)
        return timed_out
