/**
 * ToolCallItem —— 展示单次工具调用及其结果摘要（需求 5.5）。
 *
 * 视觉挪用 artoo 的「步骤行」：轻底色圆角块 + 显示器图标 + 工具名（等宽字体），
 * 右侧状态点（调用中脉冲 / 已返回）。
 *
 * 结果摘要默认只显示一行（line-clamp-1），点击整行展开/收起全文，避免长摘要
 * 撑开卡片、淹没思考过程。摘要超过 500 字符时截断展示并标注（需求 5.5）。
 *
 * 纯展示组件（内部仅维护展开态），数据来源单一（ToolActivity）。
 * 保留 data-testid（tool-call-item / tool-call-summary）与既有测试契约一致。
 */

import { useState } from 'react'
import { ChevronDown, Monitor } from 'lucide-react'

import { cn } from '@/lib/utils'
import { TOOL_SUMMARY_MAX, truncateSummary } from '@/lib/sseParser'
import type { ToolActivity } from '@/hooks/useAnalysisStream'

export interface ToolCallItemProps {
  /** 一次工具调用及其结果。 */
  activity: ToolActivity
  /** 附加类名。 */
  className?: string
}

/** 将调用参数压缩为单行摘要，便于紧凑展示。 */
function formatArgs(args: Record<string, unknown>): string {
  const keys = Object.keys(args)
  if (keys.length === 0) return ''
  return keys.map((k) => `${k}=${formatValue(args[k])}`).join(', ')
}

function formatValue(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/**
 * 渲染一条工具调用记录。
 *
 * @param activity 工具调用及结果摘要
 */
export function ToolCallItem({ activity, className }: ToolCallItemProps) {
  const { tool, args, summary, truncated, completed } = activity
  const argText = formatArgs(args)
  // 防御性截断，保证展示文本不超过上限（>500 字符截断，需求 5.5）。
  const display = summary ? truncateSummary(summary) : ''
  const isTruncated =
    truncated || (summary != null && summary.length > TOOL_SUMMARY_MAX)

  // 结果摘要默认折叠为一行，仅在有摘要时可切换展开。
  const [expanded, setExpanded] = useState(false)
  const canToggle = completed

  return (
    <div
      className={cn(
        'rounded-lg border border-border/60 bg-background/60 px-3 py-2 text-sm',
        className,
      )}
      data-testid="tool-call-item"
    >
      <button
        type="button"
        onClick={canToggle ? () => setExpanded((v) => !v) : undefined}
        aria-expanded={canToggle ? expanded : undefined}
        className={cn(
          'flex w-full items-center gap-2 text-left',
          canToggle && 'cursor-pointer',
        )}
      >
        {canToggle ? (
          <ChevronDown
            className={cn(
              'size-3.5 shrink-0 text-muted-foreground transition-transform duration-200',
              !expanded && '-rotate-90',
            )}
          />
        ) : (
          <Monitor className="size-3.5 shrink-0 text-primary/70" />
        )}
        <span className="font-mono text-xs font-medium text-foreground/80">
          {tool || '未知工具'}
        </span>
        {argText ? (
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-primary/80">
            {argText}
          </span>
        ) : (
          <span className="flex-1" />
        )}
        <span
          className={cn(
            'shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium',
            completed
              ? 'bg-primary/10 text-primary'
              : 'bg-muted text-muted-foreground',
          )}
        >
          {completed ? '已返回' : (
            <span className="inline-flex items-center gap-1">
              <span className="size-1.5 animate-pulse rounded-full bg-current" />
              调用中
            </span>
          )}
        </span>
        {isTruncated ? (
          <span className="shrink-0 rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground">
            已截断
          </span>
        ) : null}
      </button>

      {completed ? (
        <p
          className={cn(
            'mt-1.5 break-words text-xs leading-relaxed text-muted-foreground',
            expanded ? 'whitespace-pre-wrap' : 'line-clamp-1',
          )}
          data-testid="tool-call-summary"
        >
          {display || '（无结果摘要）'}
        </p>
      ) : null}
    </div>
  )
}
