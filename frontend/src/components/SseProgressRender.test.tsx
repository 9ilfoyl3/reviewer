/**
 * SSE Progress_Event 渲染测试（需求 10.7；关联 5.4、5.5、6.4、6.5、8.5、8.6、1.6、1.7）。
 *
 * 与既有测试的分工：
 * - AgentBoard.test.tsx / useAnalysisStream.test.ts 分别单测「纯展示组件」与
 *   「reducer 归约状态」。
 * - 本文件做端到端渲染验证：用假 EventSource 推送真实 SSE 事件，经
 *   useAnalysisStream 归约后驱动真实组件（AgentBoard + HealthReport），
 *   断言 thought / tool_call / final_report / error 四类事件各自渲染出对应
 *   界面元素（需求 10.7）。
 * - 并用假定时器验证提交禁用/重新启用、30s 超时提示（RepoUrlForm）与
 *   10s 中断判定 + 重连（≤3 次、间隔 3s）在 UI 上的反映。
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AgentBoard } from './AgentBoard'
import { HealthReport } from './HealthReport'
import { RepoUrlForm } from './RepoUrlForm'
import {
  DATA_TIMEOUT_MS,
  MAX_RECONNECT_ATTEMPTS,
  RECONNECT_DELAY_MS,
  useAnalysisStream,
} from '@/hooks/useAnalysisStream'
import { CREATE_ANALYSIS_TIMEOUT_MS } from '@/lib/api'
import { TOOL_SUMMARY_MAX } from '@/lib/sseParser'
import type { HealthReport as HealthReportData, ProgressEvent } from '@/types/events'

// ============ EventSource 替身 ============

/** 最小可控 EventSource 替身：手动驱动 open / 具名事件帧 / error。 */
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

  emitOpen() {
    this.onopen?.call(this as unknown as EventSource, new Event('open'))
  }

  emit(event: ProgressEvent) {
    const msg = { data: JSON.stringify(event) } as MessageEvent
    for (const cb of this.listeners[event.type] ?? []) {
      cb(msg as Event)
    }
  }

  static reset() {
    FakeEventSource.instances = []
  }

  static get last(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1]
  }
}

function makeEvent(
  partial: Partial<ProgressEvent> & { type: ProgressEvent['type']; seq: number },
): ProgressEvent {
  return {
    session_id: 's1',
    agent: null,
    data: {},
    ts: Date.now(),
    ...partial,
  }
}

// ============ 测试用渲染宿主 ============

/**
 * 测试宿主：把 SSE 消费 hook 与真实展示组件串联，模拟主页面的单向数据流，
 * 使 SSE 事件能端到端驱动 UI 渲染。
 */
function StreamView({ sessionId }: { sessionId: string }) {
  const {
    agentList,
    report,
    error,
    connectionStatus,
    reconnectExhausted,
    reconnect,
  } = useAnalysisStream(sessionId)

  const interrupted =
    connectionStatus === 'interrupted' || connectionStatus === 'reconnecting'

  return (
    <div>
      <AgentBoard agents={agentList} />
      {report ? <HealthReport report={report} /> : null}
      {error ? (
        <p role="alert" data-testid="stream-error">
          {error}
        </p>
      ) : null}
      {interrupted ? (
        <p data-testid="interrupt-notice">连接中断，正在尝试重新连接…</p>
      ) : null}
      {reconnectExhausted ? (
        <button type="button" onClick={reconnect}>
          重新发起分析
        </button>
      ) : null}
    </div>
  )
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeEventSource.reset()
  vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// ============ 1. 四种事件类型的渲染 ============

describe('SSE Progress_Event 渲染（需求 10.7）', () => {
  it('thought 事件逐段追加渲染到对应 Agent（需求 5.4）', () => {
    render(<StreamView sessionId="s1" />)
    act(() => FakeEventSource.last.emitOpen())

    act(() => {
      FakeEventSource.last.emit(
        makeEvent({ type: 'agent_start', seq: 1, agent: 'Code_Auditor' }),
      )
      FakeEventSource.last.emit(
        makeEvent({
          type: 'thought',
          seq: 2,
          agent: 'Code_Auditor',
          data: { content: '分析', iteration: 1 },
        }),
      )
      FakeEventSource.last.emit(
        makeEvent({
          type: 'thought',
          seq: 3,
          agent: 'Code_Auditor',
          data: { content: '目录结构', iteration: 1 },
        }),
      )
    })

    // 增量按序拼接后整体渲染。
    expect(screen.getByTestId('thought-content')).toHaveTextContent('分析目录结构')
    // Agent 卡片存在且状态徽章为「执行中」。
    expect(screen.getByText('Code_Auditor')).toBeInTheDocument()
    expect(screen.getByTestId('agent-status')).toHaveTextContent('执行中')
  })

  it('tool_call / tool_result 显示工具名与截断后的摘要（需求 5.5）', () => {
    render(<StreamView sessionId="s1" />)
    act(() => FakeEventSource.last.emitOpen())

    const longSummary = 'x'.repeat(600)
    act(() => {
      FakeEventSource.last.emit(
        makeEvent({
          type: 'tool_call',
          seq: 1,
          agent: 'Code_Auditor',
          data: { tool: 'read_file', args: { path: 'main.py' } },
        }),
      )
      FakeEventSource.last.emit(
        makeEvent({
          type: 'tool_result',
          seq: 2,
          agent: 'Code_Auditor',
          data: { tool: 'read_file', summary: longSummary, truncated: true },
        }),
      )
    })

    // 工具名渲染。
    expect(screen.getByText('read_file')).toBeInTheDocument()
    // 摘要被截断到 500 字符 + 省略号，并标注「已截断」。
    const summary = screen.getByTestId('tool-call-summary')
    expect(summary.textContent!.length).toBeLessThanOrEqual(TOOL_SUMMARY_MAX + 1)
    expect(screen.getByText('已截断')).toBeInTheDocument()
  })

  it('final_report 事件渲染健康报告五部分（需求 6.4）', () => {
    render(<StreamView sessionId="s1" />)
    act(() => FakeEventSource.last.emitOpen())

    const report: HealthReportData = {
      metadata_summary: {
        stars: 1234,
        forks: 56,
        language_distribution: [
          { name: 'TypeScript', percent: 70 },
          { name: 'CSS', percent: 30 },
        ],
      },
      code_auditor: {
        strengths: ['模块划分清晰'],
        improvements: ['缺少单元测试'],
        summary: '整体结构良好',
      },
      product_value: {
        readme_clarity: ['README 结构完整'],
        practical_value: ['解决实际问题'],
        activeness: ['近月有提交'],
        summary: '产品价值较高',
      },
      recommendations: ['补充测试', '完善文档', '增加 CI'],
      score: 88,
    }

    act(() => {
      FakeEventSource.last.emit(
        makeEvent({
          type: 'final_report',
          seq: 1,
          data: report as unknown as Record<string, unknown>,
        }),
      )
    })

    // 五部分标题。
    expect(screen.getByText('元数据摘要')).toBeInTheDocument()
    expect(screen.getByText('代码审计意见')).toBeInTheDocument()
    expect(screen.getByText('产品价值意见')).toBeInTheDocument()
    expect(screen.getByText('综合优化建议')).toBeInTheDocument()
    // 第五部分：总分（环形进度，用 aria-label 定位）。
    expect(screen.getByRole('img', { name: '总分 88 分' })).toBeInTheDocument()

    // 各部分关键内容。
    expect(screen.getByText('1,234')).toBeInTheDocument() // Stars 整数展示
    expect(screen.getByText('TypeScript')).toBeInTheDocument()
    expect(screen.getByText('模块划分清晰')).toBeInTheDocument()
    expect(screen.getByText('缺少单元测试')).toBeInTheDocument()
    expect(screen.getByText('README 结构完整')).toBeInTheDocument()
    expect(screen.getByText('补充测试')).toBeInTheDocument()
  })

  it('final_report 缺失部分显示占位并保留已收到部分（需求 6.5）', () => {
    render(<StreamView sessionId="s1" />)
    act(() => FakeEventSource.last.emitOpen())

    // 仅含元数据摘要与总分，其余部分缺失。
    const partial = {
      metadata_summary: { stars: 10, forks: 2, language_distribution: [] },
      score: 60,
    }
    act(() => {
      FakeEventSource.last.emit(
        makeEvent({
          type: 'final_report',
          seq: 1,
          data: partial as unknown as Record<string, unknown>,
        }),
      )
    })

    // 已收到部分保留。
    expect(screen.getByText('10')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '总分 60 分' })).toBeInTheDocument()
    // 缺失部分显示占位提示。
    const placeholders = screen.getAllByText(/尚未生成或未收到/)
    expect(placeholders.length).toBeGreaterThan(0)
  })

  it('error 事件显示中断/失败提示（需求 5.7）', () => {
    render(<StreamView sessionId="s1" />)
    act(() => FakeEventSource.last.emitOpen())

    act(() => {
      FakeEventSource.last.emit(
        makeEvent({ type: 'agent_start', seq: 1, agent: 'Code_Auditor' }),
      )
      FakeEventSource.last.emit(
        makeEvent({ type: 'error', seq: 2, data: { message: '仓库抓取失败：资源不存在' } }),
      )
    })

    // 错误提示渲染。
    const alert = screen.getByTestId('stream-error')
    expect(alert).toHaveTextContent('仓库抓取失败：资源不存在')
    // 正在执行的 Agent 被标记为失败。
    expect(screen.getByTestId('agent-status')).toHaveTextContent('失败')
  })
})

// ============ 2. 断线判定与重连的 UI 反映（需求 8.5、8.6） ============

describe('SSE 连接中断与重连（需求 8.5、8.6）', () => {
  it('10s 无数据判定中断，自动重连最多 3 次、间隔 3s', () => {
    render(<StreamView sessionId="s1" />)
    act(() => FakeEventSource.last.emitOpen())

    const initialCount = FakeEventSource.instances.length

    for (let i = 1; i <= MAX_RECONNECT_ATTEMPTS; i++) {
      // 10s 无数据 → 判定中断，UI 显示中断提示。
      act(() => {
        vi.advanceTimersByTime(DATA_TIMEOUT_MS)
      })
      expect(screen.getByTestId('interrupt-notice')).toBeInTheDocument()

      // 3s 后触发第 i 次重连。
      act(() => {
        vi.advanceTimersByTime(RECONNECT_DELAY_MS)
      })
    }

    // 共建立 3 个新连接（≤3 次重连）。
    expect(FakeEventSource.instances.length).toBe(
      initialCount + MAX_RECONNECT_ATTEMPTS,
    )

    // 第 4 次中断：重连耗尽，保留中断提示与「重新发起分析」入口。
    act(() => {
      vi.advanceTimersByTime(DATA_TIMEOUT_MS)
    })
    expect(screen.getByTestId('interrupt-notice')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '重新发起分析' }),
    ).toBeInTheDocument()
  })

  it('收到数据后重置中断计时，不误判为中断', () => {
    render(<StreamView sessionId="s1" />)
    act(() => FakeEventSource.last.emitOpen())

    // 每 8s 收到一次心跳，未达 10s 阈值，不应判定中断。
    for (let i = 1; i <= 3; i++) {
      act(() => {
        vi.advanceTimersByTime(8_000)
        FakeEventSource.last.emit(makeEvent({ type: 'heartbeat', seq: i }))
      })
      expect(screen.queryByTestId('interrupt-notice')).not.toBeInTheDocument()
    }
  })
})

// ============ 3. 提交禁用/重新启用与 30s 超时（需求 1.6、1.7） ============

describe('RepoUrlForm 提交禁用与 30s 超时（需求 1.6、1.7）', () => {
  const VALID_URL = 'https://github.com/facebook/react'

  it('analyzing 为真时持续禁用提交控件（需求 1.6）', () => {
    render(<RepoUrlForm onSessionCreated={vi.fn()} analyzing />)
    expect(screen.getByRole('button')).toBeDisabled()
    expect(screen.getByRole('textbox')).toBeDisabled()
  })

  it('30s 无响应显示超时提示并重新启用提交控件（需求 1.7）', async () => {
    // fetch 永不 resolve，仅在 abort 时以 AbortError 拒绝，模拟无响应。
    const fetchMock = vi.fn(
      (_url: string, opts: { signal: AbortSignal }) =>
        new Promise<Response>((_resolve, reject) => {
          opts.signal.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'))
          })
        }),
    )
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)

    render(<RepoUrlForm onSessionCreated={vi.fn()} />)

    // 用 fireEvent 驱动，避免 userEvent 与假定时器的相互作用；
    // 不使用 findBy/waitFor（其内部轮询在假定时器下不会推进）。
    const textbox = screen.getByRole('textbox')
    fireEvent.change(textbox, { target: { value: VALID_URL } })
    await act(async () => {
      fireEvent.submit(textbox.closest('form')!)
    })

    // 请求进行中：提交控件被禁用（需求 1.6）。
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button')).toBeDisabled()

    // 推进 30s：内置 AbortController 触发超时，并 flush 随后的 promise 链。
    await act(async () => {
      vi.advanceTimersByTime(CREATE_ANALYSIS_TIMEOUT_MS)
    })
    await act(async () => {
      await Promise.resolve()
    })

    // 显示超时提示并重新启用（需求 1.7）。
    expect(screen.getByRole('alert')).toHaveTextContent('请求超时')
    expect(screen.getByRole('button', { name: '开始评估' })).toBeEnabled()
  })
})
