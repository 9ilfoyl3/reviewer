/**
 * 根组件：侧边栏布局 + 主区视图。
 *
 * 布局：左侧 Sidebar（挪用 artoo 样式，含「新的评估 / 模型管理」菜单按钮与评估历史），
 * 右侧主区按视图切换：
 *   - 新的评估（activeRecordId 为 null）：NewReview 落地页，输入仓库地址发起评估。
 *   - 评估详情（activeRecordId 有值）：ReviewDetail，发起后直接进入并流式展示
 *     多 Agent 过程，完成后在同一页渲染报告；点击历史记录亦进入此页回看。
 *   - 模型管理：Models 页。
 *
 * 数据流向清晰：App 持有「视图 / 选中记录 / 历史分组 / 刚发起的会话集合」这份
 * 单一数据源，向下单向下发；子页面通过回调上抛意图，由 App 统一处理与刷新。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Sidebar } from '@/components/Sidebar'
import { NewReview } from '@/pages/NewReview'
import { ReviewDetail } from '@/pages/ReviewDetail'
import { Models } from '@/pages/Models'
import { deleteHistory, fetchHistory, type HistoryGroup } from '@/lib/api'

type View = 'review' | 'models'

export default function App() {
  const [view, setView] = useState<View>('review')
  const [groups, setGroups] = useState<HistoryGroup[]>([])
  const [loadingHistory, setLoadingHistory] = useState(true)
  // 主区评估详情当前展示的记录 id（null 表示「新的评估」落地页）。
  const [activeRecordId, setActiveRecordId] = useState<string | null>(null)
  // 当前详情对应的仓库地址提示（详情尚未加载时用于头部展示）。
  const [activeRepoUrl, setActiveRepoUrl] = useState<string | undefined>()
  // 本次会话内「刚发起、需要实时流式」的记录 id 集合。
  const liveIdsRef = useRef<Set<string>>(new Set())

  const refreshHistory = useCallback(async () => {
    try {
      setGroups(await fetchHistory())
    } catch {
      // 历史加载失败不打断主流程，静默即可（侧边栏显示空态）。
    } finally {
      setLoadingHistory(false)
    }
  }, [])

  useEffect(() => {
    void refreshHistory()
  }, [refreshHistory])

  // 新的评估：回到落地页。
  const handleNewReview = useCallback(() => {
    setActiveRecordId(null)
    setActiveRepoUrl(undefined)
    setView('review')
  }, [])

  // 会话创建成功：标记为 live，直接进入该会话的评估详情流式展示。
  const handleSessionCreated = useCallback(
    (sessionId: string, repoUrl: string) => {
      liveIdsRef.current.add(sessionId)
      setActiveRepoUrl(repoUrl)
      setActiveRecordId(sessionId)
      setView('review')
      void refreshHistory()
    },
    [refreshHistory],
  )

  // 点击历史记录：进入其评估详情（进行中则续流，已完成则回看报告）。
  const handleSelectRecord = useCallback((id: string) => {
    setActiveRepoUrl(undefined)
    setActiveRecordId(id)
    setView('review')
  }, [])

  const handleDeleteRecord = useCallback(
    async (id: string) => {
      try {
        await deleteHistory(id)
        toast.success('已删除评估记录')
        liveIdsRef.current.delete(id)
        if (activeRecordId === id) {
          setActiveRecordId(null)
          setActiveRepoUrl(undefined)
          setView('review')
        }
        await refreshHistory()
      } catch {
        toast.error('删除失败')
      }
    },
    [activeRecordId, refreshHistory],
  )

  const handleOpenModels = useCallback(() => {
    setView('models')
  }, [])

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar
        groups={groups}
        loading={loadingHistory}
        view={view}
        activeRecordId={view === 'review' ? activeRecordId : null}
        onNewReview={handleNewReview}
        onSelectRecord={handleSelectRecord}
        onDeleteRecord={handleDeleteRecord}
        onOpenModels={handleOpenModels}
      />

      <main className="min-w-0 flex-1 overflow-y-auto">
        {view === 'models' ? (
          <Models />
        ) : activeRecordId ? (
          <ReviewDetail
            key={activeRecordId}
            recordId={activeRecordId}
            live={liveIdsRef.current.has(activeRecordId)}
            repoUrlHint={activeRepoUrl}
            onBack={handleNewReview}
            onHistoryChange={refreshHistory}
          />
        ) : (
          <NewReview onSessionCreated={handleSessionCreated} />
        )}
      </main>
    </div>
  )
}
