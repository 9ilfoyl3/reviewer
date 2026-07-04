"""API 层请求 / 响应 Pydantic 模型（需求 1.4、1.5）。

对应设计文档 API 层。这些模型用于 ``POST /api/analysis`` 创建 Analysis_Session
的请求体与响应体，以及 URL 校验失败时的错误响应体。

- :class:`CreateAnalysisRequest` ：创建体检会话的请求体，携带用户提交的
  Repository_URL（长度约束 1–2048 字符，需求 1.1）。
- :class:`CreateAnalysisResponse`：创建成功的响应体，返回 ``session_id`` 与解析出
  的 ``owner`` / ``repo``。
- :class:`ErrorResponse`         ：校验 / 解析失败时的错误响应体（HTTP 400，
  需求 1.5），``detail`` 指明具体失败原因。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .url_parse import MAX_URL_LENGTH


class CreateAnalysisRequest(BaseModel):
    """创建 Analysis_Session 的请求体（需求 1.1、1.2、1.4）。

    ``repo_url`` 长度约束为 1–2048 字符；更细粒度的格式校验与 owner/repo 解析
    由 :func:`app.api.url_parse.parse_repo_url` 负责（解析失败返回 HTTP 400，
    需求 1.5）。
    """

    repo_url: str = Field(
        ...,
        min_length=1,
        max_length=MAX_URL_LENGTH,
        description="待体检的公开 GitHub 仓库地址，长度 1–2048 字符。",
    )


class CreateAnalysisResponse(BaseModel):
    """创建 Analysis_Session 成功的响应体。

    返回会话唯一标识与从 Repository_URL 解析出的 owner / repo，供前端订阅
    SSE 事件流与展示。
    """

    session_id: str = Field(..., description="新建 Analysis_Session 的唯一标识。")
    owner: str = Field(..., description="从 URL 解析出的仓库归属。")
    repo: str = Field(..., description="从 URL 解析出的仓库名称。")


class ErrorResponse(BaseModel):
    """错误响应体（需求 1.5）。

    URL 校验 / 解析失败时随 HTTP 400 返回，``detail`` 指明具体失败原因
    （空、超长、缺主机名、非 git 协议、格式非法等）。
    """

    detail: str = Field(..., description="错误原因描述。")
