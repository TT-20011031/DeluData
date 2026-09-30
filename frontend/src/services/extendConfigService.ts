/**
 * 扩展配置 API 服务层
 * 
 * 封装所有 SQL 示例和模板相关的 HTTP 请求
 */
import { getAuthHeader } from '@/stores/authStore'
import { API_BASE_URL } from '@/config'
import { splitSemanticTerms } from '@/utils/semanticTerms'
import type {
    SqlExample,
    SqlGroup,
    SqlExampleForm,
    SqlGroupForm,
    Template,
    TemplateGroup,
    TemplateGroupForm,
    TemplateUpdatePayload,
    BlankFieldDetectResponse,
    CandidateField,
    SemanticAccessOptions,
    SemanticAccessCompileResult,
    SemanticAccessEffectivePreview,
    SemanticAccessNaturalLanguageResult,
    SemanticAccessPolicy,
    SemanticAccessPolicyDraftInput,
    SemanticAssetTag,
    SemanticBusinessSuggestion,
    SemanticEvalCase,
    SemanticEvalRun,
    SemanticGovernanceCandidate,
    SemanticGovernanceEvidenceFact,
    SemanticGovernancePolicy,
    SemanticGovernanceRun,
    SemanticModelOverview,
    SemanticModelsPayload,
    SemanticMetricForm,
    SemanticPreviewErrorDetail,
    SemanticPreviewResult,
    SemanticRelationshipForm,
    SemanticQuestionReadiness,
    SemanticReadiness,
    SemanticRuntimeMode,
    SemanticScanRun,
    PreviewMappingItem,
} from '@/types/extendConfig'

type AccessUserPayload = {
    id: string | number
    username?: string | null
    email?: string | null
}

export class SemanticAccessApiError extends Error {
    readonly status: number
    readonly errorType?: string
    readonly details?: unknown

    constructor(message: string, status: number, errorType?: string, details?: unknown) {
        super(message)
        this.name = 'SemanticAccessApiError'
        this.status = status
        this.errorType = errorType
        this.details = details
    }
}

export class SemanticAccessSaveError extends Error {
    readonly persistedPolicy: SemanticAccessPolicy
    readonly cause: unknown

    constructor(cause: unknown, persistedPolicy: SemanticAccessPolicy) {
        super(cause instanceof Error ? cause.message : '权限保存后未能生效')
        this.name = 'SemanticAccessSaveError'
        this.persistedPolicy = persistedPolicy
        this.cause = cause
    }
}

async function semanticAccessError(response: Response, fallback: string): Promise<SemanticAccessApiError> {
    const payload = await response.json().catch(() => ({})) as {
        detail?: string | { message?: string; error_type?: string; details?: unknown }
    }
    const message = typeof payload.detail === 'object' ? payload.detail?.message : payload.detail
    const errorType = typeof payload.detail === 'object' ? payload.detail?.error_type : undefined
    const details = typeof payload.detail === 'object' ? payload.detail?.details : undefined
    return new SemanticAccessApiError(message || `${fallback}: ${response.status}`, response.status, errorType, details)
}

// =============================================================================
// SQL 示例服务
// =============================================================================

export const extendConfigService = {
    // =========================================================================
    // 语义模型
    // =========================================================================

    async getSemanticModels(): Promise<SemanticModelsPayload> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/models`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取语义模型失败: ${response.status}`)
        }

        return response.json()
    },

    async getSemanticModelOverview(): Promise<SemanticModelOverview> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/overview`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取语义模型概览失败: ${response.status}`)
        }

        return response.json()
    },

    async getSemanticAccessAssets(): Promise<SemanticModelsPayload> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/access-assets`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取问数权限资产失败: ${response.status}`)
        }

        return response.json()
    },

    async listSemanticAccessPolicies(status?: string): Promise<SemanticAccessPolicy[]> {
        const suffix = status ? `?status=${encodeURIComponent(status)}` : ''
        const response = await fetch(`${API_BASE_URL}/config/semantic/access-policies${suffix}`, {
            headers: getAuthHeader(),
        })
        if (!response.ok) throw await semanticAccessError(response, '获取权限策略失败')
        const payload = await response.json() as { policies?: SemanticAccessPolicy[] }
        return payload.policies || []
    },

    async getSemanticAccessPolicy(policyId: number): Promise<SemanticAccessPolicy> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/access-policies/${policyId}`, {
            headers: getAuthHeader(),
        })
        if (!response.ok) throw await semanticAccessError(response, '获取权限策略详情失败')
        return response.json()
    },

    async createSemanticAccessPolicyDraft(input: SemanticAccessPolicyDraftInput): Promise<SemanticAccessPolicy> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/access-policies/drafts`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(input),
        })
        if (!response.ok) throw await semanticAccessError(response, '生成权限策略草案失败')
        return response.json()
    },

    async parseSemanticAccessNaturalLanguage(sourceText: string): Promise<SemanticAccessNaturalLanguageResult> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/access-policies/parse-natural-language`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_text: sourceText }),
        })
        if (!response.ok) throw await semanticAccessError(response, '解析权限要求失败')
        return response.json()
    },

    async updateSemanticAccessPolicy(
        policyId: number,
        patch: Partial<SemanticAccessPolicyDraftInput>,
    ): Promise<SemanticAccessPolicy> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/access-policies/${policyId}`, {
            method: 'PATCH',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
        })
        if (!response.ok) throw await semanticAccessError(response, '更新权限策略失败')
        return response.json()
    },

    async compileSemanticAccessPolicy(policyId: number): Promise<SemanticAccessCompileResult> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/access-policies/${policyId}/compile`, {
            method: 'POST',
            headers: getAuthHeader(),
        })
        if (!response.ok) throw await semanticAccessError(response, '编译权限策略失败')
        return response.json()
    },

    async activateSemanticAccessPolicy(policyId: number): Promise<SemanticAccessPolicy> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/access-policies/${policyId}/activate`, {
            method: 'POST',
            headers: getAuthHeader(),
        })
        if (!response.ok) throw await semanticAccessError(response, '启用权限策略失败')
        return response.json()
    },

    async saveAndActivateSemanticAccessPolicy(
        input: SemanticAccessPolicyDraftInput,
        editablePolicyId?: number,
    ): Promise<{ persisted: SemanticAccessPolicy; active: SemanticAccessPolicy }> {
        const persisted = editablePolicyId
            ? await extendConfigService.updateSemanticAccessPolicy(editablePolicyId, input)
            : await extendConfigService.createSemanticAccessPolicyDraft(input)
        try {
            const active = await extendConfigService.activateSemanticAccessPolicy(persisted.id)
            return { persisted, active }
        } catch (error) {
            throw new SemanticAccessSaveError(error, persisted)
        }
    },

    async disableSemanticAccessPolicy(policyId: number): Promise<SemanticAccessPolicy> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/access-policies/${policyId}/disable`, {
            method: 'POST',
            headers: getAuthHeader(),
        })
        if (!response.ok) throw await semanticAccessError(response, '停用权限策略失败')
        return response.json()
    },

    async previewSemanticAccessPolicy(userId: string, policyId?: number): Promise<SemanticAccessEffectivePreview> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/access-policies/effective-preview`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, policy_id: policyId }),
        })
        if (!response.ok) throw await semanticAccessError(response, '预览有效权限失败')
        return response.json()
    },

    async listSemanticAssetTags(): Promise<SemanticAssetTag[]> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/asset-tags`, {
            headers: getAuthHeader(),
        })
        if (!response.ok) throw await semanticAccessError(response, '获取语义资产标签失败')
        const payload = await response.json() as { tags?: SemanticAssetTag[] }
        return payload.tags || []
    },

    async replaceSemanticAssetTags(assetType: SemanticAssetTag['asset_type'], assetId: number, tags: Array<Pick<SemanticAssetTag, 'tag_type' | 'tag_value'>>): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/asset-tags`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ asset_type: assetType, asset_id: assetId, tags }),
        })
        if (!response.ok) throw await semanticAccessError(response, '更新语义资产标签失败')
    },

    async scanSemanticModels(): Promise<{
        table_count: number
        column_count: number
        metric_suggestion_count?: number
        relationship_suggestion_count?: number
        metric_count?: number
        metric_confirmed_count?: number
        relationship_count?: number
        relationship_confirmed_count?: number
        removed_metric_count?: number
        removed_relationship_count?: number
    }> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/scan`, {
            method: 'POST',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '扫描失败')
        }

        return response.json()
    },

    async previewSemanticScan(): Promise<SemanticScanRun> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/scans/preview`, {
            method: 'POST',
            headers: getAuthHeader(),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || 'Schema 预览失败')
        }
        return response.json()
    },

    async listSemanticScans(): Promise<SemanticScanRun[]> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/scans`, { headers: getAuthHeader() })
        if (!response.ok) throw new Error(`获取扫描历史失败: ${response.status}`)
        const data = await response.json()
        return data.scans || []
    },

    async applySemanticScan(scanId: number): Promise<SemanticScanRun> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/scans/${scanId}/apply`, {
            method: 'POST',
            headers: getAuthHeader(),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '应用 Schema 变化失败')
        }
        return response.json()
    },

    async getSemanticReadiness(): Promise<SemanticReadiness> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/readiness`, { headers: getAuthHeader() })
        if (!response.ok) throw new Error(`获取可信就绪度失败: ${response.status}`)
        return response.json()
    },

    async createSemanticGovernanceRun(triggerType = 'manual'): Promise<SemanticGovernanceRun> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/runs`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ trigger_type: triggerType }),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error?.detail?.message || error?.detail || '启动自动治理失败')
        }
        return response.json()
    },

    async cancelSemanticGovernanceRun(runId: number): Promise<SemanticGovernanceRun> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/runs/${runId}/cancel`, {
            method: 'POST',
            headers: getAuthHeader(),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error?.detail?.message || error?.detail || '取消治理候选生成失败')
        }
        return response.json()
    },

    async retrySemanticGovernanceRun(runId: number): Promise<SemanticGovernanceRun> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/runs/${runId}/retry`, {
            method: 'POST',
            headers: getAuthHeader(),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error?.detail?.message || error?.detail || '重试治理候选生成失败')
        }
        return response.json()
    },

    async listSemanticGovernanceRuns(): Promise<SemanticGovernanceRun[]> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/runs`, { headers: getAuthHeader() })
        if (!response.ok) throw new Error(`获取治理运行失败: ${response.status}`)
        const data = await response.json()
        return data.runs || []
    },

    async listSemanticGovernanceCandidates(params: {
        status?: string
        target_type?: string
        candidate_type?: string
        risk_level?: string
        min_score?: number
    } = {}): Promise<SemanticGovernanceCandidate[]> {
        const search = new URLSearchParams()
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== '') search.set(key, String(value))
        })
        const suffix = search.size ? `?${search.toString()}` : ''
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/candidates${suffix}`, { headers: getAuthHeader() })
        if (!response.ok) throw new Error(`获取治理候选失败: ${response.status}`)
        const data = await response.json()
        return data.candidates || []
    },

    async acceptSemanticGovernanceCandidate(candidateId: number, editedPatch?: Record<string, unknown>, reason?: string): Promise<SemanticGovernanceCandidate> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/candidates/${candidateId}/accept`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ edited_patch: editedPatch, reason }),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error?.detail?.message || error?.detail || '接受候选失败')
        }
        return response.json()
    },

    async rejectSemanticGovernanceCandidate(candidateId: number, category: string, reason: string): Promise<SemanticGovernanceCandidate> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/candidates/${candidateId}/reject`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ category, reason }),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error?.detail?.message || error?.detail || '拒绝候选失败')
        }
        return response.json()
    },

    async rollbackSemanticGovernanceCandidate(candidateId: number): Promise<SemanticGovernanceCandidate> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/candidates/${candidateId}/rollback`, {
            method: 'POST',
            headers: getAuthHeader(),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error?.detail?.message || error?.detail || '回滚自动变更失败')
        }
        return response.json()
    },

    async batchSemanticGovernanceCandidates(ids: number[], action: 'accept' | 'reject', options: { reason?: string; category?: string } = {}): Promise<{ succeeded: number[]; failed: Array<{ candidate_id: number; error: string }> }> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/candidates/batch`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids, action, reason: options.reason || '', category: options.category || 'not_relevant' }),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error?.detail?.message || error?.detail || '批量处理候选失败')
        }
        return response.json()
    },

    async getSemanticGovernanceEvidence(objectType: string, objectId: number): Promise<SemanticGovernanceEvidenceFact[]> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/evidence/${objectType}/${objectId}`, { headers: getAuthHeader() })
        if (!response.ok) throw new Error(`获取证据时间线失败: ${response.status}`)
        const data = await response.json()
        return data.evidence || []
    },

    async getSemanticGovernancePolicy(): Promise<SemanticGovernancePolicy> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/policy`, { headers: getAuthHeader() })
        if (!response.ok) throw new Error(`获取治理策略失败: ${response.status}`)
        return response.json()
    },

    async updateSemanticGovernancePolicy(patch: Partial<SemanticGovernancePolicy>): Promise<SemanticGovernancePolicy> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/governance/policy`, {
            method: 'PATCH',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error?.detail?.message || error?.detail || '更新治理策略失败')
        }
        return response.json()
    },

    async checkSemanticQuestion(question: string): Promise<SemanticQuestionReadiness> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/readiness/check-question`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ question }),
        })
        if (!response.ok) throw new Error(`问题诊断失败: ${response.status}`)
        return response.json()
    },

    async setSemanticRuntimeMode(runtimeMode: SemanticRuntimeMode): Promise<{ runtime_mode: SemanticRuntimeMode; warning: string | null }> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/runtime-mode`, {
            method: 'PATCH',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ runtime_mode: runtimeMode }),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '切换运行模式失败')
        }
        return response.json()
    },

    async updateSemanticModel(modelType: string, id: number, patch: Record<string, unknown>): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/${modelType}/${id}`, {
            method: 'PATCH',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '更新失败')
        }
    },

    async getSemanticAccessOptions(): Promise<SemanticAccessOptions> {
        const usersResponse = await fetch(`${API_BASE_URL}/authorization/users`, { headers: getAuthHeader() })
        if (!usersResponse.ok) {
            throw new Error(`获取用户列表失败: ${usersResponse.status}`)
        }

        const usersData = await usersResponse.json() as unknown
        const users = Array.isArray(usersData) ? usersData as AccessUserPayload[] : []
        return {
            roles: [],
            users: users.map(user => ({
                id: String(user.id),
                name: user.username || user.email || String(user.id),
                description: user.email,
            })),
        }
    },

    async previewSemanticQuery(question: string): Promise<SemanticPreviewResult> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/preview-query`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ question }),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = (typeof error.detail === 'object' ? error.detail : { message: error.detail }) as SemanticPreviewErrorDetail
            const message = detail.message || `预览失败: ${response.status}`
            const previewError = new Error(message) as Error & { detail?: SemanticPreviewErrorDetail }
            previewError.detail = detail
            throw previewError
        }

        return response.json()
    },

    async getSemanticEvaluationCases(): Promise<SemanticEvalCase[]> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/evaluation/cases`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取评估用例失败: ${response.status}`)
        }

        const data = await response.json()
        return data.cases || []
    },

    async getSemanticEvaluationRuns(): Promise<SemanticEvalRun[]> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/evaluation/runs`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取评估记录失败: ${response.status}`)
        }

        const data = await response.json()
        return data.runs || []
    },

    async runSemanticEvaluation(caseIds?: string[]): Promise<{ runs: SemanticEvalRun[] }> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/evaluation/run`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ case_ids: caseIds && caseIds.length ? caseIds : undefined }),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '运行评估失败')
        }

        return response.json()
    },

    async generateBusinessSuggestions(payload: {
        scope: 'datasource' | 'table'
        table_id?: number | null
        force?: boolean
        use_llm?: boolean
    }): Promise<{ suggestion_count: number }> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/business-suggestions/generate`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '生成建议失败')
        }

        return response.json()
    },

    async getTableBusinessSuggestions(tableId: number): Promise<SemanticBusinessSuggestion[]> {
        const response = await fetch(
            `${API_BASE_URL}/config/semantic/business-suggestions/tables/${tableId}`,
            { headers: getAuthHeader() },
        )
        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '获取当前表语义建议失败')
        }
        return response.json()
    },

    async acceptTableBusinessSuggestions(tableId: number): Promise<{ accepted_count: number }> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/business-suggestions/tables/${tableId}/accept-all`, {
            method: 'POST',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '按表接受建议失败')
        }

        return response.json()
    },

    async generateMetricSuggestions(): Promise<{ suggestion_count: number }> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/metrics/suggestions/generate`, {
            method: 'POST',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '生成指标建议失败')
        }

        return response.json()
    },

    async generateRelationshipSuggestions(): Promise<{ suggestion_count: number }> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/relationships/suggestions/generate`, {
            method: 'POST',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '生成关系建议失败')
        }

        return response.json()
    },

    async updateBusinessSuggestion(id: number, patch: Record<string, unknown>): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/business-suggestions/${id}`, {
            method: 'PATCH',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '更新建议失败')
        }
    },

    async acceptBusinessSuggestion(id: number): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/business-suggestions/${id}/accept`, {
            method: 'POST',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '接受建议失败')
        }
    },

    async batchAcceptBusinessSuggestions(ids: number[]): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/business-suggestions/batch-accept`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '批量接受建议失败')
        }
    },

    async rejectBusinessSuggestion(id: number): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/business-suggestions/${id}/reject`, {
            method: 'POST',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '忽略建议失败')
        }
    },

    async createSemanticMetric(form: SemanticMetricForm): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/metrics`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: form.name,
                business_name: form.business_name || form.name,
                formula: form.formula,
                aggregation: form.aggregation || null,
                table_id: form.table_id,
                column_id: form.column_id,
                time_column_id: form.time_column_id,
                default_grain: form.default_grain || null,
                description: form.description,
                synonyms: splitSemanticTerms(form.synonyms),
                status: form.status,
                is_queryable: form.is_queryable,
                is_sensitive: form.is_sensitive,
            }),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '创建指标失败')
        }
    },

    async createSemanticRelationship(form: SemanticRelationshipForm): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/semantic/relationships`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(form),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            const detail = typeof error.detail === 'object' ? error.detail.message : error.detail
            throw new Error(detail || '创建关系失败')
        }
    },

    // =========================================================================
    // SQL 示例
    // =========================================================================

    /**
     * 获取所有 SQL 示例
     */
    async getSqlExamples(): Promise<SqlExample[]> {
        const response = await fetch(`${API_BASE_URL}/config/sql-examples`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取 SQL 示例失败: ${response.status}`)
        }

        const data = await response.json()
        return data.examples || []
    },

    /**
     * 保存 SQL 示例（新建或更新）
     */
    async saveSqlExample(id: number | null, data: SqlExampleForm): Promise<void> {
        const url = id
            ? `${API_BASE_URL}/config/sql-examples/${id}`
            : `${API_BASE_URL}/config/sql-examples`

        const response = await fetch(url, {
            method: id ? 'PUT' : 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '保存失败')
        }
    },

    /**
     * 删除单个 SQL 示例
     */
    async deleteSqlExample(id: number): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/sql-examples/${id}`, {
            method: 'DELETE',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error('删除失败')
        }
    },

    /**
     * 批量删除 SQL 示例
     */
    async batchDeleteSqlExamples(ids: number[]): Promise<{ message: string }> {
        const response = await fetch(`${API_BASE_URL}/config/sql-examples/batch-delete`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '批量删除失败')
        }

        return response.json()
    },

    /**
     * 批量移动 SQL 示例到指定分组
     */
    async batchMoveSqlExamples(ids: number[], groupId: number | null): Promise<{ message: string }> {
        const response = await fetch(`${API_BASE_URL}/config/sql-examples/batch-move`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids, group_id: groupId }),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '批量移动失败')
        }

        return response.json()
    },

    /**
     * 切换 SQL 示例的启用状态
     */
    async toggleSqlExampleActive(id: number, isActive: boolean): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/sql-examples/${id}`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: isActive }),
        })

        if (!response.ok) {
            throw new Error('更新状态失败')
        }
    },

    // =========================================================================
    // SQL 分组
    // =========================================================================

    /**
     * 获取所有 SQL 分组
     */
    async getSqlGroups(): Promise<SqlGroup[]> {
        const response = await fetch(`${API_BASE_URL}/config/sql-groups`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取分组失败: ${response.status}`)
        }

        const data = await response.json()
        return data.groups || []
    },

    /**
     * 保存 SQL 分组（新建或更新）
     */
    async saveSqlGroup(id: number | null, data: SqlGroupForm): Promise<void> {
        const url = id
            ? `${API_BASE_URL}/config/sql-groups/${id}`
            : `${API_BASE_URL}/config/sql-groups`

        const response = await fetch(url, {
            method: id ? 'PUT' : 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '保存分组失败')
        }
    },

    /**
     * 删除 SQL 分组
     */
    async deleteSqlGroup(id: number): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/sql-groups/${id}`, {
            method: 'DELETE',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error('删除分组失败')
        }
    },

    // =========================================================================
    // 模板
    // =========================================================================

    /**
     * 获取所有模板
     */
    async getTemplates(): Promise<Template[]> {
        const response = await fetch(`${API_BASE_URL}/config/templates`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取模板失败: ${response.status}`)
        }

        const data = await response.json()
        return data.templates || []
    },

    /**
     * 获取单个模板详情
     */
    async getTemplate(id: number): Promise<Template> {
        const response = await fetch(`${API_BASE_URL}/config/templates/${id}`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || `获取模板失败: ${response.status}`)
        }

        return response.json()
    },

    /**
     * 创建新模板（上传文件）
     * @returns 新创建的模板 ID
     */
    async createTemplate(formData: FormData): Promise<number> {
        const response = await fetch(`${API_BASE_URL}/config/templates`, {
            method: 'POST',
            headers: getAuthHeader(),
            body: formData,
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '上传失败')
        }

        const data = await response.json()
        return data.id
    },

    /**
     * 更新模板
     */
    async updateTemplate(id: number, data: TemplateUpdatePayload): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/templates/${id}`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '更新失败')
        }
    },

    /**
     * 删除模板
     */
    async deleteTemplate(id: number): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/templates/${id}`, {
            method: 'DELETE',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error('删除失败')
        }
    },

    /**
     * 测试渲染模板
     */
    async testRenderTemplate(id: number): Promise<Blob> {
        const response = await fetch(`${API_BASE_URL}/config/templates/${id}/test-render`, {
            method: 'POST',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '测试渲染失败')
        }

        return response.blob()
    },

    async previewTemplate(id: number): Promise<{ html: string; mapping: PreviewMappingItem[] }> {
        const response = await fetch(`${API_BASE_URL}/config/templates/${id}/preview`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '预览失败')
        }

        return response.json()
    },

    async compileTemplate(id: number, bindings: unknown[], outputName?: string): Promise<{ compiled_path: string; compiled_name: string }> {
        const response = await fetch(`${API_BASE_URL}/config/templates/${id}/compile`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ bindings, output_name: outputName }),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '编译失败')
        }

        return response.json()
    },

    /**
     * 切换模板的启用状态
     */
    async toggleTemplateActive(id: number, isActive: boolean): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/templates/${id}`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: isActive }),
        })

        if (!response.ok) {
            throw new Error('更新状态失败')
        }
    },

    // ========== LLM 智能检测 ==========

    /**
     * 使用 LLM 优化空白字段标签
     */
    async optimizeBlankLabels(
        templateId: number,
        candidates: BlankFieldDetectResponse
    ): Promise<BlankFieldDetectResponse> {
        const response = await fetch(
            `${API_BASE_URL}/config/templates/${templateId}/optimize-blanks`,
            {
                method: 'POST',
                headers: {
                    ...getAuthHeader(),
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(candidates),
            }
        )

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '优化失败')
        }

        return response.json()
    },

    /**
     * 启动 LLM 智能检测后台任务
     */
    async startLLMDetection(templateId: number): Promise<{
        task_id: string;
        status: string;
        progress: number;
        stage: string;
    }> {
        const response = await fetch(
            `${API_BASE_URL}/config/templates/${templateId}/detect-blanks-llm/start`,
            {
                method: 'POST',
                headers: getAuthHeader(),
            }
        )

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '启动检测失败')
        }

        return response.json()
    },

    /**
     * 查询 LLM 检测任务状态
     */
    async getLLMDetectionStatus(taskId: string): Promise<{
        task_id: string;
        status: string;
        progress: number;
        stage: string;
        result?: CandidateField[];
        error?: string;
    }> {
        const response = await fetch(
            `${API_BASE_URL}/config/templates/detect-blanks-llm/status/${taskId}`,
            {
                headers: getAuthHeader(),
            }
        )

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '查询状态失败')
        }

        return response.json()
    },

    // =========================================================================
    // 模板分组
    // =========================================================================

    /**
     * 获取所有模板分组
     */
    async getTemplateGroups(): Promise<TemplateGroup[]> {
        const response = await fetch(`${API_BASE_URL}/config/templates/groups/list`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取模板分组失败: ${response.status}`)
        }

        const data = await response.json()
        return data.groups || []
    },

    /**
     * 保存模板分组（新建或更新）
     */
    async saveTemplateGroup(id: number | null, data: TemplateGroupForm): Promise<void> {
        const url = id
            ? `${API_BASE_URL}/config/templates/groups/${id}`
            : `${API_BASE_URL}/config/templates/groups`

        const response = await fetch(url, {
            method: id ? 'PUT' : 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || '保存分组失败')
        }
    },

    /**
     * 删除模板分组
     */
    async deleteTemplateGroup(id: number): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/templates/groups/${id}`, {
            method: 'DELETE',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error('删除分组失败')
        }
    },
}
