"""后端配置与启动期 fail-fast 校验

使用 pydantic-settings 从环境变量 / `.env` 读取配置。

必需项（缺失/为空则启动失败，需求 7.3、9.4）：
  - LLM_BASE_URL：LLM_Provider 基础地址（OpenAI 兼容）
  - LLM_API_KEY ：LLM_Provider API 密钥
  - LLM_MODEL   ：LLM_Provider 模型名称

可选项：
  - GITHUB_TOKEN         ：GitHub 访问令牌，缺失时降级为匿名访问（需求 9.5）
  - REDIS_URL            ：Redis 连接地址（队列 + Pub/Sub + 会话状态）
  - REVIEW_MAX_CONCURRENT：单 Worker 最大并发体检数（默认 4）
  - AGENT_MAX_ITERATIONS ：单 Agent ReAct 最大轮数（默认 8，范围 1–20）
"""

import logging
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# 必需环境变量清单：任一缺失/为空则 fail-fast（需求 7.3、9.4）
REQUIRED_ENV_VARS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


class Settings(BaseSettings):
    """Reviewer 后端配置。

    字段名小写；pydantic-settings 大小写不敏感地映射到环境变量
    （如 `llm_base_url` 对应 `LLM_BASE_URL`）。
    """

    # ---- LLM Provider（必需，需求 7.2） ----
    llm_base_url: str = ""  # LLM_BASE_URL：OpenAI 兼容基础地址
    llm_api_key: str = ""  # LLM_API_KEY：API 密钥
    llm_model: str = ""  # LLM_MODEL：模型名称

    # ---- GitHub（可选，需求 2.6、9.5） ----
    github_token: str = ""  # GITHUB_TOKEN：缺失时匿名访问，速率额度较低

    # ---- Redis（队列 + Pub/Sub + 会话状态） ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- PostgreSQL（体检历史 + 模型配置持久化） ----
    # 使用 asyncpg 驱动；默认指向本地 reviewer 库。
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/reviewer"
    )

    # ---- 并发与 Agent 控制 ----
    review_max_concurrent: int = 4  # 单 Worker 最大并发体检数
    # ReAct 最大轮数；合法范围 1–20（需求 4.7），越界值会被钳制到边界而非导致启动失败
    agent_max_iterations: int = 8

    model_config = {"env_file": ".env", "case_sensitive": False, "extra": "ignore"}

    @field_validator("agent_max_iterations", mode="before")
    @classmethod
    def _clamp_iterations(cls, v: object) -> int:
        """将 AGENT_MAX_ITERATIONS 钳制到 [1, 20]（需求 4.7）。

        以 mode="before" 在类型校验前介入：非法/越界配置不使整个应用启动失败，
        而是防御式地钳制到最近的合法边界（<1 → 1，>20 → 20），保证 Agent
        ReAct 循环始终拿到合法轮数上限。无法解析为整数时回退到默认值 8。
        """
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 8
        return max(1, min(20, n))


def validate_settings(settings: Settings) -> None:
    """启动期 fail-fast 校验（需求 7.3、9.4、9.5）。

    - 逐项检查必需环境变量，收集全部缺失项后统一报错并终止启动
      （逐项打印缺失名称，而非遇到第一个就退出）。
    - GITHUB_TOKEN 缺失时仅打印提示并继续（可选项降级）。

    Raises:
        RuntimeError: 存在任一必需项缺失/为空时抛出，阻止进入服务监听状态。
    """
    missing: list[str] = []
    for env_name in REQUIRED_ENV_VARS:
        # 环境变量名转为 Settings 字段名（小写）
        field_name = env_name.lower()
        value = getattr(settings, field_name, "")
        if not value or not str(value).strip():
            missing.append(env_name)

    if missing:
        # 逐项打印每个缺失环境变量名称（需求 9.4）
        for env_name in missing:
            logger.error("缺少必需环境变量：%s（请在环境或 .env 中配置后重试）", env_name)
        raise RuntimeError(
            "启动终止：缺少必需环境变量 "
            + "、".join(missing)
            + "。请配置后重新启动（fail-fast，不进入服务监听状态）。"
        )

    # 可选项 GITHUB_TOKEN 缺失：打印提示并继续（需求 9.5）
    if not settings.github_token or not settings.github_token.strip():
        logger.warning(
            "未配置可选环境变量 GITHUB_TOKEN：GitHub API 将以匿名方式访问，"
            "速率限制额度较低，可能影响大仓库抓取。"
        )


@lru_cache()
def get_settings() -> Settings:
    """获取全局配置单例，并执行启动期 fail-fast 校验。

    任一必需项缺失/为空时抛 RuntimeError，禁止静默兜底进入可服务状态。
    """
    settings = Settings()
    validate_settings(settings)
    return settings
