/**
 * 语言分布归一化纯函数（可独立单测，需求 6.6，Property 8）。
 *
 * 将任意「语言 -> 权重（字节数或原始占比）」分布归一化为「语言名 + 整数占比」
 * 列表，并用最大余数法（Largest Remainder Method）做四舍五入补偿，
 * 确保各占比之和恒等于 100%。
 */

import type { LanguagePercent } from '@/types/events'

/** 归一化输入：语言名 -> 权重（如字节数，或后端已算好的占比）。 */
export type LanguageWeights = Record<string, number>

/**
 * 将语言权重分布归一化为整数占比列表，占比之和恒为 100%。
 *
 * 规则：
 * - 忽略权重非有限值或 ≤ 0 的语言项。
 * - 总权重为 0（无有效项）时返回空列表。
 * - 先按 `value / total * 100` 计算原始占比，向下取整得到基础整数占比，
 *   再用最大余数法把剩余的百分点依次补给小数部分最大的项，
 *   使整数占比之和精确等于 100（四舍五入补偿）。
 * - 结果按占比降序排列，占比相同的按语言名升序，保证展示稳定。
 *
 * @param weights 语言名到权重（字节数/占比）的映射
 * @returns 归一化后的「语言名 + 整数占比」列表，占比之和为 100%（列表非空时）
 */
export function normalizeLanguages(weights: LanguageWeights): LanguagePercent[] {
  const entries = Object.entries(weights).filter(
    ([, value]) => Number.isFinite(value) && value > 0,
  )

  const total = entries.reduce((sum, [, value]) => sum + value, 0)
  if (entries.length === 0 || total <= 0) {
    return []
  }

  // 计算原始占比、向下取整的基础占比与小数余数。
  const computed = entries.map(([name, value]) => {
    const exact = (value / total) * 100
    const floor = Math.floor(exact)
    return { name, floor, remainder: exact - floor }
  })

  const floorSum = computed.reduce((sum, item) => sum + item.floor, 0)
  // 需要补偿的百分点数量（因向下取整而缺失的部分）。
  let remaining = 100 - floorSum

  // 最大余数法：余数越大越优先 +1；余数相同按语言名升序，保证确定性。
  const byRemainder = [...computed].sort((a, b) => {
    if (b.remainder !== a.remainder) return b.remainder - a.remainder
    return a.name.localeCompare(b.name)
  })

  const bonus = new Set<string>()
  for (const item of byRemainder) {
    if (remaining <= 0) break
    bonus.add(item.name)
    remaining -= 1
  }

  const result: LanguagePercent[] = computed.map((item) => ({
    name: item.name,
    percent: item.floor + (bonus.has(item.name) ? 1 : 0),
  }))

  // 展示排序：占比降序，其次语言名升序。
  result.sort((a, b) => {
    if (b.percent !== a.percent) return b.percent - a.percent
    return a.name.localeCompare(b.name)
  })

  return result
}

/**
 * 将后端已给出的「语言名 + 占比」列表重新归一化，确保占比之和精确为 100%。
 *
 * 后端下发的占比可能因各自四舍五入导致总和偏离 100%；此处以其占比为权重
 * 复用 {@link normalizeLanguages} 做补偿。
 *
 * @param distribution 后端下发的语言占比列表
 * @returns 占比之和为 100% 的整数占比列表
 */
export function normalizeLanguageDistribution(
  distribution: LanguagePercent[],
): LanguagePercent[] {
  const weights: LanguageWeights = {}
  for (const { name, percent } of distribution) {
    if (typeof name !== 'string' || name.length === 0) continue
    weights[name] = (weights[name] ?? 0) + percent
  }
  return normalizeLanguages(weights)
}
