/**
 * Agent 角色的展示元数据（中文名 / 描述 / 图标 / 主题色）。
 *
 * 后端以英文 role 发射事件（Code_Auditor / Product_Value_Agent / Final_Judge），
 * 前端统一在这里映射为中文标签与视觉标识，供看板与流式步骤展示复用，
 * 保持「一处定义、多处引用」的清晰数据流。
 */

import { Gavel, ScanSearch, Sparkles, type LucideIcon } from 'lucide-react'

export interface AgentMeta {
  /** 中文角色名。 */
  label: string
  /** 一句话职责描述。 */
  description: string
  /** 角色图标。 */
  icon: LucideIcon
}

const AGENT_META: Record<string, AgentMeta> = {
  Code_Auditor: {
    label: '代码审计',
    description: '评估目录结构与核心代码质量',
    icon: ScanSearch,
  },
  Product_Value_Agent: {
    label: '产品价值',
    description: '评估 README 清晰度、实用价值与活跃度',
    icon: Sparkles,
  },
  Final_Judge: {
    label: '总分裁判',
    description: '汇总各方意见，给出总分与优化建议',
    icon: Gavel,
  },
}

/** 取某个 Agent 角色的展示元数据；未知角色回退到通用标识。 */
export function agentMeta(role: string): AgentMeta {
  return (
    AGENT_META[role] ?? {
      label: role,
      description: '',
      icon: Sparkles,
    }
  )
}
