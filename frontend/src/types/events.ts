/**
 * 前端与后端 SSE 事件 / 报告的 TypeScript 类型定义。
 *
 * 与后端 backend/app/events/types.py（ProgressEvent / EventType）及
 * backend/app/models/report.py（HealthReport）保持结构一致。
 */

// ============ 事件类型 ============

export type EventType =
  | 'agent_start'
  | 'thought'
  | 'tool_call'
  | 'tool_result'
  | 'agent_complete'
  | 'final_report'
  | 'error'
  | 'heartbeat'

/** thought 事件载荷：Agent 推理的增量片段。 */
export interface ThoughtData {
  content: string
  iteration: number
}

/** tool_call 事件载荷：Agent 发起的工具调用。 */
export interface ToolCallData {
  tool: string
  args: Record<string, unknown>
}

/** tool_result 事件载荷：工具执行结果摘要（>500 字符前端截断）。 */
export interface ToolResultData {
  tool: string
  summary: string
  truncated: boolean
}

/** agent_start / agent_complete 事件载荷。 */
export interface AgentLifecycleData {
  agent: string
  conclusion?: unknown
}

/** error 事件载荷。 */
export interface ErrorData {
  message: string
  stage?: string
}

/**
 * 后端通过 SSE 推送的进度事件。
 *
 * `data` 为类型相关载荷，具体形状见上方 *Data 接口；
 * 消费方按 `type` 收窄后读取。`seq` 为单调递增序号，保证按序渲染。
 */
export interface ProgressEvent {
  type: EventType
  session_id: string
  agent?: string | null
  seq: number
  data: Record<string, unknown>
  ts: number
}

// ============ 健康评估报告 ============

export interface LanguagePercent {
  name: string
  percent: number
}

export interface MetadataSummary {
  stars: number
  forks: number
  language_distribution: LanguagePercent[]
}

export interface CodeAuditorOpinion {
  strengths: string[]
  improvements: string[]
  summary: string
}

export interface ProductValueOpinion {
  readme_clarity: string[]
  practical_value: string[]
  activeness: string[]
  summary: string
}

export interface HealthReport {
  metadata_summary: MetadataSummary
  code_auditor: CodeAuditorOpinion
  product_value: ProductValueOpinion
  recommendations: string[]
  score: number
}
