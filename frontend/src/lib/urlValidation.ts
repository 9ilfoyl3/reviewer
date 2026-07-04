/**
 * 前端 GitHub 仓库 URL 校验纯函数（可独立单测，需求 1.1、1.3、10.6）。
 *
 * 校验规则：
 * - 空字符串 → 失败
 * - 超过 2048 字符 → 失败
 * - 不符合 `https://github.com/{owner}/{repo}` 格式 → 失败
 * - owner 与 repo 均须非空且仅由字母、数字、连字符、下划线、点号组成
 */

/** URL 长度上限（需求 1.1、1.3）。 */
export const MAX_URL_LENGTH = 2048

/** owner / repo 允许的字符集：字母、数字、连字符、下划线、点号。 */
const SEGMENT_PATTERN = /^[A-Za-z0-9._-]+$/

export interface RepoUrlValidationResult {
  /** 是否校验通过。 */
  valid: boolean
  /** 校验通过时解析出的仓库拥有者。 */
  owner?: string
  /** 校验通过时解析出的仓库名（已去除可选的 .git 后缀）。 */
  repo?: string
  /** 校验失败时的具体原因描述。 */
  error?: string
}

/**
 * 校验并解析 GitHub 仓库 URL。
 *
 * @param input 用户输入的原始字符串
 * @returns 校验结果；通过时附带 owner/repo，失败时附带具体原因
 */
export function validateRepoUrl(input: string): RepoUrlValidationResult {
  if (input == null || input.trim().length === 0) {
    return { valid: false, error: '请输入 GitHub 仓库地址' }
  }

  if (input.length > MAX_URL_LENGTH) {
    return {
      valid: false,
      error: `地址长度不能超过 ${MAX_URL_LENGTH} 个字符`,
    }
  }

  const trimmed = input.trim()

  let url: URL
  try {
    url = new URL(trimmed)
  } catch {
    return {
      valid: false,
      error: '地址格式不正确，应形如 https://github.com/{owner}/{repo}',
    }
  }

  if (url.protocol !== 'https:') {
    return { valid: false, error: '仅支持 https 协议的 GitHub 地址' }
  }

  if (url.hostname !== 'github.com') {
    return { valid: false, error: '仅支持 github.com 域名的仓库地址' }
  }

  // 去除首尾斜杠后按 "/" 拆分，路径必须恰好为 owner/repo 两段
  const segments = url.pathname.split('/').filter((s) => s.length > 0)
  if (segments.length !== 2) {
    return {
      valid: false,
      error: '地址应形如 https://github.com/{owner}/{repo}',
    }
  }

  const [owner, rawRepo] = segments
  // 允许可选的 .git 后缀
  const repo = rawRepo.endsWith('.git') ? rawRepo.slice(0, -4) : rawRepo

  if (!SEGMENT_PATTERN.test(owner)) {
    return {
      valid: false,
      error: 'owner 只能包含字母、数字、连字符、下划线或点号',
    }
  }

  if (repo.length === 0 || !SEGMENT_PATTERN.test(repo)) {
    return {
      valid: false,
      error: 'repo 只能包含字母、数字、连字符、下划线或点号',
    }
  }

  return { valid: true, owner, repo }
}
