/**
 * AgentBoard —— 多 Agent 进度看板（需求 5.4、5.5、8.3、8.4）。
 *
 * 以纵向列表（对话式）呈现各 Agent 卡片，按首次出现顺序渲染，营造与 artoo
 * 对话流一致的「逐段生成」体验。数据来源单一（agentList 由 useAnalysisStream
 * 归约），单向下发到 AgentCard。会话未产生任何 Agent 时显示占位提示。
 *
 * 保留 data-testid（agent-board / agent-board-empty）与既有测试契约一致。
 */

import { Loader2 } from 'lucide-react'

import { cn } from '@/lib/utils'
import { AgentCard } from '@/components/AgentCard'
import type { AgentView } from '@/hooks/useAnalysisStream'

export interface AgentBoardProps {
  /** Agent 视图数组（按首次出现顺序）。 */
  agents: AgentView[]
  /** 附加类名。 */
  className?: string
}

/**
 * 渲染 Agent 进度看板（对话式纵向列表）。
 *
 * @param agents Agent 视图列表
 */
export function AgentBoard({ agents, className }: AgentBoardProps) {
  if (agents.length === 0) {
    return (
      <div
        className={cn(
          'flex items-center justify-center gap-2 rounded-xl border border-dashed border-border bg-muted/20 px-4 py-12 text-sm text-muted-foreground',
          className,
        )}
        data-testid="agent-board-empty"
      >
        <Loader2 className="size-4 animate-spin" />
        等待 Agent 启动…
      </div>
    )
  }

  return (
    <div
      className={cn('flex flex-col gap-4', className)}
      data-testid="agent-board"
    >
      {agents.map((agent) => (
        <AgentCard key={agent.name} agent={agent} />
      ))}
    </div>
  )
}
