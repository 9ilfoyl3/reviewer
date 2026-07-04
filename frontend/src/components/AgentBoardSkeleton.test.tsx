/**
 * AgentBoardSkeleton 骨架占位测试（需求 8.2）。
 *
 * 覆盖：默认渲染 3 张骨架卡片、可配置数量、可选提示文案。
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AgentBoardSkeleton } from './AgentBoardSkeleton'

describe('AgentBoardSkeleton 骨架占位', () => {
  it('默认渲染 3 张骨架卡片', () => {
    render(<AgentBoardSkeleton />)
    expect(screen.getAllByTestId('agent-card-skeleton')).toHaveLength(3)
  })

  it('可配置骨架卡片数量', () => {
    render(<AgentBoardSkeleton count={5} />)
    expect(screen.getAllByTestId('agent-card-skeleton')).toHaveLength(5)
  })

  it('提供 label 时渲染提示文案', () => {
    render(<AgentBoardSkeleton label="连接中断，重连中…" />)
    expect(screen.getByText('连接中断，重连中…')).toBeInTheDocument()
  })

  it('未提供 label 时不渲染提示文案', () => {
    render(<AgentBoardSkeleton />)
    expect(screen.getByTestId('agent-board-skeleton')).toBeInTheDocument()
  })
})
