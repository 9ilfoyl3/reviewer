/**
 * Agent 进度看板组件测试（需求 5.4、5.5、8.3、8.4）。
 *
 * 覆盖：
 * - AgentCard 状态徽章文案（等待/执行/完成/失败）。
 * - ThoughtStream 逐段追加渲染思考文本。
 * - ToolCallItem 显示工具名与结果摘要，>500 字符截断。
 * - AgentBoard 空态占位与按序渲染多个 Agent。
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AgentBoard } from './AgentBoard'
import { AgentCard } from './AgentCard'
import { ToolCallItem } from './ToolCallItem'
import type { AgentView, ToolActivity } from '@/hooks/useAnalysisStream'

function makeAgent(partial: Partial<AgentView> & { name: string }): AgentView {
  return {
    status: 'waiting',
    thought: '',
    iteration: 0,
    tools: [],
    ...partial,
  }
}

describe('AgentCard 状态徽章（需求 8.3、8.4）', () => {
  it.each([
    ['waiting', '等待中'],
    ['running', '执行中'],
    ['completed', '已完成'],
    ['failed', '失败'],
  ] as const)('状态 %s 显示徽章 %s', (status, label) => {
    render(<AgentCard agent={makeAgent({ name: 'Code_Auditor', status })} />)
    expect(screen.getByTestId('agent-status')).toHaveTextContent(label)
  })

  it('逐段追加的思考文本被渲染（需求 5.4）', () => {
    render(
      <AgentCard
        agent={makeAgent({ name: 'A', status: 'running', thought: '分析目录结构', iteration: 2 })}
      />,
    )
    expect(screen.getByTestId('thought-content')).toHaveTextContent('分析目录结构')
    expect(screen.getByText('第 2 轮')).toBeInTheDocument()
  })

  it('工具调用显示工具名与摘要（需求 5.5）', () => {
    const tools: ToolActivity[] = [
      { tool: 'read_file', args: { path: 'a.py' }, summary: '文件内容摘要', truncated: false, completed: true },
    ]
    render(<AgentCard agent={makeAgent({ name: 'A', status: 'running', tools })} />)
    expect(screen.getByText('read_file')).toBeInTheDocument()
    expect(screen.getByTestId('tool-call-summary')).toHaveTextContent('文件内容摘要')
  })
})

describe('ToolCallItem 摘要截断（需求 5.5）', () => {
  it('结果摘要超过 500 字符时截断展示并标注', () => {
    const long = 'x'.repeat(600)
    const activity: ToolActivity = {
      tool: 'read_tree',
      args: {},
      summary: long,
      truncated: true,
      completed: true,
    }
    render(<ToolCallItem activity={activity} />)
    const summary = screen.getByTestId('tool-call-summary')
    // 展示文本不超过 500 字符 + 省略号。
    expect(summary.textContent!.length).toBeLessThanOrEqual(501)
    expect(screen.getByText('已截断')).toBeInTheDocument()
  })

  it('未完成的工具调用显示「调用中」且不渲染摘要', () => {
    const activity: ToolActivity = {
      tool: 'read_readme',
      args: {},
      completed: false,
    }
    render(<ToolCallItem activity={activity} />)
    expect(screen.getByText('调用中')).toBeInTheDocument()
    expect(screen.queryByTestId('tool-call-summary')).not.toBeInTheDocument()
  })
})

describe('AgentBoard 容器', () => {
  it('无 Agent 时显示占位提示', () => {
    render(<AgentBoard agents={[]} />)
    expect(screen.getByTestId('agent-board-empty')).toBeInTheDocument()
  })

  it('按顺序渲染多个 Agent 卡片', () => {
    const agents = [
      makeAgent({ name: 'Code_Auditor', status: 'completed' }),
      makeAgent({ name: 'Product_Value_Agent', status: 'running' }),
      makeAgent({ name: 'Final_Judge', status: 'waiting' }),
    ]
    render(<AgentBoard agents={agents} />)
    const cards = screen.getAllByTestId('agent-card')
    expect(cards).toHaveLength(3)
    expect(screen.getByText('Code_Auditor')).toBeInTheDocument()
    expect(screen.getByText('Product_Value_Agent')).toBeInTheDocument()
    expect(screen.getByText('Final_Judge')).toBeInTheDocument()
  })
})
