/**
 * AgentBoardSkeleton —— 加载 / 连接中断时的骨架占位（需求 8.2）。
 *
 * 用 shadcn `Skeleton`（animate-pulse）呈现 Agent 卡片的骨架轮廓，
 * 在会话尚未产生任何事件、或 SSE 连接中断重连期间提供平滑的等待反馈，
 * 仅强化状态反馈、不干扰阅读。纯展示组件，无内部状态。
 */

import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/skeleton'
import { Card, CardContent, CardHeader } from '@/components/ui/card'

export interface AgentBoardSkeletonProps {
  /** 骨架卡片数量，默认 3（对应三个 Agent 角色）。 */
  count?: number
  /** 顶部提示文案，例如「等待 Agent 启动…」或「连接中断，重连中…」。 */
  label?: string
  /** 附加类名。 */
  className?: string
}

/** 单个 Agent 卡片骨架。 */
function AgentCardSkeleton() {
  return (
    <Card className="flex flex-col" data-testid="agent-card-skeleton">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-5 w-14 rounded-full" />
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <Skeleton className="h-32 w-full rounded-md" />
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-12 w-full rounded-md" />
      </CardContent>
    </Card>
  )
}

/**
 * 渲染 Agent 看板骨架。
 *
 * @param count 骨架卡片数量
 * @param label 顶部提示文案
 */
export function AgentBoardSkeleton({
  count = 3,
  label,
  className,
}: AgentBoardSkeletonProps) {
  return (
    <div className={cn('flex flex-col gap-4', className)} data-testid="agent-board-skeleton">
      {label ? (
        <p className="text-center text-sm text-muted-foreground">{label}</p>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: count }).map((_, idx) => (
          <AgentCardSkeleton key={idx} />
        ))}
      </div>
    </div>
  )
}
