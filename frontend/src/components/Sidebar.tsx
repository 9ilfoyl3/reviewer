/**
 * Sidebar —— 左侧边栏（完整挪用 artoo 的侧边栏样式与结构）。
 *
 * 组成（对齐 artoo Layout 侧边栏）：
 * - 顶部：品牌标题「Reviewer」（衬线字体，占据整行）+ 收起/展开切换。
 * - 菜单按钮区：「新的评估」与「模型管理」均为菜单按钮（artoo NavLink 风格，高亮当前项）。
 * - 评估历史：按仓库分组的历史列表（每个仓库一段独立历史），空态挪用 artoo。
 *
 * 收起/展开有宽度过渡动画（挪用 artoo：单一 aside + transition-[width]，展开层与
 * 收起层叠放并做透明度切换）。侧边栏 h-screen 固定，仅右侧主区滚动。
 *
 * 纯展示 + 事件上抛：数据（历史分组、当前选中）由父级持有并下发，
 * 用户操作通过回调上抛，保持单向数据流。
 */

import { useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { ChevronDown, Cpu, GitBranch, History, PanelLeft, SquarePen, Trash2 } from 'lucide-react'

import { cn } from '@/lib/utils'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { HistoryGroup } from '@/lib/api'

export type SidebarView = 'review' | 'models'

export interface SidebarProps {
  groups: HistoryGroup[]
  loading: boolean
  view: SidebarView
  activeRecordId: string | null
  onNewReview: () => void
  onSelectRecord: (id: string) => void
  onDeleteRecord: (id: string) => void
  onOpenModels: () => void
}

/** 状态点的颜色映射（绿色主题挪用 artoo）。 */
const STATUS_DOT: Record<string, string> = {
  queued: 'bg-muted-foreground',
  running: 'bg-primary animate-pulse',
  completed: 'bg-primary',
  failed: 'bg-destructive',
}

function statusLabel(status: string, score?: number | null): string {
  switch (status) {
    case 'completed':
      return typeof score === 'number' ? `${score} 分` : '已完成'
    case 'running':
      return '进行中'
    case 'failed':
      return '失败'
    default:
      return '排队中'
  }
}

/** 单个仓库分组（可折叠）。 */
function RepoGroup({
  group,
  activeRecordId,
  onSelectRecord,
  onDeleteRecord,
}: {
  group: HistoryGroup
  activeRecordId: string | null
  onSelectRecord: (id: string) => void
  onDeleteRecord: (id: string) => void
}) {
  const [open, setOpen] = useState(true)

  return (
    <div className="flex flex-col">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group flex items-center gap-2 rounded-lg px-3 py-1.5 text-left text-[13px] font-medium text-sidebar-foreground/85 transition-colors hover:bg-sidebar-accent/60"
      >
        <GitBranch className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="flex-1 truncate" title={`${group.owner}/${group.repo}`}>
          {group.owner}/{group.repo}
        </span>
        <ChevronDown
          className={cn(
            'size-3.5 shrink-0 text-muted-foreground transition-transform',
            open ? '' : '-rotate-90',
          )}
        />
      </button>

      <AnimatePresence initial={false}>
        {open ? (
          <motion.ul
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            className="ml-3.5 overflow-hidden border-l border-sidebar-border pl-2"
          >
            {group.records.map((rec) => {
              const active = rec.id === activeRecordId
              return (
                <li key={rec.id}>
                  <div
                    className={cn(
                      'group/item flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs transition-colors',
                      active
                        ? 'bg-sidebar-primary font-medium text-sidebar-primary-foreground'
                        : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => onSelectRecord(rec.id)}
                      className="flex flex-1 items-center gap-2 text-left"
                    >
                      <span
                        className={cn(
                          'size-1.5 shrink-0 rounded-full',
                          active
                            ? 'bg-sidebar-primary-foreground/80'
                            : STATUS_DOT[rec.status] ?? 'bg-muted-foreground',
                        )}
                      />
                      <span className="flex-1 truncate">
                        {new Date(rec.created_at).toLocaleString('zh-CN', {
                          month: '2-digit',
                          day: '2-digit',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                      <span className="shrink-0 tabular-nums">
                        {statusLabel(rec.status, rec.score)}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => onDeleteRecord(rec.id)}
                      className={cn(
                        'shrink-0 rounded p-0.5 opacity-0 transition-opacity group-hover/item:opacity-100',
                        active
                          ? 'text-sidebar-primary-foreground/70 hover:text-sidebar-primary-foreground'
                          : 'text-muted-foreground hover:text-destructive',
                      )}
                      title="删除这条记录"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                </li>
              )
            })}
          </motion.ul>
        ) : null}
      </AnimatePresence>
    </div>
  )
}

/** 菜单按钮（artoo NavLink 风格）。 */
function MenuButton({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: typeof SquarePen
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
        active
          ? 'bg-sidebar-primary text-sidebar-primary-foreground'
          : 'text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground',
      )}
    >
      <Icon className="size-4" />
      {label}
    </button>
  )
}

export function Sidebar({
  groups,
  loading,
  view,
  activeRecordId,
  onNewReview,
  onSelectRecord,
  onDeleteRecord,
  onOpenModels,
}: SidebarProps) {
  const [open, setOpen] = useState(true)

  const newReviewActive = view === 'review' && activeRecordId === null
  const modelsActive = view === 'models'

  return (
    <aside
      className={cn(
        'relative flex h-screen shrink-0 flex-col overflow-hidden border-r border-sidebar-border bg-sidebar transition-[width] duration-200 ease-in-out',
        open ? 'w-60' : 'w-12',
      )}
    >
      {/* 展开态内容 */}
      <div
        className={cn(
          'flex h-full w-60 flex-col transition-opacity duration-200',
          open ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
      >
        {/* 品牌 + 收起 */}
        <div className="flex items-center justify-between px-4 py-3">
          <h1 className="font-serif text-lg font-semibold text-sidebar-foreground">
            Reviewer
          </h1>
          <button
            type="button"
            onClick={() => setOpen(false)}
            title="收起侧边栏"
            className="flex size-7 items-center justify-center rounded-md text-sidebar-foreground/60 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"
          >
            <PanelLeft className="size-4" />
          </button>
        </div>

        {/* 菜单按钮：新的评估 + 模型管理 */}
        <div className="space-y-1 px-3 pb-2 pt-1">
          <MenuButton
            icon={SquarePen}
            label="新的评估"
            active={newReviewActive}
            onClick={onNewReview}
          />
          <MenuButton
            icon={Cpu}
            label="模型管理"
            active={modelsActive}
            onClick={onOpenModels}
          />
        </div>

        {/* 评估历史 */}
        <p className="px-4 pb-1 pt-2 text-xs font-medium text-sidebar-foreground/85">
          评估历史
        </p>
        <ScrollArea className="flex-1 px-2">
          <div className="flex flex-col gap-0.5 py-1">
            {loading ? (
              <p className="px-2 py-4 text-center text-xs text-muted-foreground">
                加载中…
              </p>
            ) : groups.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <History className="mb-2 size-6 opacity-20" />
                <p className="text-xs">还没有评估记录</p>
                <p className="text-xs">发起一次评估开始吧</p>
              </div>
            ) : (
              groups.map((g) => (
                <RepoGroup
                  key={`${g.owner}/${g.repo}`}
                  group={g}
                  activeRecordId={activeRecordId}
                  onSelectRecord={onSelectRecord}
                  onDeleteRecord={onDeleteRecord}
                />
              ))
            )}
          </div>
        </ScrollArea>
      </div>

      {/* 收起态图标栏（叠放，透明度切换） */}
      <div
        className={cn(
          'absolute inset-0 flex h-full w-12 flex-col items-center transition-opacity duration-200',
          open ? 'pointer-events-none opacity-0' : 'opacity-100',
        )}
      >
        <button
          type="button"
          onClick={() => setOpen(true)}
          title="展开侧边栏"
          className="group mt-3 flex size-8 items-center justify-center rounded-md transition-colors hover:bg-sidebar-accent"
        >
          <span className="font-serif text-lg font-semibold text-sidebar-foreground group-hover:hidden">
            R
          </span>
          <PanelLeft className="hidden size-4 text-sidebar-foreground group-hover:block" />
        </button>
        <div className="mt-4 flex flex-col items-center gap-1">
          <button
            type="button"
            onClick={onNewReview}
            title="新的评估"
            className={cn(
              'flex size-8 items-center justify-center rounded-md transition-colors',
              newReviewActive
                ? 'bg-sidebar-primary text-sidebar-primary-foreground'
                : 'text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground',
            )}
          >
            <SquarePen className="size-4" />
          </button>
          <button
            type="button"
            onClick={onOpenModels}
            title="模型管理"
            className={cn(
              'flex size-8 items-center justify-center rounded-md transition-colors',
              modelsActive
                ? 'bg-sidebar-primary text-sidebar-primary-foreground'
                : 'text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground',
            )}
          >
            <Cpu className="size-4" />
          </button>
        </div>
      </div>
    </aside>
  )
}
