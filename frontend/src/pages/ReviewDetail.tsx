/**
 * ReviewDetail —— 单次评估详情（统一「流式过程 + 报告」，对话式体验）。
 *
 * 这是「新评估发起后直接进入的详情页」，也是「点击历史记录回看的详情页」：
 * - 进行中 / 刚发起（live）：订阅 SSE，实时流式展示各 Agent 的思考与工具调用，
 *   分析完成后在同一页内直接渲染健康评估报告（就和流式对话的体验一致）。
 * - 已完成 / 失败的历史记录：直接从历史详情读取并渲染报告 / 失败原因。
 *
 * 数据流向清晰：会话/记录 id 为单一入口，是否流式由「live 或记录状态」推导；
 * 报告优先取流式结果，回退到历史存储，单向下发到子组件。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  ArrowLeft,
  CheckCircle2,
  Clock,
  GitBranch,
  Loader2,
  TriangleAlert,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { AgentBoard } from '@/components/AgentBoard'
import { HealthReport } from '@/components/HealthReport'
import { ConnectionNotice } from '@/components/ConnectionNotice'
import { useAnalysisStream } from '@/hooks/useAnalysisStream'
import { fetchHistoryDetail, type HistoryDetail } from '@/lib/api'
import { cn } from '@/lib/utils'

export interface ReviewDetailProps {
  /** 会话/历史记录 id（二者同源）。 */
  recordId: string
  /** 是否为刚发起、需要实时流式的会话。 */
  live: boolean
  /** 仓库地址提示（详情尚未加载时用于头部展示）。 */
  repoUrlHint?: string
  /** 返回「新的评估」落地页。 */
  onBack: () => void
  /** 会话状态变化时刷新侧边栏历史。 */
  onHistoryChange?: () => void
}

/** 头部状态徽章。 */
function StatusPill({
  status,
  score,
}: {
  status: string
  score?: number | null
}) {
  const map: Record<
    string,
    { label: string; className: string; icon: typeof Clock }
  > = {
    queued: { label: '排队中', className: 'bg-muted text-muted-foreground', icon: Clock },
    running: { label: '分析中', className: 'bg-primary/10 text-primary', icon: Loader2 },
    completed: {
      label: typeof score === 'number' ? `已完成 · ${score} 分` : '已完成',
      className: 'bg-primary/10 text-primary',
      icon: CheckCircle2,
    },
    failed: { label: '失败', className: 'bg-destructive/10 text-destructive', icon: TriangleAlert },
  }
  const meta = map[status] ?? map.queued
  const Icon = meta.icon
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
        meta.className,
      )}
    >
      <Icon className={cn('size-3.5', status === 'running' && 'animate-spin')} />
      {meta.label}
    </span>
  )
}

export function ReviewDetail({
  recordId,
  live,
  repoUrlHint,
  onBack,
  onHistoryChange,
}: ReviewDetailProps) {
  const [detail, setDetail] = useState<HistoryDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(true)

  // 拉取历史详情（头部信息 + 已存报告 + 记录状态 + 落库的多 Agent 过程）。
  const reloadDetail = useCallback(async () => {
    try {
      const d = await fetchHistoryDetail(recordId)
      setDetail(d)
    } catch {
      // 详情拉取失败不阻断流式（live 会话仍可直接订阅 SSE）。
    }
  }, [recordId])

  useEffect(() => {
    let disposed = false
    setLoadingDetail(true)
    setDetail(null)
    fetchHistoryDetail(recordId)
      .then((d) => {
        if (!disposed) setDetail(d)
      })
      .catch(() => {})
      .finally(() => {
        if (!disposed) setLoadingDetail(false)
      })
    return () => {
      disposed = true
    }
  }, [recordId])

  // 是否建立 SSE 连接：
  // - live（刚发起）：始终建流，直到收到终态事件后 hook 自行关闭（完整收齐过程）；
  // - 非 live（历史回看）：仅当记录仍在排队/进行中才续流，已完成/失败直接看落库报告。
  const connect =
    live || detail?.status === 'running' || detail?.status === 'queued'

  const {
    sessionStatus,
    connectionStatus,
    agentList,
    report: streamReport,
    error: streamError,
    reconnectExhausted,
    reconnect,
  } = useAnalysisStream(connect ? recordId : null)

  const streamTerminal =
    sessionStatus === 'completed' || sessionStatus === 'failed'
  // 正在流式（用于是否展示连接提示 / 占位看板）：已建流且尚未进入终态。
  const streaming = connect && !streamTerminal

  const report = streamReport ?? detail?.report ?? null
  const interrupted =
    streaming &&
    (connectionStatus === 'interrupted' || connectionStatus === 'reconnecting')

  // 头部状态：live 用会话实时状态（未建流时回退记录状态），历史回看用记录状态。
  const headerStatus = live
    ? sessionStatus === 'idle'
      ? detail?.status ?? 'queued'
      : sessionStatus
    : detail?.status ?? 'queued'

  const errorMessage =
    streamError ?? (detail?.status === 'failed' ? detail?.error ?? null : null)

  // 会话状态变化时刷新侧边栏历史（状态/分数实时同步）。
  useEffect(() => {
    onHistoryChange?.()
  }, [sessionStatus, onHistoryChange])

  // 流式结束（completed/failed）后，稍候回拉一次详情：拿到落库的报告与多 Agent 过程，
  // 保证「SSE 晚连接漏收过程」等情况下也能还原完整面板，并与刷新后保持一致。
  useEffect(() => {
    if (sessionStatus !== 'completed' && sessionStatus !== 'failed') return
    const t = setTimeout(() => {
      void reloadDetail()
    }, 800)
    return () => clearTimeout(t)
  }, [sessionStatus, reloadDetail])

  // ============ 连接反馈（Toast，需求 8.5、8.6） ============
  const notifiedInterruptRef = useRef(false)
  const notifiedExhaustedRef = useRef(false)

  useEffect(() => {
    if (connectionStatus === 'open' || connectionStatus === 'connecting') {
      notifiedInterruptRef.current = false
    }
  }, [connectionStatus])

  useEffect(() => {
    if (interrupted && !notifiedInterruptRef.current) {
      notifiedInterruptRef.current = true
      toast.warning('连接中断，正在尝试重新连接…')
    }
  }, [interrupted])

  useEffect(() => {
    if (reconnectExhausted && !notifiedExhaustedRef.current) {
      notifiedExhaustedRef.current = true
      toast.error('连接已中断，多次重连失败', {
        action: { label: '重新连接', onClick: () => reconnect() },
      })
    }
  }, [reconnectExhausted, reconnect])

  // 报告首次出现时：平滑滚动到底部，让报告顺畅进入视野。
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const hadReportRef = useRef(false)
  useEffect(() => {
    if (report && !hadReportRef.current) {
      hadReportRef.current = true
      // 等报告完成入场动画后再滚动，过渡更顺滑。
      const t = setTimeout(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
      }, 120)
      return () => clearTimeout(t)
    }
    if (!report) hadReportRef.current = false
  }, [report])

  const repoTitle = useMemo(() => {
    if (detail) return `${detail.owner}/${detail.repo}`
    if (repoUrlHint) return repoUrlHint.replace(/^https?:\/\/github\.com\//i, '')
    return '评估详情'
  }, [detail, repoUrlHint])

  // 展示用的 Agent 过程：优先用实时归约的 agentList（流式中/刚完成都保留），
  // 为空时回退到落库聚合的过程（历史回看、SSE 晚连接漏收过程等）。
  const displayAgents = agentList.length > 0 ? agentList : detail?.agents ?? []
  const showAgents = displayAgents.length > 0 || streaming
  const showReport = report !== null

  return (
    <div className="flex min-h-full flex-col">
      {/* 头部：全宽固定置顶，无分割线 */}
      <div className="sticky top-0 z-10 bg-background">
        <div className="flex items-center gap-3 px-4 py-3 sm:px-6">
          <Button variant="ghost" size="icon" onClick={onBack} title="返回">
            <ArrowLeft className="size-4" />
          </Button>
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <GitBranch className="size-5" />
          </div>
          <div className="flex min-w-0 flex-1 flex-col">
            <h1 className="truncate text-lg font-semibold tracking-tight">
              {repoTitle}
            </h1>
            {detail?.created_at ? (
              <span className="text-xs text-muted-foreground">
                {new Date(detail.created_at).toLocaleString('zh-CN')}
              </span>
            ) : null}
          </div>
          <StatusPill status={headerStatus} score={detail?.score ?? report?.score} />
        </div>
      </div>

      {/* 内容区：居中限宽 */}
      <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6">
        {/* 连接中断 / 重连耗尽提示 */}
        {streaming && (interrupted || reconnectExhausted) ? (
          <ConnectionNotice
            exhausted={reconnectExhausted}
            onReconnect={() => reconnect()}
            onRestart={onBack}
          />
        ) : null}

        {/* 多 Agent 协作过程（流式实时 / 回看还原） */}
        {showAgents ? (
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-medium text-muted-foreground">
              多 Agent 协作过程
            </h2>
            <AgentBoard agents={displayAgents} />
          </section>
        ) : null}

        {/* 报告 */}
        {showReport ? (
          <HealthReport report={report} />
        ) : errorMessage ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 py-16 text-center text-sm text-muted-foreground">
              <TriangleAlert className="size-6 text-destructive" />
              这次评估失败了：{errorMessage}
            </CardContent>
          </Card>
        ) : !streaming && !loadingDetail ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 py-16 text-center text-sm text-muted-foreground">
              <TriangleAlert className="size-6 text-muted-foreground" />
              这次评估尚未完成，暂无报告。
            </CardContent>
          </Card>
        ) : loadingDetail && !streaming ? (
          <div className="flex items-center justify-center gap-2 py-20 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            加载中…
          </div>
        ) : null}

        {/* 滚动锚点：报告出现时平滑滚动到此处 */}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
