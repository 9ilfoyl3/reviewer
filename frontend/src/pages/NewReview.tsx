/**
 * NewReview —— 发起新评估的落地页（空状态，挪用 artoo 的居中空态观感）。
 *
 * 仅负责「输入仓库地址 → 创建会话」这一步；创建成功后通过 onSessionCreated
 * 上抛给 App，由 App 切换到该会话的评估详情（ReviewDetail）进行流式展示。
 * 保持数据流向清晰：本页不持有分析态，只承载输入与提交。
 */

import { useCallback } from 'react'
import { toast } from 'sonner'
import { GitBranch } from 'lucide-react'

import { RepoUrlForm } from '@/components/RepoUrlForm'

export interface NewReviewProps {
  /** 会话创建成功：上抛 session_id 与仓库地址，由 App 进入评估详情。 */
  onSessionCreated: (sessionId: string, repoUrl: string) => void
}

export function NewReview({ onSessionCreated }: NewReviewProps) {
  const handleError = useCallback((message: string) => {
    toast.error(message)
  }, [])

  return (
    <div className="flex min-h-full items-center justify-center px-4 py-16">
      <div className="flex w-full max-w-xl flex-col items-center gap-6 text-center">
        <div className="flex size-16 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <GitBranch className="size-8" />
        </div>
        <div className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">
            GitHub 仓库评估
          </h1>
          <p className="text-sm text-muted-foreground">
            输入一个公开 GitHub 仓库地址，多 Agent 协作分析并实时生成健康评估报告。
          </p>
        </div>

        <div className="w-full">
          <RepoUrlForm onSessionCreated={onSessionCreated} onError={handleError} />
        </div>

        <p className="text-xs text-muted-foreground">
          例如 https://github.com/facebook/react
        </p>
      </div>
    </div>
  )
}
