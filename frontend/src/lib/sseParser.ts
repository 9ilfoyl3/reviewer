/**
 * SSE 事件解析辅助（需求 5.2、5.4、5.5）。
 *
 * 后端以 `event: {type}\ndata: {json}\n\n` 帧格式推送 ProgressEvent。
 * 使用原生 EventSource 时，浏览器已按帧解析并派发 MessageEvent，
 * 这里负责将 MessageEvent 的 JSON 字符串安全解析为 ProgressEvent。
 */

import type {
  ErrorData,
  EventType,
  ProgressEvent,
  ThoughtData,
  ToolCallData,
  ToolResultData,
} from '@/types/events'

/** tool_result 摘要前端展示的字符上限（需求 5.5）。 */
export const TOOL_SUMMARY_MAX = 500

/**
 * 将 SSE 帧的 data JSON 字符串解析为 ProgressEvent。
 *
 * @param raw MessageEvent.data 中的原始 JSON 文本
 * @returns 解析成功返回 ProgressEvent，语法非法或结构不符返回 null
 */
export function parseProgressEvent(raw: string): ProgressEvent | null {
  if (!raw || raw.trim().length === 0) {
    return null
  }

  let obj: unknown
  try {
    obj = JSON.parse(raw)
  } catch {
    return null
  }

  if (!isProgressEvent(obj)) {
    return null
  }

  return obj
}

const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set<EventType>([
  'agent_start',
  'thought',
  'tool_call',
  'tool_result',
  'agent_complete',
  'final_report',
  'error',
  'heartbeat',
])

/** 运行时结构校验：判定任意值是否为合法 ProgressEvent。 */
export function isProgressEvent(value: unknown): value is ProgressEvent {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const e = value as Record<string, unknown>
  return (
    typeof e.type === 'string' &&
    KNOWN_EVENT_TYPES.has(e.type) &&
    typeof e.session_id === 'string' &&
    typeof e.seq === 'number' &&
    typeof e.ts === 'number' &&
    typeof e.data === 'object' &&
    e.data !== null
  )
}

// ============ 类型收窄辅助 ============

export function asThoughtData(event: ProgressEvent): ThoughtData {
  const d = event.data
  return {
    content: typeof d.content === 'string' ? d.content : '',
    iteration: typeof d.iteration === 'number' ? d.iteration : 0,
  }
}

export function asToolCallData(event: ProgressEvent): ToolCallData {
  const d = event.data
  return {
    tool: typeof d.tool === 'string' ? d.tool : '',
    args:
      typeof d.args === 'object' && d.args !== null
        ? (d.args as Record<string, unknown>)
        : {},
  }
}

export function asToolResultData(event: ProgressEvent): ToolResultData {
  const d = event.data
  const summary = typeof d.summary === 'string' ? d.summary : ''
  return {
    tool: typeof d.tool === 'string' ? d.tool : '',
    summary,
    truncated: typeof d.truncated === 'boolean' ? d.truncated : false,
  }
}

export function asErrorData(event: ProgressEvent): ErrorData {
  const d = event.data
  return {
    message: typeof d.message === 'string' ? d.message : '未知错误',
    stage: typeof d.stage === 'string' ? d.stage : undefined,
  }
}

/**
 * 截断结果摘要用于展示（需求 5.5：超过 500 字符截断）。
 *
 * @param summary 原始摘要文本
 * @returns 不超过 TOOL_SUMMARY_MAX 字符的展示文本，被截断时追加省略号
 */
export function truncateSummary(summary: string): string {
  if (summary.length <= TOOL_SUMMARY_MAX) {
    return summary
  }
  return summary.slice(0, TOOL_SUMMARY_MAX) + '…'
}
