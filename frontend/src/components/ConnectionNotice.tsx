/**
 * ConnectionNotice —— 连接状态提示（美化版，替换原生硬卡片）。
 *
 * 两种状态：
 * - reconnecting（重连中）：柔和的琥珀色调 + 呼吸动画的加载环，传达「正在恢复」。
 * - exhausted（重连耗尽）：克制的错误色调 + 重试/重新发起入口。
 *
 * 纯展示 + 事件上抛，不持有业务态。相比原先生硬的 destructive 边框卡片，
 * 这里用渐变底、圆润图标徽章、动效与更清晰的层次，降低视觉冲击。
 */

import { motion } from 'motion/react'
import { Loader2, PlugZap, RefreshCw, RotateCcw } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

export interface ConnectionNoticeProps {
  /** true = 重连全部失败；false = 仍在自动重连中。 */
  exhausted: boolean
  onReconnect: () => void
  onRestart: () => void
  className?: string
}

export function ConnectionNotice({
  exhausted,
  onReconnect,
  onRestart,
  className,
}: ConnectionNoticeProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className={cn(
        'relative overflow-hidden rounded-xl border shadow-sm',
        exhausted
          ? 'border-destructive/20 bg-linear-to-br from-destructive/5 to-transparent'
          : 'border-amber-400/25 bg-linear-to-br from-amber-50/80 to-transparent dark:from-amber-500/10',
        className,
      )}
      data-testid="connection-notice"
    >
      <div className="flex flex-col items-center gap-4 px-6 py-8 text-center">
        {/* 图标徽章 */}
        <div
          className={cn(
            'relative flex size-14 items-center justify-center rounded-full',
            exhausted
              ? 'bg-destructive/10 text-destructive'
              : 'bg-amber-400/15 text-amber-600 dark:text-amber-400',
          )}
        >
          {exhausted ? (
            <PlugZap className="size-6" />
          ) : (
            <>
              {/* 呼吸光环 */}
              <motion.span
                className="absolute inset-0 rounded-full border-2 border-amber-400/40"
                animate={{ scale: [1, 1.35], opacity: [0.6, 0] }}
                transition={{ duration: 1.6, repeat: Infinity, ease: 'easeOut' }}
              />
              <Loader2 className="size-6 animate-spin" />
            </>
          )}
        </div>

        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-foreground">
            {exhausted ? '连接已中断' : '正在重新连接…'}
          </p>
          <p className="max-w-sm text-xs text-muted-foreground">
            {exhausted
              ? '多次自动重连均未成功。你可以再试一次，或重新发起体检。'
              : '与服务器的实时连接暂时中断，正在自动尝试恢复，请稍候。'}
          </p>
        </div>

        {exhausted ? (
          <div className="flex flex-wrap justify-center gap-2">
            <Button variant="outline" size="sm" onClick={onReconnect} className="gap-1.5">
              <RefreshCw className="size-3.5" />
              重新连接
            </Button>
            <Button size="sm" onClick={onRestart} className="gap-1.5">
              <RotateCcw className="size-3.5" />
              重新发起体检
            </Button>
          </div>
        ) : null}
      </div>
    </motion.div>
  )
}
