import { API_BASE_URL } from '@/config'
import { getAuthHeader } from '@/stores/authStore'
import type { SqlExample, SqlExampleForm, SqlExampleValidationResult } from '@/types/extendConfig'

export type AuditedSqlExample = SqlExample & { owner_id: string | null }

export const sqlExampleService = {
    async validate(data: SqlExampleForm): Promise<SqlExampleValidationResult> {
        const response = await fetch(`${API_BASE_URL}/config/sql-examples/validate`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question: data.question,
                sql: data.sql,
                parameters: data.parameters,
            }),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '校验 SQL 示例失败')
        }
        return response.json()
    },

    async audit(): Promise<AuditedSqlExample[]> {
        const response = await fetch(`${API_BASE_URL}/config/sql-examples/audit?limit=100`, {
            headers: getAuthHeader(),
        })
        if (!response.ok) throw new Error('加载 SQL 示例审计列表失败')
        const data = await response.json()
        return data.examples || []
    },

    async claimOrphan(exampleId: number, ownerId: string): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/sql-examples/${exampleId}/claim`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ owner_id: ownerId }),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '分配归属账号失败')
        }
    },
}
