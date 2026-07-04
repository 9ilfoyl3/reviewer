/**
 * useAnalysisStream 单元测试（需求 5.3、5.4、8.5、8.6）。
 *
 * 用假的 EventSource 替身驱动 SSE 帧，用 fake timers 驱动断线判定与重连节奏，
 * 断言 reducer 按事件类型正确归约，以及 10s 中断判定 + 3 次重连策略。
 */

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DATA_TIMEOUT_MS,
  MAX_RECONNECT_ATTEMPTS,
  RECONNECT_DELAY_MS,
  useAnalysisStream,
} from './useAnalysisStream'
import type { ProgressEvent } from '@/types/events'

// ============ EventSource 替身 ============

class FakeEventSource {
  static instances: FakeEventSource[] = []

  url: string
  onopen: ((this: EventSource, ev: Event) => unknown) | null = null
  onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null = null
  onerror: ((this: EventSource, ev: Event) => unknown) | null = null
  closed = false
  private listeners: Record<string, EventListener[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, cb: EventListener) {
    ;(this.listeners[type] ??= []).push(cb)
  }

  close() {
    this.closed = true
  }

  /** 模拟连接建立。 */
  emitOpen() {
    this.onopen?.call(this as unknown as EventSource, new Event('open'))
  }

  /** 模拟推送一条具名事件帧。 */
  emit(event: ProgressEvent) {
    const raw = JSON.stringify(event)
    const msg = { data: raw } as MessageEvent
    // 具名事件走 addEventListener，同时 onmessage 也会被 default 帧触发。
    for (const cb of this.listeners[event.type] ?? []) {
      cb(msg as Event)
    }
  }

  /** 模拟底层连接错误。 */
  emitError() {
    this.onerror?.call(this as unknown as EventSource, new Event('error'))
  }

  static reset() {
    FakeEventSource.instances = []
  }

  static get last(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1]
  }
}

function makeEvent(partial: Partial<ProgressEvent> & { type: ProgressEvent['type']; seq: number }): ProgressEvent {
  return {
    session_id: 's1',
    agent: null,
    data: {},
    ts: Date.now(),
    ...partial,
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeEventSource.reset()
  vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useAnalysisStream reducer 归约', () => {
  it('thought 事件逐段追加到对应 Agent（需求 5.4）', () => {
    const { result } = renderHook(() => useAnalysisStream('s1'))
    act(() => FakeEventSource.last.emitOpen())

    act(() => {
      FakeEventSource.last.emit(
        makeEvent({ type: 'agent_start', seq: 1, agent: 'Code_Auditor' }),
      )
      FakeEventSource.last.emit(
        makeEvent({ type: 'thought', seq: 2, agent: 'Code_Auditor', data: { content: '分析', iteration: 1 } }),
      )
      FakeEventSource.last.emit(
        makeEvent({ type: 'thought', seq: 3, agent: 'Code_Auditor', data: { content: '目录', iteration: 1 } }),
      )
    })

    const agent = result.current.agents['Code_Auditor']
    expect(agent.thought).toBe('分析目录')
    expect(agent.status).toBe('running')
  })

  it('tool_call 追加、tool_result 匹配并截断摘要（需求 5.5）', () => {
    const { result } = renderHook(() => useAnalysisStream('s1'))
    act(() => FakeEventSource.last.emitOpen())

    const longSummary = 'x'.repeat(600)
    act(() => {
      FakeEventSource.last.emit(
        makeEvent({ type: 'tool_call', seq: 1, agent: 'A', data: { tool: 'read_file', args: { path: 'a.py' } } }),
      )
      FakeEventSource.last.emit(
        makeEvent({ type: 'tool_result', seq: 2, agent: 'A', data: { tool: 'read_file', summary: longSummary, truncated: true } }),
      )
    })

    const tools = result.current.agents['A'].tools
    expect(tools).toHaveLength(1)
    expect(tools[0].completed).toBe(true)
    expect(tools[0].summary?.length).toBeLessThanOrEqual(501) // 500 + 省略号
  })

  it('final_report 渲染报告并关闭连接（需求 5.8）', () => {
    const { result } = renderHook(() => useAnalysisStream('s1'))
    act(() => FakeEventSource.last.emitOpen())

    const report = {
      metadata_summary: { stars: 1, forks: 2, language_distribution: [] },
      code_auditor: { strengths: [], improvements: [], summary: '' },
      product_value: { readme_clarity: [], practical_value: [], activeness: [], summary: '' },
      recommendations: [],
      score: 88,
    }
    act(() => {
      FakeEventSource.last.emit(makeEvent({ type: 'final_report', seq: 1, data: report as unknown as Record<string, unknown> }))
    })

    expect(result.current.report?.score).toBe(88)
    expect(result.current.sessionStatus).toBe('completed')
    expect(result.current.connectionStatus).toBe('closed')
    expect(FakeEventSource.last.closed).toBe(true)
  })

  it('error 事件显示中断提示并关闭（需求 5.7）', () => {
    const { result } = renderHook(() => useAnalysisStream('s1'))
    act(() => FakeEventSource.last.emitOpen())

    act(() => {
      FakeEventSource.last.emit(makeEvent({ type: 'error', seq: 1, data: { message: '抓取失败' } }))
    })

    expect(result.current.error).toBe('抓取失败')
    expect(result.current.sessionStatus).toBe('failed')
    expect(result.current.connectionStatus).toBe('closed')
  })
})

describe('useAnalysisStream 断线与重连', () => {
  it('10 秒无数据判定中断并自动重连，最多 3 次（需求 8.5、8.6）', () => {
    const { result } = renderHook(() => useAnalysisStream('s1'))
    act(() => FakeEventSource.last.emitOpen())
    expect(result.current.connectionStatus).toBe('open')

    const initialCount = FakeEventSource.instances.length

    // 逐次触发 10s 中断 + 3s 重连，共 3 次重连。
    for (let i = 1; i <= MAX_RECONNECT_ATTEMPTS; i++) {
      act(() => {
        vi.advanceTimersByTime(DATA_TIMEOUT_MS) // 判定中断
      })
      expect(result.current.connectionStatus).toBe('interrupted')
      act(() => {
        vi.advanceTimersByTime(RECONNECT_DELAY_MS) // 触发重连
      })
      expect(result.current.reconnectAttempts).toBe(i)
    }

    // 建立了 3 个新的连接。
    expect(FakeEventSource.instances.length).toBe(initialCount + MAX_RECONNECT_ATTEMPTS)

    // 第 4 次中断：耗尽，保留中断提示，不再重连。
    act(() => {
      vi.advanceTimersByTime(DATA_TIMEOUT_MS)
    })
    expect(result.current.reconnectExhausted).toBe(true)
    expect(result.current.connectionStatus).toBe('interrupted')
  })

  it('重连 URL 携带 Last-Event-ID（需求 8.6）', () => {
    renderHook(() => useAnalysisStream('s1'))
    act(() => FakeEventSource.last.emitOpen())

    act(() => {
      FakeEventSource.last.emit(makeEvent({ type: 'thought', seq: 5, agent: 'A', data: { content: 'x', iteration: 1 } }))
    })
    act(() => {
      vi.advanceTimersByTime(DATA_TIMEOUT_MS)
      vi.advanceTimersByTime(RECONNECT_DELAY_MS)
    })

    expect(FakeEventSource.last.url).toContain('last_event_id=5')
  })
})
