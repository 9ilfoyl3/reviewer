/**
 * RepoUrlForm 单元测试（需求 1.1、1.2、1.3、1.6、1.7）。
 *
 * 覆盖：
 * - 非法输入内联报错且不发起请求（1.1、1.3）
 * - 合法输入 POST /api/analysis 并回调 session_id（1.2）
 * - analyzing 为真时持续禁用提交控件（1.6）
 * - 非成功响应 / 超时显示错误提示并重新启用（1.7）
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RepoUrlForm } from './RepoUrlForm'
import * as api from '@/lib/api'

const VALID_URL = 'https://github.com/facebook/react'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('RepoUrlForm', () => {
  it('空输入时显示校验错误且不发起请求（需求 1.1、1.3）', async () => {
    const user = userEvent.setup()
    const createSpy = vi.spyOn(api, 'createAnalysis')
    const onSessionCreated = vi.fn()

    render(<RepoUrlForm onSessionCreated={onSessionCreated} />)
    await user.click(screen.getByRole('button', { name: '开始体检' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('请输入 GitHub 仓库地址')
    expect(createSpy).not.toHaveBeenCalled()
    expect(onSessionCreated).not.toHaveBeenCalled()
  })

  it('格式非法时显示具体原因且不发起请求（需求 1.3）', async () => {
    const user = userEvent.setup()
    const createSpy = vi.spyOn(api, 'createAnalysis')

    render(<RepoUrlForm onSessionCreated={vi.fn()} />)
    await user.type(screen.getByRole('textbox'), 'https://gitlab.com/foo/bar')
    await user.click(screen.getByRole('button', { name: '开始体检' }))

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(createSpy).not.toHaveBeenCalled()
  })

  it('合法输入时 POST /api/analysis 并回调 session_id（需求 1.2）', async () => {
    const user = userEvent.setup()
    const createSpy = vi
      .spyOn(api, 'createAnalysis')
      .mockResolvedValue({ session_id: 'sess-123' })
    const onSessionCreated = vi.fn()

    render(<RepoUrlForm onSessionCreated={onSessionCreated} />)
    await user.type(screen.getByRole('textbox'), VALID_URL)
    await user.click(screen.getByRole('button', { name: '开始体检' }))

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith(VALID_URL)
      expect(onSessionCreated).toHaveBeenCalledWith('sess-123', VALID_URL)
    })
  })

  it('analyzing 为真时持续禁用提交控件（需求 1.6）', () => {
    render(<RepoUrlForm onSessionCreated={vi.fn()} analyzing />)

    expect(screen.getByRole('button')).toBeDisabled()
    expect(screen.getByRole('textbox')).toBeDisabled()
  })

  it('非成功响应显示错误提示并重新启用（需求 1.7）', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'createAnalysis').mockRejectedValue(
      new api.ApiError('仓库地址格式非法', { status: 400 }),
    )
    render(<RepoUrlForm onSessionCreated={vi.fn()} />)

    await user.type(screen.getByRole('textbox'), VALID_URL)
    await user.click(screen.getByRole('button', { name: '开始体检' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('仓库地址格式非法')
    // 请求结束后重新启用（需求 1.7）
    expect(screen.getByRole('button', { name: '开始体检' })).toBeEnabled()
  })

  it('请求超时显示超时提示并重新启用（需求 1.7）', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'createAnalysis').mockRejectedValue(
      new api.ApiError('请求超时，请稍后重试', { isTimeout: true }),
    )
    render(<RepoUrlForm onSessionCreated={vi.fn()} />)

    await user.type(screen.getByRole('textbox'), VALID_URL)
    await user.click(screen.getByRole('button', { name: '开始体检' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('请求超时')
    expect(screen.getByRole('button', { name: '开始体检' })).toBeEnabled()
  })
})
