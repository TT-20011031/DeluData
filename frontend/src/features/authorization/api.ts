import { fetchWithAuth } from '@/stores/authStore'
import type { DeletionBlockers } from './types'

const API = import.meta.env.VITE_API_BASE_URL || '/api'

export class AuthorizationApiError extends Error {
  status: number
  code?: string
  blockers?: DeletionBlockers

  constructor(message: string, status: number, code?: string, blockers?: DeletionBlockers) {
    super(message)
    this.name = 'AuthorizationApiError'
    this.status = status
    this.code = code
    this.blockers = blockers
  }
}

export async function authorizationRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchWithAuth(`${API}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers || {}),
    },
  })

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const detail = payload?.detail
    const code = typeof detail === 'object' ? detail?.code : detail
    const blockers = typeof detail === 'object' ? detail?.blockers : undefined
    throw new AuthorizationApiError(code || `请求失败 (${response.status})`, response.status, code, blockers)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

const ERROR_MESSAGES: Record<string, string> = {
  organization_not_found: '部门不存在或已被删除',
  organization_cycle: '不能把部门移动到自身或下级部门中',
  organization_disabled: '目标部门已停用',
  position_not_found: '岗位不存在或已被删除',
  user_not_found: '账号不存在或已被删除',
  username_exists: '登录名已存在',
  workspace_user_limit_reached: '当前工作区账号数量已达到上限',
  workspace_owner_cannot_be_disabled: '工作区所有者账号不能停用',
  assignment_overlap: '该账号在此岗位已有时间重叠的任职',
  primary_assignment_overlap: '该账号在所选时间内已有主任职',
  user_or_position_not_found: '账号或岗位不存在',
  workspace_scope_required: '只有工作区级管理员可以管理待分配账号',
}

export function apiErrorMessage(error: unknown, fallback = '操作失败'): string {
  if (error instanceof AuthorizationApiError) return ERROR_MESSAGES[error.code || ''] || error.message || fallback
  return error instanceof Error ? error.message : fallback
}
