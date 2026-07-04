// Feature: reviewer, Property 5: 前端 URL 校验分类
//
// Property 5: 前端 URL 校验分类（Validates: Requirements 1.3）
// 对任意符合 `https://github.com/{owner}/{repo}` 格式的字符串，validateRepoUrl 应返回校验通过；
// 对任意空、超 2048 字符或不符合格式的字符串，应返回校验失败并给出具体原因（error）。

import { describe, it, expect } from 'vitest'
import { validateRepoUrl, MAX_URL_LENGTH } from './urlValidation'

describe('validateRepoUrl - 合法输入（需求 1.3 / 10.6）', () => {
  it('接受标准 https github 地址', () => {
    const result = validateRepoUrl('https://github.com/facebook/react')
    expect(result.valid).toBe(true)
    expect(result.owner).toBe('facebook')
    expect(result.repo).toBe('react')
    expect(result.error).toBeUndefined()
  })

  it('接受带 .git 后缀的地址并去除后缀', () => {
    const result = validateRepoUrl('https://github.com/facebook/react.git')
    expect(result.valid).toBe(true)
    expect(result.owner).toBe('facebook')
    expect(result.repo).toBe('react')
  })

  it('接受 owner/repo 含连字符、下划线、点号、数字', () => {
    const result = validateRepoUrl('https://github.com/my-org_1/repo.name-2')
    expect(result.valid).toBe(true)
    expect(result.owner).toBe('my-org_1')
    expect(result.repo).toBe('repo.name-2')
  })

  it('接受首尾空白并 trim 后合法的地址', () => {
    const result = validateRepoUrl('  https://github.com/owner/repo  ')
    expect(result.valid).toBe(true)
    expect(result.owner).toBe('owner')
    expect(result.repo).toBe('repo')
  })

  it('接受带末尾斜杠的地址', () => {
    const result = validateRepoUrl('https://github.com/owner/repo/')
    expect(result.valid).toBe(true)
    expect(result.owner).toBe('owner')
    expect(result.repo).toBe('repo')
  })

  it('接受长度恰好等于上限的地址', () => {
    const base = 'https://github.com/owner/'
    const repo = 'a'.repeat(MAX_URL_LENGTH - base.length)
    const url = base + repo
    expect(url.length).toBe(MAX_URL_LENGTH)
    const result = validateRepoUrl(url)
    expect(result.valid).toBe(true)
    expect(result.repo).toBe(repo)
  })
})

describe('validateRepoUrl - 非法输入（需求 1.3 / 10.6）', () => {
  it('拒绝空字符串并给出原因', () => {
    const result = validateRepoUrl('')
    expect(result.valid).toBe(false)
    expect(result.error).toBeTruthy()
    expect(result.owner).toBeUndefined()
    expect(result.repo).toBeUndefined()
  })

  it('拒绝纯空白字符串', () => {
    const result = validateRepoUrl('   ')
    expect(result.valid).toBe(false)
    expect(result.error).toBeTruthy()
  })

  it('拒绝超过 2048 字符的地址', () => {
    const url = 'https://github.com/owner/' + 'a'.repeat(MAX_URL_LENGTH)
    expect(url.length).toBeGreaterThan(MAX_URL_LENGTH)
    const result = validateRepoUrl(url)
    expect(result.valid).toBe(false)
    expect(result.error).toContain(String(MAX_URL_LENGTH))
  })

  it('拒绝无法解析为 URL 的格式错误字符串', () => {
    const result = validateRepoUrl('not a url')
    expect(result.valid).toBe(false)
    expect(result.error).toBeTruthy()
  })

  it('拒绝非 https 协议（http）', () => {
    const result = validateRepoUrl('http://github.com/owner/repo')
    expect(result.valid).toBe(false)
    expect(result.error).toContain('https')
  })

  it('拒绝 ssh 协议格式', () => {
    const result = validateRepoUrl('git@github.com:owner/repo.git')
    expect(result.valid).toBe(false)
    expect(result.error).toBeTruthy()
  })

  it('拒绝非 github.com 域名', () => {
    const result = validateRepoUrl('https://gitlab.com/owner/repo')
    expect(result.valid).toBe(false)
    expect(result.error).toBeTruthy()
  })

  it('拒绝缺少 repo 段（仅 owner）', () => {
    const result = validateRepoUrl('https://github.com/owner')
    expect(result.valid).toBe(false)
    expect(result.error).toBeTruthy()
  })

  it('拒绝路径段超过两段', () => {
    const result = validateRepoUrl('https://github.com/owner/repo/extra')
    expect(result.valid).toBe(false)
    expect(result.error).toBeTruthy()
  })

  it('拒绝 owner 含非法字符', () => {
    const result = validateRepoUrl('https://github.com/ow ner/repo')
    expect(result.valid).toBe(false)
    expect(result.error).toBeTruthy()
  })

  it('拒绝 .git 去除后 repo 为空', () => {
    const result = validateRepoUrl('https://github.com/owner/.git')
    expect(result.valid).toBe(false)
    expect(result.error).toBeTruthy()
  })
})

describe('validateRepoUrl - 分类一致性（Property 5）', () => {
  // 表驱动断言：每个用例的校验结果与预期分类一致
  const cases: Array<{ input: string; expectedValid: boolean; desc: string }> = [
    { input: 'https://github.com/a/b', expectedValid: true, desc: '最短合法地址' },
    { input: 'https://github.com/a/b.git', expectedValid: true, desc: '带 .git' },
    { input: '', expectedValid: false, desc: '空' },
    { input: 'ftp://github.com/a/b', expectedValid: false, desc: '非 https 协议' },
    { input: 'https://example.com/a/b', expectedValid: false, desc: '错误域名' },
    { input: 'https://github.com/a', expectedValid: false, desc: '缺 repo' },
  ]

  it.each(cases)('$desc → valid=$expectedValid', ({ input, expectedValid }) => {
    const result = validateRepoUrl(input)
    expect(result.valid).toBe(expectedValid)
    if (expectedValid) {
      expect(result.owner).toBeTruthy()
      expect(result.repo).toBeTruthy()
    } else {
      expect(result.error).toBeTruthy()
    }
  })
})
