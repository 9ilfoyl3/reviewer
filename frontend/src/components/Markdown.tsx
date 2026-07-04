/**
 * Markdown —— 基于 streamdown 的 Markdown 渲染（挪用 artoo 的流式渲染方案）。
 *
 * - 静态文本：``streaming={false}``，一次性渲染。
 * - 流式文本：``streaming``，配合 blurIn 逐词淡入动画，营造实时生成感（与 artoo 对话一致）。
 * - 中日韩排版由 ``@streamdown/cjk`` 插件处理。
 *
 * prose 排版类挪用 artoo（对话气泡与切片视图同款），保证观感一致。
 */

import { Streamdown } from 'streamdown'
import { cjk } from '@streamdown/cjk'

import { cn } from '@/lib/utils'

/** 流式渲染动画配置（挪用 artoo）：模糊渐入、按词、放慢节奏。 */
const STREAM_ANIMATION = {
  animation: 'blurIn',
  sep: 'word',
  duration: 300,
  stagger: 20,
  easing: 'ease-out',
} as const

export interface MarkdownProps {
  /** Markdown 文本。 */
  children: string
  /** 是否处于流式生成中（开启逐词淡入动画）。 */
  streaming?: boolean
  /** 附加类名（作用于 prose 容器）。 */
  className?: string
}

/**
 * 渲染 Markdown 文本。
 *
 * @param children Markdown 源文本
 * @param streaming 是否流式渲染
 */
export function Markdown({ children, streaming = false, className }: MarkdownProps) {
  return (
    <div
      className={cn(
        'prose prose-sm max-w-none dark:prose-invert',
        '[&>p]:mb-2 [&>p:last-child]:mb-0',
        '[&_table]:text-xs [&_table]:w-full [&_table]:border-collapse',
        '[&_td]:border [&_td]:border-border/50 [&_td]:px-2.5 [&_td]:py-1.5',
        '[&_th]:border [&_th]:border-border/50 [&_th]:px-2.5 [&_th]:py-1.5 [&_th]:bg-muted/40 [&_th]:font-medium',
        className,
      )}
    >
      <Streamdown
        mode={streaming ? 'streaming' : 'static'}
        plugins={{ cjk: cjk }}
        isAnimating={streaming}
        animated={STREAM_ANIMATION}
      >
        {children}
      </Streamdown>
    </div>
  )
}
