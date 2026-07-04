/**
 * RepoUrlForm —— 仓库 URL 输入与提交表单（需求 1.1、1.2、1.3、1.6、1.7）。
 *
 * 设计要点（保持数据流向清晰、单向）：
 * - 基于 shadcn Input + Button + 原生 <form> 组合（本项目未引入 react-hook-form，
 *   沿用既有轻量约定）。
 * - 校验交给 `lib/urlValidation.ts` 的纯函数 `validateRepoUrl`，本组件只负责
 *   触发校验、展示内联错误与调用 API。
 * - 提交结果通过 `onSessionCreated` 回调上抛给父级（页面级单一数据源），
 *   本组件不持有会话/分析态，仅持有「本次提交进行中」的局部态。
 *
 * 交互契约：
 * - 输入非法（空 / 超 2048 字符 / 格式错误）→ 在输入框正下方显示具体原因，
 *   阻止发起请求（需求 1.1、1.3）。
 * - 输入合法 → `POST /api/analysis` 并禁用提交控件（需求 1.2）。
 * - 分析进行中（父级 `analyzing` 为真）→ 持续禁用提交控件，阻止重复提交
 *   （需求 1.6）。
 * - 请求 30s 无响应或返回非成功响应 → 显示失败/超时提示并重新启用
 *   （需求 1.7，超时由 `createAnalysis` 内置 30s AbortController 实现）。
 */

import { useId, useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ApiError, createAnalysis } from '@/lib/api'
import { MAX_URL_LENGTH, validateRepoUrl } from '@/lib/urlValidation'

export interface RepoUrlFormProps {
  /**
   * 会话创建成功回调：由父级据此建立 SSE 连接并进入分析态。
   * @param sessionId 后端返回的会话 ID
   * @param repoUrl 本次提交的合法仓库地址
   */
  onSessionCreated: (sessionId: string, repoUrl: string) => void
  /**
   * 请求失败/超时回调：供父级追加全局反馈（如 Toast，需求 1.7）。
   * 内联错误已由本组件展示，此回调为可选增强。
   * @param message 失败原因描述
   */
  onError?: (message: string) => void
  /**
   * 是否处于分析中状态（由父级根据会话状态传入）。
   * 为真时持续禁用提交控件以阻止重复提交同一分析（需求 1.6）。
   */
  analyzing?: boolean
}

/**
 * 仓库 URL 输入表单组件。
 */
export function RepoUrlForm({
  onSessionCreated,
  onError,
  analyzing = false,
}: RepoUrlFormProps) {
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  /** 本次提交请求进行中（POST /api/analysis 未返回）。 */
  const [submitting, setSubmitting] = useState(false)

  const inputId = useId()
  const errorId = useId()

  // 提交控件在「请求进行中」或「分析进行中」时均禁用（需求 1.2、1.6）。
  const disabled = submitting || analyzing

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (disabled) return

    // 先做前端校验，非法则内联报错并阻止请求（需求 1.1、1.3）。
    const result = validateRepoUrl(value)
    if (!result.valid) {
      setError(result.error ?? '仓库地址格式不正确')
      return
    }

    setError(null)
    setSubmitting(true)
    try {
      const { session_id } = await createAnalysis(value.trim())
      onSessionCreated(session_id, value.trim())
    } catch (err) {
      // 超时或非成功响应：显示失败/超时提示并重新启用（需求 1.7）。
      let message: string
      if (err instanceof ApiError) {
        message = err.isTimeout ? '请求超时，请稍后重试' : err.message
      } else {
        message = '发起分析失败，请稍后重试'
      }
      setError(message)
      onError?.(message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2" noValidate>
      <Label htmlFor={inputId} className="sr-only">
        GitHub 仓库地址
      </Label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          id={inputId}
          type="text"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          maxLength={MAX_URL_LENGTH}
          placeholder="https://github.com/{owner}/{repo}"
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            // 输入变化时清除旧的错误提示，避免陈旧反馈。
            if (error) setError(null)
          }}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className="flex-1"
        />
        <Button type="submit" disabled={disabled} className="sm:w-32">
          {disabled ? '分析中…' : '开始评估'}
        </Button>
      </div>
      {error ? (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </form>
  )
}
