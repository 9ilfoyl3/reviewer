/**
 * useAnalysisStream —— SSE 事件流消费 hook（需求 5.3、5.4、8.3、8.4、8.5、8.6）。
 *
 * 设计要点（保持数据流向清晰、单一数据源）：
 * - 用原生 `EventSource` 消费 `/api/analysis/{sid}/events`。
 * - SSE 事件是唯一数据源，`useReducer` 是唯一写入口：按事件类型归约到
 *   一份会话状态（会话状态 + 每个 Agent 状态与思考缓冲 + 报告 + 连接状态）。
 * - 连接生命周期（建连 / 断线判定 / 重连 / 终止）由 effect 内的定时器驱动，
 *   通过 dispatch 反映到 reducer 状态，子组件单向消费。
 *
 * 断线与重连（需求 8.5、8.6）：
 * - 连续 10 秒无任何数据/事件 → 判定连接中断。
 * - 自动重连最多 3 次、每次间隔 3 秒，重连时带 Last-Event-ID（对应 seq）。
 * - 3 次重连仍失败 → 保留连接中断提示，并通过 `reconnect()` 暴露重新发起入口。
 *
 * 终止（需求 5.8）：收到 final_report 或 error 后主动 close，不再重连或推送。
 */

import { useCallback, useEffect, useReducer, useRef } from 'react'

import type { EventType, HealthReport, ProgressEvent } from '@/types/events'
import { analysisEventsUrl } from '@/lib/api'
import {
  asErrorData,
  asThoughtData,
  asToolCallData,
  asToolResultData,
  parseProgressEvent,
  truncateSummary,
} from '@/lib/sseParser'

// ============ 常量 ============

/** 连续无数据判定中断的阈值（需求 8.5：10 秒）。 */
export const DATA_TIMEOUT_MS = 10_000

/** 自动重连间隔（需求 8.6：3 秒）。 */
export const RECONNECT_DELAY_MS = 3_000

/** 自动重连最大次数（需求 8.6：最多 3 次）。 */
export const MAX_RECONNECT_ATTEMPTS = 3

/** 需要监听的具名 SSE 事件（后端以 `event: {type}` 帧推送）。 */
const EVENT_NAMES: readonly EventType[] = [
  'agent_start',
  'thought',
  'tool_call',
  'tool_result',
  'agent_complete',
  'final_report',
  'error',
  'heartbeat',
]

// ============ 状态类型 ============

/** 会话整体状态。 */
export type SessionStatus = 'idle' | 'running' | 'completed' | 'failed'

/** SSE 连接状态。 */
export type ConnectionStatus =
  | 'connecting'
  | 'open'
  | 'interrupted'
  | 'reconnecting'
  | 'closed'

/** 单个 Agent 的展示状态（需求 8.3）。 */
export type AgentStatus = 'waiting' | 'running' | 'completed' | 'failed'

/** 一次工具调用及其结果摘要（需求 5.5）。 */
export interface ToolActivity {
  /** 工具名称。 */
  tool: string
  /** 调用参数。 */
  args: Record<string, unknown>
  /** 结果摘要（>500 字符已截断展示），未返回结果前为 undefined。 */
  summary?: string
  /** 结果是否被截断。 */
  truncated?: boolean
  /** 是否已收到工具结果。 */
  completed: boolean
}

/** 单个 Agent 的归约视图。 */
export interface AgentView {
  /** 角色名。 */
  name: string
  /** 状态徽章（等待/执行/完成/失败）。 */
  status: AgentStatus
  /** 逐 token 追加的思考缓冲（需求 5.4）。 */
  thought: string
  /** 当前 ReAct 轮次。 */
  iteration: number
  /** 工具调用序列。 */
  tools: ToolActivity[]
}

/** hook 的单一数据源状态。 */
export interface AnalysisStreamState {
  /** 会话整体状态。 */
  sessionStatus: SessionStatus
  /** SSE 连接状态。 */
  connectionStatus: ConnectionStatus
  /** 各 Agent 状态映射。 */
  agents: Record<string, AgentView>
  /** Agent 首次出现顺序，用于稳定渲染。 */
  agentOrder: string[]
  /** 最终健康体检报告，未生成前为 null。 */
  report: HealthReport | null
  /** 中断/失败原因提示，无则为 null。 */
  error: string | null
  /** 已接收的最大 seq，用作 Last-Event-ID。 */
  lastSeq: number
  /** 当前重连尝试计数。 */
  reconnectAttempts: number
  /** 是否已耗尽全部重连次数（需求 8.6）。 */
  reconnectExhausted: boolean
}

const initialState: AnalysisStreamState = {
  sessionStatus: 'idle',
  connectionStatus: 'connecting',
  agents: {},
  agentOrder: [],
  report: null,
  error: null,
  lastSeq: 0,
  reconnectAttempts: 0,
  reconnectExhausted: false,
}

// ============ Reducer ============

type Action =
  | { type: 'reset' }
  | { type: 'connecting' }
  | { type: 'open' }
  | { type: 'interrupted' }
  | { type: 'reconnecting'; attempt: number }
  | { type: 'exhausted' }
  | { type: 'event'; event: ProgressEvent }

function emptyAgent(name: string): AgentView {
  return { name, status: 'waiting', thought: '', iteration: 0, tools: [] }
}

/** 取事件所属 Agent 名，优先用顶层 agent 字段，回退到 data.agent。 */
function agentNameOf(event: ProgressEvent): string | null {
  if (typeof event.agent === 'string' && event.agent.length > 0) {
    return event.agent
  }
  const dataAgent = (event.data as Record<string, unknown>).agent
  return typeof dataAgent === 'string' && dataAgent.length > 0 ? dataAgent : null
}

/** 确保指定 Agent 存在，返回带该 Agent 的新 state（不可变更新）。 */
function ensureAgent(
  state: AnalysisStreamState,
  name: string,
): AnalysisStreamState {
  if (state.agents[name]) return state
  return {
    ...state,
    agents: { ...state.agents, [name]: emptyAgent(name) },
    agentOrder: [...state.agentOrder, name],
  }
}

/** 对指定 Agent 应用局部更新，返回新 state（不可变更新）。 */
function updateAgent(
  state: AnalysisStreamState,
  name: string,
  patch: (agent: AgentView) => AgentView,
): AnalysisStreamState {
  const ensured = ensureAgent(state, name)
  const current = ensured.agents[name]
  return {
    ...ensured,
    agents: { ...ensured.agents, [name]: patch(current) },
  }
}

function reduceEvent(
  state: AnalysisStreamState,
  event: ProgressEvent,
): AnalysisStreamState {
  const isTerminal = event.type === 'final_report' || event.type === 'error'

  // 去重：重连补发的历史事件（seq 不大于已接收）直接忽略，避免思考重复追加；
  // 终态事件（final_report/error）例外，始终应用以保证收敛。
  if (event.seq <= state.lastSeq && !isTerminal) {
    return state
  }

  const lastSeq = Math.max(state.lastSeq, event.seq)
  const base: AnalysisStreamState = { ...state, lastSeq }
  const name = agentNameOf(event)

  switch (event.type) {
    case 'agent_start': {
      if (!name) return base
      return updateAgent(base, name, (a) => ({ ...a, status: 'running' }))
    }

    case 'thought': {
      if (!name) return base
      const { content, iteration } = asThoughtData(event)
      return updateAgent(base, name, (a) => ({
        ...a,
        status: 'running',
        thought: a.thought + content,
        iteration: Math.max(a.iteration, iteration),
      }))
    }

    case 'tool_call': {
      if (!name) return base
      const { tool, args } = asToolCallData(event)
      return updateAgent(base, name, (a) => ({
        ...a,
        status: 'running',
        tools: [...a.tools, { tool, args, completed: false }],
      }))
    }

    case 'tool_result': {
      if (!name) return base
      const { tool, summary, truncated } = asToolResultData(event)
      const display = truncateSummary(summary)
      return updateAgent(base, name, (a) => {
        // 就近匹配同名未完成的工具调用；找不到则补一条已完成项。
        const idx = findPendingToolIndex(a.tools, tool)
        if (idx === -1) {
          return {
            ...a,
            tools: [
              ...a.tools,
              { tool, args: {}, summary: display, truncated, completed: true },
            ],
          }
        }
        const tools = a.tools.slice()
        tools[idx] = { ...tools[idx], summary: display, truncated, completed: true }
        return { ...a, tools }
      })
    }

    case 'agent_complete': {
      if (!name) return base
      return updateAgent(base, name, (a) => ({ ...a, status: 'completed' }))
    }

    case 'final_report': {
      // 后端以 FinalReportData(report=...) 承载，data 形如 { report: {...} }；
      // 兼容直接为报告对象的情况（如测试），优先取 data.report。
      const data = event.data as Record<string, unknown>
      const report = ((data.report ?? data) as unknown) as HealthReport
      return {
        ...base,
        report,
        sessionStatus: 'completed',
        connectionStatus: 'closed',
      }
    }

    case 'error': {
      const { message } = asErrorData(event)
      // 标记正在执行的 Agent 为失败，便于看板反馈。
      const agents = { ...base.agents }
      for (const key of base.agentOrder) {
        if (agents[key].status === 'running') {
          agents[key] = { ...agents[key], status: 'failed' }
        }
      }
      return {
        ...base,
        agents,
        error: message,
        sessionStatus: 'failed',
        connectionStatus: 'closed',
      }
    }

    case 'heartbeat':
    default:
      // 心跳仅用于保活，不改变业务状态。
      return base
  }
}

/** 查找同名且尚未收到结果的工具调用下标，从后向前就近匹配。 */
function findPendingToolIndex(tools: ToolActivity[], tool: string): number {
  for (let i = tools.length - 1; i >= 0; i--) {
    if (tools[i].tool === tool && !tools[i].completed) return i
  }
  return -1
}

function reducer(
  state: AnalysisStreamState,
  action: Action,
): AnalysisStreamState {
  switch (action.type) {
    case 'reset':
      return { ...initialState, sessionStatus: 'running', connectionStatus: 'connecting' }
    case 'connecting':
      return { ...state, connectionStatus: 'connecting' }
    case 'open':
      // 成功建连后清除中断计数与耗尽标记。
      return {
        ...state,
        connectionStatus: 'open',
        reconnectAttempts: 0,
        reconnectExhausted: false,
      }
    case 'interrupted':
      return { ...state, connectionStatus: 'interrupted' }
    case 'reconnecting':
      return {
        ...state,
        connectionStatus: 'reconnecting',
        reconnectAttempts: action.attempt,
      }
    case 'exhausted':
      // 保留中断提示（需求 8.6）。
      return { ...state, connectionStatus: 'interrupted', reconnectExhausted: true }
    case 'event':
      return reduceEvent(state, action.event)
    default:
      return state
  }
}

// ============ URL 构造 ============

/** 构造带 Last-Event-ID 的 SSE URL（重连时用 seq 续传，需求 8.6）。 */
function buildEventsUrl(sessionId: string, lastSeq: number): string {
  const base = analysisEventsUrl(sessionId)
  if (lastSeq > 0) {
    const sep = base.includes('?') ? '&' : '?'
    return `${base}${sep}last_event_id=${lastSeq}`
  }
  return base
}

// ============ Hook ============

export interface UseAnalysisStreamResult extends AnalysisStreamState {
  /** Agent 视图数组（按首次出现顺序），便于直接渲染。 */
  agentList: AgentView[]
  /** 手动重新发起连接（用于 3 次重连耗尽后的重试入口，需求 8.6）。 */
  reconnect: () => void
}

/**
 * 消费某个会话的 SSE 事件流。
 *
 * @param sessionId 会话 ID；为 null/空时不建立连接（空闲态）
 * @returns 归约后的会话状态、Agent 列表与手动重连入口
 */
export function useAnalysisStream(
  sessionId: string | null,
): UseAnalysisStreamResult {
  const [state, dispatch] = useReducer(reducer, initialState)

  // 连接与定时器句柄用 ref 持有，避免频繁重建 effect。
  const esRef = useRef<EventSource | null>(null)
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const attemptsRef = useRef(0)
  const lastSeqRef = useRef(0)
  const terminatedRef = useRef(false)

  // 手动重连计数：递增触发 effect 重跑（重新建连）。
  const [manualNonce, setManualNonce] = useReducer((n: number) => n + 1, 0)

  const reconnect = useCallback(() => {
    setManualNonce()
  }, [])

  useEffect(() => {
    if (!sessionId) return

    let disposed = false
    terminatedRef.current = false
    attemptsRef.current = 0
    lastSeqRef.current = 0
    dispatch({ type: 'reset' })

    const clearWatchdog = () => {
      if (watchdogRef.current) {
        clearTimeout(watchdogRef.current)
        watchdogRef.current = null
      }
    }
    const clearReconnect = () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
    }
    const closeEs = () => {
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
    }

    // 断线判定守护：10 秒无数据则触发中断处理。
    const armWatchdog = () => {
      clearWatchdog()
      watchdogRef.current = setTimeout(handleInterrupt, DATA_TIMEOUT_MS)
    }

    // 终止：收到终态事件或组件卸载，停止一切连接与定时器。
    const terminate = () => {
      terminatedRef.current = true
      clearWatchdog()
      clearReconnect()
      closeEs()
    }

    // 收到任意帧（含心跳）：视为有数据，重置守护与重连计数并归约。
    const handleData = (raw: string) => {
      if (disposed || terminatedRef.current) return
      attemptsRef.current = 0
      armWatchdog()
      const event = parseProgressEvent(raw)
      if (!event) return
      if (event.seq > lastSeqRef.current) lastSeqRef.current = event.seq
      dispatch({ type: 'event', event })
      if (event.type === 'final_report' || event.type === 'error') {
        terminate()
      }
    }

    // 中断处理：关闭当前连接，按策略调度重连或标记耗尽。
    const handleInterrupt = () => {
      if (disposed || terminatedRef.current) return
      closeEs()
      clearWatchdog()
      clearReconnect()
      if (attemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
        dispatch({ type: 'exhausted' })
        return
      }
      dispatch({ type: 'interrupted' })
      reconnectTimerRef.current = setTimeout(() => {
        if (disposed || terminatedRef.current) return
        attemptsRef.current += 1
        dispatch({ type: 'reconnecting', attempt: attemptsRef.current })
        open()
      }, RECONNECT_DELAY_MS)
    }

    const open = () => {
      if (disposed || terminatedRef.current) return
      dispatch({ type: 'connecting' })
      const es = new EventSource(buildEventsUrl(sessionId, lastSeqRef.current))
      esRef.current = es

      const onFrame = (e: MessageEvent) => handleData(e.data)
      es.onopen = () => {
        if (disposed) return
        dispatch({ type: 'open' })
        armWatchdog()
      }
      es.onmessage = onFrame
      for (const name of EVENT_NAMES) {
        es.addEventListener(name, onFrame as EventListener)
      }
      es.onerror = () => {
        // 交由统一中断逻辑接管（关闭原生自动重连，改由我们控制节奏）。
        handleInterrupt()
      }

      // 建连阶段也启动守护，避免一直连不上时无法判定中断。
      armWatchdog()
    }

    open()

    return () => {
      disposed = true
      clearWatchdog()
      clearReconnect()
      closeEs()
    }
    // manualNonce 变化时重建连接（重试入口）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, manualNonce])

  const agentList = state.agentOrder.map((name) => state.agents[name])

  return { ...state, agentList, reconnect }
}
