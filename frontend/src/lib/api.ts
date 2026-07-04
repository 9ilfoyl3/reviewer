/**
 * 后端 API 调用封装（需求 1.2、1.7）。
 *
 * - POST /api/analysis：提交仓库 URL，创建 Analysis_Session，返回 session_id
 * - GET  /api/analysis/{sid}/events：SSE 事件流（由 useAnalysisStream 用 EventSource 消费）
 */

/** API 基础地址，默认走同源相对路径，可由构建期环境变量覆盖。 */
export const API_BASE =
  (import.meta.env?.VITE_API_BASE as string | undefined) ?? ''

/** 创建分析会话的默认请求超时（需求 1.7：30 秒）。 */
export const CREATE_ANALYSIS_TIMEOUT_MS = 30_000

export interface CreateAnalysisResponse {
  session_id: string
}

/** API 调用错误，携带 HTTP 状态码（超时/网络错误时 status 为 undefined）。 */
export class ApiError extends Error {
  readonly status?: number
  readonly isTimeout: boolean

  constructor(message: string, opts?: { status?: number; isTimeout?: boolean }) {
    super(message)
    this.name = 'ApiError'
    this.status = opts?.status
    this.isTimeout = opts?.isTimeout ?? false
  }
}

/**
 * 提交仓库 URL 创建分析会话。
 *
 * @param repoUrl 已通过前端校验的合法仓库地址
 * @param signal 可选的外部 AbortSignal
 * @returns 后端返回的 session_id
 * @throws ApiError 请求超时（30s）或后端返回非成功响应时
 */
export async function createAnalysis(
  repoUrl: string,
  signal?: AbortSignal,
): Promise<CreateAnalysisResponse> {
  const controller = new AbortController()
  const timer = setTimeout(
    () => controller.abort(),
    CREATE_ANALYSIS_TIMEOUT_MS,
  )

  // 将外部 signal 与内部超时 signal 联动
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  let resp: Response
  try {
    resp = await fetch(`${API_BASE}/api/analysis`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_url: repoUrl }),
      signal: controller.signal,
    })
  } catch (err) {
    if (controller.signal.aborted) {
      throw new ApiError('请求超时，请稍后重试', { isTimeout: true })
    }
    throw new ApiError('网络请求失败，请检查连接后重试')
  } finally {
    clearTimeout(timer)
  }

  if (!resp.ok) {
    const detail = await readErrorDetail(resp)
    throw new ApiError(detail ?? `请求失败（HTTP ${resp.status}）`, {
      status: resp.status,
    })
  }

  const json = (await resp.json()) as Partial<CreateAnalysisResponse>
  if (!json || typeof json.session_id !== 'string') {
    throw new ApiError('后端响应缺少 session_id')
  }
  return { session_id: json.session_id }
}

/** 从错误响应体中尽力提取描述信息（FastAPI 风格 { detail }）。 */
async function readErrorDetail(resp: Response): Promise<string | null> {
  try {
    const data = await resp.json()
    if (data && typeof data === 'object') {
      const detail = (data as Record<string, unknown>).detail
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail) && detail.length > 0) {
        const first = detail[0] as Record<string, unknown>
        if (typeof first?.msg === 'string') return first.msg
      }
    }
  } catch {
    // 响应体非 JSON，忽略
  }
  return null
}

/**
 * 构造某个会话的 SSE 事件流 URL，供 EventSource 使用。
 *
 * @param sessionId 会话 ID
 */
export function analysisEventsUrl(sessionId: string): string {
  return `${API_BASE}/api/analysis/${encodeURIComponent(sessionId)}/events`
}

// ============ 评估历史 ============

import type { HealthReport } from '@/types/events'
import type { AgentView } from '@/hooks/useAnalysisStream'

/** 单次评估的历史摘要。 */
export interface HistoryItem {
  id: string
  repo_url: string
  owner: string
  repo: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  score?: number | null
  error?: string | null
  created_at: string
  updated_at: string
}

/** 按仓库聚合的一组历史。 */
export interface HistoryGroup {
  owner: string
  repo: string
  repo_url: string
  records: HistoryItem[]
}

/** 单次评估详情（含完整报告与多 Agent 协作过程）。 */
export interface HistoryDetail extends HistoryItem {
  report?: HealthReport | null
  /** 聚合后的多 Agent 协作过程（供刷新/回看还原流式过程）。 */
  agents?: AgentView[] | null
}

/** 通用 JSON 请求封装。 */
async function requestJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new ApiError('网络请求失败，请检查连接后重试')
  }
  if (!resp.ok) {
    const detail = await readErrorDetail(resp)
    throw new ApiError(detail ?? `请求失败（HTTP ${resp.status}）`, {
      status: resp.status,
    })
  }
  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}

/** 拉取按仓库分组的评估历史（侧边栏用）。 */
export function fetchHistory(): Promise<HistoryGroup[]> {
  return requestJson<HistoryGroup[]>('/api/history')
}

/** 拉取某次评估的详情（含完整报告，回看用）。 */
export function fetchHistoryDetail(id: string): Promise<HistoryDetail> {
  return requestJson<HistoryDetail>(`/api/history/${encodeURIComponent(id)}`)
}

/** 删除某条评估历史。 */
export function deleteHistory(id: string): Promise<void> {
  return requestJson<void>(`/api/history/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

// ============ 模型配置 ============

/** 模型配置（响应，不含 api_key 明文）。 */
export interface ModelConfigItem {
  id: string
  name: string
  base_url: string
  model: string
  api_key_set: boolean
  is_default: boolean
  created_at: string
}

/** 新建 / 更新模型配置的入参。 */
export interface ModelConfigInput {
  name: string
  base_url: string
  model: string
  api_key?: string
  is_default?: boolean
}

/** 连通性测试结果。 */
export interface ModelTestResult {
  success: boolean
  message: string
  reply?: string | null
}

export function fetchModelConfigs(): Promise<ModelConfigItem[]> {
  return requestJson<ModelConfigItem[]>('/api/model-configs')
}

export function createModelConfig(
  body: ModelConfigInput,
): Promise<ModelConfigItem> {
  return requestJson<ModelConfigItem>('/api/model-configs', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateModelConfig(
  id: string,
  body: Partial<ModelConfigInput>,
): Promise<ModelConfigItem> {
  return requestJson<ModelConfigItem>(
    `/api/model-configs/${encodeURIComponent(id)}`,
    { method: 'PUT', body: JSON.stringify(body) },
  )
}

export function deleteModelConfig(id: string): Promise<void> {
  return requestJson<void>(`/api/model-configs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export function testModelConfig(body: {
  base_url: string
  model: string
  api_key?: string
  config_id?: string
}): Promise<ModelTestResult> {
  return requestJson<ModelTestResult>('/api/model-configs/test', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
