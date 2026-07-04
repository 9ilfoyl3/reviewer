/**
 * HealthReport —— 健康体检报告卡片（需求 6.4、6.5、6.6）。
 *
 * 渲染 Health_Report 的五个部分：
 *   1. 元数据摘要（Star/Fork 整数 + 语言分布占比列表）
 *   2. 代码审计意见（优点 / 改进点 / 摘要）
 *   3. 产品价值意见（README 清晰度 / 实用价值 / 活跃度 / 摘要）
 *   4. 综合优化建议（3–10 条）
 *   5. 总分（0–100 环形进度）
 *
 * 缺任一部分时该部分显示占位提示，并保留已成功接收的其它部分（需求 6.5）。
 * Star/Fork 以整数展示；语言分布归一化为「语言名 + 占比」列表且占比之和为
 * 100%（四舍五入补偿，需求 6.6），归一化逻辑抽取到 lib/languageStats.ts。
 */

import { type ComponentType, type ReactNode, useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import gsap from 'gsap'
import { BarChart3, Lightbulb, ScanSearch, Sparkles } from 'lucide-react'

import { CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Markdown } from '@/components/Markdown'
import { cn } from '@/lib/utils'
import { normalizeLanguageDistribution } from '@/lib/languageStats'
import type {
  CodeAuditorOpinion,
  HealthReport as HealthReportData,
  MetadataSummary,
  ProductValueOpinion,
} from '@/types/events'

// ============ 类型 ============

/**
 * 报告卡片入参。
 *
 * `report` 可能为完整报告，也可能是部分字段缺失的对象（例如后端仅推送了
 * 部分内容），因此各字段以可选 / 宽松类型接收，缺失时渲染占位（需求 6.5）。
 */
export interface HealthReportProps {
  /** 健康体检报告数据；各部分可能缺失。 */
  report: Partial<HealthReportData> | null | undefined
  /** 附加类名。 */
  className?: string
}

/** 整数格式化（千分位），用于 Star / Fork 展示。 */
function formatInteger(value: number): string {
  return Math.trunc(value).toLocaleString('en-US')
}

/** 缺失部分的占位提示。 */
function SectionPlaceholder({ label }: { label: string }) {
  return (
    <p className="text-sm text-muted-foreground italic">
      {label}尚未生成或未收到，等待分析完成…
    </p>
  )
}

/** 带标题的意见小节（列表）。 */
function OpinionList({ title, items }: { title: string; items?: string[] }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-sm font-medium text-foreground">{title}</h4>
      {items && items.length > 0 ? (
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          {items.map((item, idx) => (
            <li key={idx}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="pl-1 text-sm text-muted-foreground italic">暂无</p>
      )}
    </div>
  )
}

// ============ 部分 1：元数据摘要 ============

function MetadataSummarySection({ summary }: { summary?: MetadataSummary }) {
  if (!summary) {
    return <SectionPlaceholder label="元数据摘要" />
  }

  const languages = normalizeLanguageDistribution(
    summary.language_distribution ?? [],
  )

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-3">
        <div className="flex items-baseline gap-1.5 rounded-md bg-secondary px-3 py-2">
          <span className="text-lg font-semibold text-foreground">
            {formatInteger(summary.stars)}
          </span>
          <span className="text-sm text-muted-foreground">Stars</span>
        </div>
        <div className="flex items-baseline gap-1.5 rounded-md bg-secondary px-3 py-2">
          <span className="text-lg font-semibold text-foreground">
            {formatInteger(summary.forks)}
          </span>
          <span className="text-sm text-muted-foreground">Forks</span>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <h4 className="text-sm font-medium text-foreground">语言分布</h4>
        {languages.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {languages.map((lang) => (
              <li key={lang.name} className="flex flex-col gap-1">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-foreground">{lang.name}</span>
                  <span className="text-muted-foreground">{lang.percent}%</span>
                </div>
                <Progress value={lang.percent} className="h-1.5" />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground italic">暂无语言分布数据</p>
        )}
      </div>
    </div>
  )
}

// ============ 部分 2：代码审计意见 ============

function CodeAuditorSection({ opinion }: { opinion?: CodeAuditorOpinion }) {
  if (!opinion) {
    return <SectionPlaceholder label="代码审计意见" />
  }
  return (
    <div className="flex flex-col gap-4">
      {opinion.summary ? (
        <div className="text-sm text-muted-foreground">
          <Markdown>{opinion.summary}</Markdown>
        </div>
      ) : null}
      <OpinionList title="优点" items={opinion.strengths} />
      <OpinionList title="改进点" items={opinion.improvements} />
    </div>
  )
}

// ============ 部分 3：产品价值意见 ============

function ProductValueSection({ opinion }: { opinion?: ProductValueOpinion }) {
  if (!opinion) {
    return <SectionPlaceholder label="产品价值意见" />
  }
  return (
    <div className="flex flex-col gap-4">
      {opinion.summary ? (
        <div className="text-sm text-muted-foreground">
          <Markdown>{opinion.summary}</Markdown>
        </div>
      ) : null}
      <OpinionList title="README 清晰度" items={opinion.readme_clarity} />
      <OpinionList title="实用价值" items={opinion.practical_value} />
      <OpinionList title="开源活跃度" items={opinion.activeness} />
    </div>
  )
}

// ============ 部分 4：综合优化建议 ============

function RecommendationsSection({ items }: { items?: string[] }) {
  if (!items || items.length === 0) {
    return <SectionPlaceholder label="综合优化建议" />
  }
  return (
    <ol className="list-decimal space-y-2 pl-5 text-sm text-muted-foreground">
      {items.map((item, idx) => (
        <li key={idx}>{item}</li>
      ))}
    </ol>
  )
}

// ============ 部分 5：总分环形进度 ============

/** 根据分数返回主题色（用于环形描边）。 */
function scoreColor(score: number): string {
  if (score >= 80) return 'var(--primary)'
  if (score >= 60) return 'var(--chart-5)'
  return 'var(--destructive)'
}

/**
 * 总分环形进度（需求 8.2）。
 *
 * 用 GSAP 从 0 补间到最终分数：
 * - 环形描边 `stroke-dashoffset` 平滑收敛，直观呈现分数占比。
 * - 中心数字滚动递增到最终分数。
 * aria-label 恒为最终分数，保证可访问性与测试稳定（不受动画中间态影响）；
 * 若 GSAP 在当前环境未驱动补间，则回退为直接显示最终值。
 */
function ScoreRing({ score }: { score?: number }) {
  const isValid = typeof score === 'number' && !Number.isNaN(score)
  const clamped = isValid ? Math.max(0, Math.min(100, Math.round(score as number))) : 0

  const radius = 30
  const stroke = 6
  const circumference = 2 * Math.PI * radius
  const finalOffset = circumference * (1 - clamped / 100)
  const color = scoreColor(clamped)

  const ringRef = useRef<SVGCircleElement | null>(null)
  const [displayScore, setDisplayScore] = useState(clamped)

  useEffect(() => {
    if (!isValid) return
    // 数字滚动补间（0 → 最终分数）。
    const counter = { v: 0 }
    const numTween = gsap.to(counter, {
      v: clamped,
      duration: 1,
      ease: 'power2.out',
      onUpdate: () => setDisplayScore(Math.round(counter.v)),
      onComplete: () => setDisplayScore(clamped),
    })
    // 环形描边补间（满圈 → 目标 offset）。
    let ringTween: gsap.core.Tween | undefined
    if (ringRef.current) {
      ringTween = gsap.fromTo(
        ringRef.current,
        { strokeDashoffset: circumference },
        { strokeDashoffset: finalOffset, duration: 1, ease: 'power2.out' },
      )
    }
    // 兜底：确保最终值落定（防止环境未驱动 ticker 时停留在中间态）。
    setDisplayScore(clamped)
    return () => {
      numTween.kill()
      ringTween?.kill()
    }
  }, [clamped, isValid, circumference, finalOffset])

  if (!isValid) {
    return <SectionPlaceholder label="总分" />
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground">健康总分</span>
      <div className="relative size-[72px]">
        <svg
          className="h-full w-full -rotate-90"
          viewBox="0 0 72 72"
          role="img"
          aria-label={`总分 ${clamped} 分`}
        >
          <circle
            cx="36"
            cy="36"
            r={radius}
            fill="none"
            stroke="var(--secondary)"
            strokeWidth={stroke}
          />
          <circle
            ref={ringRef}
            cx="36"
            cy="36"
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={finalOffset}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
          <span className="text-lg font-semibold text-foreground">
            {displayScore}
          </span>
          <span className="mt-0.5 text-[9px] text-muted-foreground">/100</span>
        </div>
      </div>
    </div>
  )
}

// ============ 分段揭示容器 ============

/**
 * 报告分段的 stagger 揭示容器（需求 8.2）。
 *
 * 按 index 递增延迟做一次性淡入 + 上移，营造报告逐段生成的节奏感；
 * 动画仅影响呈现、不改变 DOM 结构与文本，保证测试与阅读稳定。
 */
function RevealSection({
  index,
  title,
  icon: Icon,
  children,
}: {
  index: number
  title: string
  icon: ComponentType<{ className?: string }>
  children: ReactNode
}) {
  return (
    <motion.section
      className={cn(
        'flex flex-col gap-4',
        index > 0 && 'border-t border-border/60 pt-8',
      )}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: 'easeOut', delay: index * 0.12 }}
    >
      <div className="flex items-center gap-2.5">
        <div className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Icon className="size-4" />
        </div>
        <h3 className="text-base font-semibold text-foreground">{title}</h3>
      </div>
      {children}
    </motion.section>
  )
}

// ============ 主组件 ============

/**
 * 健康体检报告卡片：渲染五个部分，缺失部分显示占位并保留已收到部分。
 */
export function HealthReport({ report, className }: HealthReportProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: 'easeOut' }}
    >
      <div
        className={cn(
          'w-full rounded-2xl border border-border bg-card text-card-foreground',
          className,
        )}
      >
        <CardHeader className="flex flex-row items-center justify-between gap-4 space-y-0 py-4">
          <CardTitle className="text-lg">健康体检报告</CardTitle>
          <ScoreRing score={report?.score} />
        </CardHeader>
        <CardContent className="flex flex-col gap-8">
          {/* 五部分卡片 stagger 依次揭示（需求 8.2），仅入场一次、不干扰阅读。 */}
          <RevealSection index={0} title="元数据摘要" icon={BarChart3}>
            <MetadataSummarySection summary={report?.metadata_summary} />
          </RevealSection>

          <RevealSection index={1} title="代码审计意见" icon={ScanSearch}>
            <CodeAuditorSection opinion={report?.code_auditor} />
          </RevealSection>

          <RevealSection index={2} title="产品价值意见" icon={Sparkles}>
            <ProductValueSection opinion={report?.product_value} />
          </RevealSection>

          <RevealSection index={3} title="综合优化建议" icon={Lightbulb}>
            <RecommendationsSection items={report?.recommendations} />
          </RevealSection>
        </CardContent>
      </div>
    </motion.div>
  )
}
