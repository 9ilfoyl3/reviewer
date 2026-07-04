// 语言占比归一化单元测试（需求 6.6，Property 8）。
//
// Property 8: 语言占比归一化
// Validates: Requirements 6.6
// 对随机字节分布，断言归一化产出的占比之和恒为 100%（列表非空时），
// 且每项占比为 [0,100] 的整数、语言集合与有效输入一致、结果按占比降序稳定排列。

import { describe, it, expect } from 'vitest'

import {
  normalizeLanguages,
  normalizeLanguageDistribution,
  type LanguageWeights,
} from './languageStats'

/** 确定性伪随机数生成器（mulberry32），保证测试可复现。 */
function makeRng(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state |= 0
    state = (state + 0x6d2b79f5) | 0
    let t = Math.imul(state ^ (state >>> 15), 1 | state)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 生成一份随机的「语言 -> 字节数」分布。 */
function randomByteDistribution(rng: () => number): LanguageWeights {
  const langCount = 1 + Math.floor(rng() * 8) // 1~8 种语言
  const weights: LanguageWeights = {}
  for (let i = 0; i < langCount; i++) {
    const name = `Lang_${i}`
    // 字节数跨度较大：从个位到百万级，覆盖悬殊分布。
    const bytes = Math.floor(rng() * 1_000_000) + 1
    weights[name] = bytes
  }
  return weights
}

describe('normalizeLanguages - 占比之和恒为 100%（Property 8，需求 6.6）', () => {
  it('随机字节分布：非空结果的占比之和恒等于 100', () => {
    const rng = makeRng(0x1234_5678)
    for (let iter = 0; iter < 500; iter++) {
      const weights = randomByteDistribution(rng)
      const result = normalizeLanguages(weights)

      // 至少存在一个正权重项，结果不应为空。
      expect(result.length).toBeGreaterThan(0)

      const sum = result.reduce((acc, item) => acc + item.percent, 0)
      expect(sum).toBe(100)

      // 每项占比均为 [0,100] 的整数。
      for (const item of result) {
        expect(Number.isInteger(item.percent)).toBe(true)
        expect(item.percent).toBeGreaterThanOrEqual(0)
        expect(item.percent).toBeLessThanOrEqual(100)
      }

      // 结果语言集合应与输入语言集合一致。
      expect(result.map((r) => r.name).sort()).toEqual(
        Object.keys(weights).sort(),
      )

      // 结果按占比降序排列（同占比按名称升序）。
      for (let i = 1; i < result.length; i++) {
        const prev = result[i - 1]
        const cur = result[i]
        if (prev.percent === cur.percent) {
          expect(prev.name.localeCompare(cur.name)).toBeLessThanOrEqual(0)
        } else {
          expect(prev.percent).toBeGreaterThan(cur.percent)
        }
      }
    }
  })

  it('混入非法/零/负权重项：仍满足占比之和为 100 且忽略无效项', () => {
    const rng = makeRng(0x0bad_f00d)
    for (let iter = 0; iter < 300; iter++) {
      const weights = randomByteDistribution(rng)
      // 掺入若干无效项（0、负数、NaN、Infinity）。
      weights['Zero'] = 0
      weights['Neg'] = -Math.floor(rng() * 1000) - 1
      weights['Nan'] = Number.NaN
      weights['Inf'] = Number.POSITIVE_INFINITY

      const result = normalizeLanguages(weights)
      const names = new Set(result.map((r) => r.name))

      // 无效项被忽略。
      expect(names.has('Zero')).toBe(false)
      expect(names.has('Neg')).toBe(false)
      expect(names.has('Nan')).toBe(false)
      expect(names.has('Inf')).toBe(false)

      const sum = result.reduce((acc, item) => acc + item.percent, 0)
      expect(sum).toBe(100)
    }
  })

  it('全部为无效项时返回空列表', () => {
    expect(normalizeLanguages({})).toEqual([])
    expect(
      normalizeLanguages({ a: 0, b: -1, c: Number.NaN, d: Infinity }),
    ).toEqual([])
  })

  it('单一语言归一化为 100%', () => {
    expect(normalizeLanguages({ TypeScript: 12345 })).toEqual([
      { name: 'TypeScript', percent: 100 },
    ])
  })
})

describe('normalizeLanguageDistribution - 重新归一化占比之和为 100%（需求 6.6）', () => {
  it('随机占比列表：重新归一化后占比之和恒为 100', () => {
    const rng = makeRng(0xdead_beef)
    for (let iter = 0; iter < 300; iter++) {
      const count = 1 + Math.floor(rng() * 6)
      const distribution = Array.from({ length: count }, (_, i) => ({
        name: `Lang_${i}`,
        // 后端下发的占比可能因各自四舍五入使总和偏离 100。
        percent: Math.floor(rng() * 60),
      })).filter((d) => d.percent > 0)

      const result = normalizeLanguageDistribution(distribution)
      if (distribution.length === 0) {
        expect(result).toEqual([])
        continue
      }
      const sum = result.reduce((acc, item) => acc + item.percent, 0)
      expect(sum).toBe(100)
    }
  })
})
