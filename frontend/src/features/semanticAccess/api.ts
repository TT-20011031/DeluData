import { getAuthHeader } from '@/stores/authStore'

const API = import.meta.env.VITE_API_BASE_URL || '/api'

export class SemanticAccessApiError extends Error {
  status: number
  code?: string
  details?: {
    blockers?: Array<{ code: string; message: string }>
    warnings?: Array<{ code: string; message: string }>
    current_revision?: number
    table_id?: number
    target_type?: string
    target_id?: string
  }

  constructor(message: string, status: number, code?: string, details?: SemanticAccessApiError['details']) {
    super(message)
    this.name = 'SemanticAccessApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

export async function semanticAccessRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      ...getAuthHeader(),
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const detail = payload?.detail
    throw new SemanticAccessApiError(
      detail?.message || (typeof detail === 'string' ? detail : '') || `请求失败 (${response.status})`,
      response.status,
      detail?.code,
      detail?.details,
    )
  }
  return response.json() as Promise<T>
}

export function semanticAccessErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof SemanticAccessApiError) {
    if (error.code === 'revision_conflict') return '配置已被其他管理员修改，请刷新后重试。'
    if (error.code === 'bootstrap_revision_conflict') return '审核方案已被其他管理员修改，请刷新后重试。'
    if (error.code === 'schema_changed') return '治理后的表、字段或指标已经变化，请重新生成首次配置。'
    if (error.code === 'organization_changed') return '组织架构已经变化，请重新生成首次配置。'
    if (error.code === 'mapping_conflict') return '数据归属映射已经变化，请重新生成首次配置。'
    if (error.code === 'target_binding_conflict'
      || error.code === 'target_configured_conflict'
      || error.code === 'target_revision_conflict') {
      return '目标策略已经发生人工修改，本次不会覆盖，请重新生成首次配置。'
    }
    if (error.code?.includes('revision_conflict') || error.code?.endsWith('_conflict')) {
      return '审核方案依赖的数据已经变化，请刷新或重新生成。'
    }
    const blockers = error.details?.blockers?.map(item => item.message).filter(Boolean)
    if (blockers?.length) return blockers.join('；')
    return error.message
  }
  return error instanceof Error ? error.message : fallback
}
