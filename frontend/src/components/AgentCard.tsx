/**
 * AgentCard —— 单个 Agent 的实时视图（需求 5.4、5.5、8.2、8.3、8.4）。
 *
 * 视觉挪用 artoo 的「步骤面板」语言：左侧圆角图标底座 + 角色中文名/职责，
 * 右侧状态徽章；下方是思考流与工具调用步骤。执行中卡片描边做柔和脉冲，
 * 状态徽章切换用 AnimatePresence 淡入淡出，工具项依次入场。
 *
 * 纯展示组件，数据来源单一（AgentView），状态由上游 useAnalysisStream 归约。
 * 保留 data-testid（agent-card / agent-status）与既有测试契约一致。
 */

import { useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { CheckCircle2, ChevronDown, Loader2, Wrench, XCircle } from 'lucide-react'

import { cn } from '@/lib/utils'
import { agentMeta } from '@/lib/agentMeta'
import { ThoughtStream } from '@/components/ThoughtStream'
import { ToolCallItem } from '@/components/ToolCallItem'
import type { AgentStatus, AgentView } from '@/hooks/useAnalysisStream'

export interface AgentCardProps {
  /** 单个 Agent 的归约视图。 */
  agent: AgentView
  /** 附加类名。 */
  className?: string
}

/** 状态徽章的文案、配色与图标映射（需求 8.3，绿色主题挪用 artoo）。 */
const STATUS_META: Record<
  AgentStatus,
  { label: string; className: string; icon: typeof Loader2 | null; spin?: boolean }
> = {
  waiting: {
    label: '等待中',
    className: 'bg-muted text-muted-foreground',
    icon: null,
  },
  running: {
    label: '执行中',
    className: 'bg-primary/10 text-primary',
    icon: Loader2,
    spin: true,
  },
  completed: {
    label: '已完成',
    className: 'bg-primary/10 text-primary',
    icon: CheckCircle2,
  },
  failed: {
    label: '失败',
    className: 'bg-destructive/10 text-destructive',
    icon: XCircle,
  },
}

/** 执行中时卡片描边的柔和脉冲，用于强化「进行中」反馈。 */
const RUNNING_PULSE = {
  boxShadow: [
    '0 0 0 0 color-mix(in oklab, var(--primary) 35%, transparent)',
    '0 0 0 5px color-mix(in oklab, var(--primary) 0%, transparent)',
  ],
}

/**
 * 渲染单个 Agent 卡片。
 *
 * @param agent Agent 视图（角色名 / 状态 / 思考 / 工具调用）
 */
export function AgentCard({ agent, className }: AgentCardProps) {
  const meta = agentMeta(agent.name)
  const status = STATUS_META[agent.status]
  const isRunning = agent.status === 'running'
  const Icon = meta.icon
  const StatusIcon = status.icon
  // 工具调用分组默认展开，可整体收起，避免长列表淹没思考过程。
  const [toolsOpen, setToolsOpen] = useState(true)

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
    >
      <motion.div
        className={cn('rounded-xl border border-border bg-card', className)}
        data-testid="agent-card"
        data-status={agent.status}
        animate={isRunning ? RUNNING_PULSE : { boxShadow: '0 0 0 0 transparent' }}
        transition={
          isRunning
            ? { duration: 1.8, repeat: Infinity, ease: 'easeOut' }
            : { duration: 0.3 }
        }
      >
        {/* 头部：图标底座 + 角色名/职责 + 状态徽章 */}
        <div className="flex items-center gap-3 px-4 py-3.5">
          <div
            className={cn(
              'flex size-9 shrink-0 items-center justify-center rounded-lg transition-colors',
              agent.status === 'failed'
                ? 'bg-destructive/10 text-destructive'
                : 'bg-primary/10 text-primary',
            )}
          >
            <Icon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-foreground">
                {meta.label}
              </span>
              <span className="truncate text-xs text-muted-foreground/70">
                {agent.name}
              </span>
            </div>
            {meta.description ? (
              <p className="truncate text-xs text-muted-foreground">
                {meta.description}
              </p>
            ) : null}
          </div>

          <AnimatePresence mode="wait" initial={false}>
            <motion.span
              key={agent.status}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
              className={cn(
                'inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium',
                status.className,
              )}
              data-testid="agent-status"
            >
              {StatusIcon ? (
                <StatusIcon
                  className={cn('size-3', status.spin && 'animate-spin')}
                />
              ) : null}
              {status.label}
            </motion.span>
          </AnimatePresence>
        </div>

        {/* 主体：思考流 + 工具调用步骤 */}
        <div className="flex flex-col gap-3 border-t border-border/60 px-4 py-3.5">
          <ThoughtStream
            thought={agent.thought}
            iteration={agent.iteration}
            streaming={isRunning}
          />

          {agent.tools.length > 0 ? (
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => setToolsOpen((v) => !v)}
                aria-expanded={toolsOpen}
                className="group flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
              >
                <ChevronDown
                  className={cn(
                    'size-3.5 transition-transform duration-200',
                    !toolsOpen && '-rotate-90',
                  )}
                />
                <Wrench className="size-3.5 text-primary/70" />
                工具调用
                <span className="rounded-full bg-muted px-1.5 text-[10px] tabular-nums text-muted-foreground">
                  {agent.tools.length}
                </span>
              </button>

              <AnimatePresence initial={false}>
                {toolsOpen ? (
                  <motion.div
                    key="tools-body"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2, ease: 'easeOut' }}
                    className="flex flex-col gap-2 overflow-hidden"
                  >
                    {agent.tools.map((tool, idx) => (
                      <motion.div
                        key={`${tool.tool}-${idx}`}
                        initial={{ opacity: 0, x: -8 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 8 }}
                        transition={{ duration: 0.25, ease: 'easeOut' }}
                      >
                        <ToolCallItem activity={tool} />
                      </motion.div>
                    ))}
                  </motion.div>
                ) : null}
              </AnimatePresence>
            </div>
          ) : null}
        </div>
      </motion.div>
    </motion.div>
  )
}
