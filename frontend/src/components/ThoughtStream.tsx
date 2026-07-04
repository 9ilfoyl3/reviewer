/**
 * ThoughtStream —— Agent 思考文本的流式渲染（需求 5.4、8.2）。
 *
 * 思考缓冲由 useAnalysisStream 归约维护，本组件仅做展示：
 * - 用 streamdown 渲染 Markdown（挪用 artoo 的流式方案），执行中开启逐词淡入。
 * - 顶部为可点击的标题栏：脑图标 + 「思考过程」+ 轮次，点击整体折叠/展开。
 *   流式生成中会自动展开，保证用户能实时看到最新思考。
 * - 展开区左侧主色强调边，与工具调用区做视觉区分，让思考过程更醒目。
 * - 新增量到达时容器自动滚动到底部，跟随最新思考。
 * - 空文本时显示占位提示，避免出现空白块。
 *
 * 保留 data-testid（thought-content）与既有测试契约一致。
 */

import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { Brain, ChevronDown } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Markdown } from '@/components/Markdown'

export interface ThoughtStreamProps {
  /** 逐 token 追加后的完整思考文本。 */
  thought: string
  /** 当前 ReAct 轮次，用于顶部提示。 */
  iteration?: number
  /** 是否处于流式生成中（开启逐词淡入动画）。 */
  streaming?: boolean
  /** 附加类名。 */
  className?: string
}

/**
 * 渲染单个 Agent 的思考流。
 *
 * @param thought 思考文本（增量已在上游拼接）
 * @param iteration 当前轮次
 * @param streaming 是否流式生成中
 */
export function ThoughtStream({
  thought,
  iteration,
  streaming = false,
  className,
}: ThoughtStreamProps) {
  // 思考框自身的滚动视口。用内部滚动跟随最新思考，避免调用 scrollIntoView
  // 触发外层页面滚动（多个 Agent 同时输出时会导致页面上下跳动）。
  const viewportRef = useRef<HTMLDivElement | null>(null)
  // 默认展开，让用户第一眼就能看到思考过程；可手动折叠。
  const [open, setOpen] = useState(true)

  // 流式生成中强制展开，保证实时思考不被折叠遮挡。
  useEffect(() => {
    if (streaming) setOpen(true)
  }, [streaming])

  useEffect(() => {
    if (!open) return
    const el = viewportRef.current
    // 仅滚动本框自身，不影响外层页面。
    if (el) el.scrollTop = el.scrollHeight
  }, [thought, open])

  const hasThought = thought.trim().length > 0

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="group flex items-center justify-between gap-2 rounded-md text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <span className="flex items-center gap-1.5 font-medium">
          <ChevronDown
            className={cn(
              'size-3.5 transition-transform duration-200',
              !open && '-rotate-90',
            )}
          />
          <Brain className="size-3.5 text-primary/70" />
          思考过程
          {streaming && hasThought ? (
            <span className="size-1.5 animate-pulse rounded-full bg-primary" />
          ) : null}
        </span>
        {iteration ? (
          <span className="tabular-nums">第 {iteration} 轮</span>
        ) : null}
      </button>

      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            key="thought-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            className="overflow-hidden"
          >
            <div
              ref={viewportRef}
              className="max-h-56 overflow-y-auto rounded-lg border border-border/60 bg-muted/30 px-3 py-2.5"
            >
              {hasThought ? (
                <div
                  data-testid="thought-content"
                  className="text-sm leading-relaxed"
                >
                  <Markdown streaming={streaming}>{thought}</Markdown>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">等待思考输出…</p>
              )}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  )
}
