/**
 * EmptyState —— 统一空状态（挪用 artoo 的空态设计）。
 *
 * artoo 的空态形态：居中的圆角图标底座 + 一行说明文案 + 可选操作按钮。
 * 全站空态（体检落地页、模型管理无数据、历史无记录）统一走这里，保证观感一致。
 *
 * 纯展示组件，操作通过 action 上抛，不持有业务态。
 */

import { type ComponentType, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

export interface EmptyStateProps {
  /** 顶部图标（lucide 图标组件）。 */
  icon: ComponentType<{ className?: string }>
  /** 主标题。 */
  title: ReactNode
  /** 说明文案（次要）。 */
  description?: ReactNode
  /** 可选操作区（通常放一个按钮）。 */
  action?: ReactNode
  /** 附加类名。 */
  className?: string
}

/**
 * 渲染统一空状态。
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center px-6 py-20 text-center',
        className,
      )}
    >
      <div className="mb-4 flex size-16 items-center justify-center rounded-2xl bg-muted/60">
        <Icon className="size-8 text-muted-foreground/60" />
      </div>
      <p className="text-base font-medium text-foreground">{title}</p>
      {description ? (
        <p className="mt-1.5 max-w-md text-sm text-muted-foreground">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  )
}
