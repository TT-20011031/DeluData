/**
 * Wiki 模块前端服务层。
 *
 * 封装 /api/wiki/* 的 HTTP 调用，统一鉴权与错误处理。
 */
import { getAuthHeader } from '@/stores/authStore'
import { API_BASE_URL } from '@/config'
import type {
    RetrievalDiagnosticsRequest,
    RetrievalDiagnosticsResponse,
    WikiCompileRequest,
    WikiCompileResponse,
    WikiCompileTaskAccepted,
    WikiCompileTaskCancelResponse,
    WikiCompileTaskStatus,
    WikiCandidateGovernanceResponse,
    WikiGovernanceActionRequest,
    WikiGraphResponse,
    WikiLintReport,
    WikiPageDetail,
    WikiPageListResponse,
    WikiPageUpdateRequest,
} from '@/types/wiki'

async function parseErrorMessage(response: Response, fallback: string): Promise<string> {
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
        const payload = await response.json().catch(() => ({}))
        const detail = payload?.detail
        if (typeof detail === 'string' && detail.trim()) {
            return detail
        }
        if (payload?.message && typeof payload.message === 'string') {
            return payload.message
        }
    }
    const text = await response.text().catch(() => '')
    return text.trim() || fallback
}

async function ensureOk(response: Response, fallback: string): Promise<void> {
    if (!response.ok) {
        throw new Error(await parseErrorMessage(response, fallback))
    }
}

export interface ListPagesParams {
    domain?: string
    status?: string
    keyword?: string
    scope?: string
    department_id?: number
    business_domain?: string
    document_type?: string
    confidentiality_level?: string
    limit?: number
    offset?: number
}

export const wikiService = {
    async listPages(params: ListPagesParams = {}): Promise<WikiPageListResponse> {
        const query = new URLSearchParams()
        if (params.domain) query.set('domain', params.domain)
        if (params.status) query.set('status', params.status)
        if (params.keyword) query.set('keyword', params.keyword)
        if (params.scope) query.set('scope', params.scope)
        if (params.department_id !== undefined) query.set('department_id', String(params.department_id))
        if (params.business_domain) query.set('business_domain', params.business_domain)
        if (params.document_type) query.set('document_type', params.document_type)
        if (params.confidentiality_level) query.set('confidentiality_level', params.confidentiality_level)
        if (params.limit !== undefined) query.set('limit', String(params.limit))
        if (params.offset !== undefined) query.set('offset', String(params.offset))

        const url = `${API_BASE_URL}/wiki/pages${query.size ? `?${query}` : ''}`
        const res = await fetch(url, { headers: getAuthHeader() })
        await ensureOk(res, 'failed_to_list_pages')
        return res.json()
    },

    async getGraph(params: Omit<ListPagesParams, 'status' | 'keyword' | 'limit' | 'offset'> = {}): Promise<WikiGraphResponse> {
        const query = new URLSearchParams()
        if (params.domain) query.set('domain', params.domain)
        if (params.scope) query.set('scope', params.scope)
        if (params.department_id !== undefined) query.set('department_id', String(params.department_id))
        if (params.business_domain) query.set('business_domain', params.business_domain)
        if (params.document_type) query.set('document_type', params.document_type)
        if (params.confidentiality_level) query.set('confidentiality_level', params.confidentiality_level)

        const url = `${API_BASE_URL}/wiki/graph${query.size ? `?${query}` : ''}`
        const res = await fetch(url, { headers: getAuthHeader() })
        await ensureOk(res, 'failed_to_get_wiki_graph')
        return res.json()
    },

    async getPage(slug: string): Promise<WikiPageDetail> {
        const res = await fetch(
            `${API_BASE_URL}/wiki/pages/${encodeURIComponent(slug)}`,
            { headers: getAuthHeader() },
        )
        if (res.status === 404) {
            throw new Error('page_not_found')
        }
        await ensureOk(res, 'failed_to_get_page')
        return res.json()
    },

    async updatePage(slug: string, payload: WikiPageUpdateRequest): Promise<{
        success: boolean
        changed?: boolean
        version?: number
        error?: string
    }> {
        const res = await fetch(
            `${API_BASE_URL}/wiki/pages/${encodeURIComponent(slug)}`,
            {
                method: 'PUT',
                headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            },
        )
        await ensureOk(res, 'failed_to_update_page')
        return res.json()
    },

    async compile(payload: WikiCompileRequest): Promise<WikiCompileResponse> {
        const res = await fetch(`${API_BASE_URL}/wiki/compile`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        await ensureOk(res, 'failed_to_compile')
        return res.json()
    },

    /**
     * [体验] 异步入队编译任务，立即返回 task_id；前端通过 getCompileTask 轮询进度。
     * 推荐替代同步 compile()，避免 HTTP 长连接超时和按钮死转。
     */
    async compileAsync(payload: WikiCompileRequest): Promise<WikiCompileTaskAccepted> {
        const res = await fetch(`${API_BASE_URL}/wiki/compile?async=true`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        await ensureOk(res, 'failed_to_enqueue_compile')
        return res.json()
    },

    /** [体验] 查询编译任务进度（轮询用）。 */
    async getCompileTask(taskId: string): Promise<WikiCompileTaskStatus> {
        const res = await fetch(
            `${API_BASE_URL}/wiki/tasks/${encodeURIComponent(taskId)}`,
            { headers: getAuthHeader() },
        )
        await ensureOk(res, 'failed_to_get_compile_task')
        return res.json()
    },

    /**
     * [体验] 请求取消编译任务（软取消语义）。
     *
     * pending 任务立即转 cancelled；running 任务转 cancel_requested，
     * worker 会在下一个候选开始前停止启动新 LLM 调用，已成功落库的页保留。
     * 前端应继续轮询 getCompileTask 直到终态 `cancelled`。
     */
    async cancelCompileTask(taskId: string): Promise<WikiCompileTaskCancelResponse> {
        const res = await fetch(
            `${API_BASE_URL}/wiki/tasks/${encodeURIComponent(taskId)}/cancel`,
            { method: 'POST', headers: getAuthHeader() },
        )
        await ensureOk(res, 'failed_to_cancel_compile_task')
        return res.json()
    },

    async lint(): Promise<WikiLintReport> {
        const res = await fetch(`${API_BASE_URL}/wiki/lint`, {
            method: 'POST',
            headers: getAuthHeader(),
        })
        await ensureOk(res, 'failed_to_lint')
        return res.json()
    },

    async listCandidateGovernance(params: {
        keyword?: string
        status?: string
        scope?: string
        department_id?: number
        business_domain?: string
        document_type?: string
        confidentiality_level?: string
        limit?: number
        offset?: number
    } = {}): Promise<WikiCandidateGovernanceResponse> {
        const query = new URLSearchParams()
        if (params.keyword) query.set('keyword', params.keyword)
        if (params.status) query.set('status', params.status)
        if (params.scope) query.set('scope', params.scope)
        if (params.department_id !== undefined) query.set('department_id', String(params.department_id))
        if (params.business_domain) query.set('business_domain', params.business_domain)
        if (params.document_type) query.set('document_type', params.document_type)
        if (params.confidentiality_level) query.set('confidentiality_level', params.confidentiality_level)
        if (params.limit !== undefined) query.set('limit', String(params.limit))
        if (params.offset !== undefined) query.set('offset', String(params.offset))
        const url = `${API_BASE_URL}/wiki/governance/candidates${query.size ? `?${query}` : ''}`
        const res = await fetch(url, { headers: getAuthHeader() })
        await ensureOk(res, 'failed_to_list_candidate_governance')
        return res.json()
    },

    async applyGovernanceAction(
        slug: string,
        payload: WikiGovernanceActionRequest,
    ): Promise<{ success: boolean; changed?: boolean; version?: number; error?: string }> {
        const res = await fetch(
            `${API_BASE_URL}/wiki/pages/${encodeURIComponent(slug)}/governance-action`,
            {
                method: 'POST',
                headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            },
        )
        await ensureOk(res, 'failed_to_apply_governance_action')
        return res.json()
    },

    /** [M3.5] Wiki 域聚合（KnowledgeScopePicker 一级 Tab 数据源）。 */
    async listDomains(samplePerDomain = 5): Promise<WikiDomainListResponse> {
        const url = `${API_BASE_URL}/wiki/domains?sample_per_domain=${samplePerDomain}`
        const res = await fetch(url, { headers: getAuthHeader() })
        await ensureOk(res, 'failed_to_list_domains')
        return res.json()
    },

    /** [M3.5] 检索路由评估汇总。 */
    async getRouteMetrics(days = 7): Promise<WikiRouteSummary> {
        const url = `${API_BASE_URL}/wiki/metrics/route?days=${days}`
        const res = await fetch(url, { headers: getAuthHeader() })
        await ensureOk(res, 'failed_to_get_route_metrics')
        return res.json()
    },

    /** [M4.2] 当前工作区 Wiki 配额使用情况。 */
    async getQuota(): Promise<WikiQuotaStatus> {
        const url = `${API_BASE_URL}/wiki/quota`
        const res = await fetch(url, { headers: getAuthHeader() })
        await ensureOk(res, 'failed_to_get_quota')
        return res.json()
    },

    /** Retrieval diagnostics for admin/debug knowledge workflows. */
    async diagnoseRetrieval(
        payload: RetrievalDiagnosticsRequest,
    ): Promise<RetrievalDiagnosticsResponse> {
        const res = await fetch(`${API_BASE_URL}/wiki/diagnostics/retrieval`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        await ensureOk(res, 'failed_to_diagnose_retrieval')
        return res.json()
    },

    async listConversationDiagnostics(params: { session_id?: string; limit?: number } = {}): Promise<ConversationDiagnosticsResponse> {
        const query = new URLSearchParams()
        if (params.session_id) query.set('session_id', params.session_id)
        if (params.limit) query.set('limit', String(params.limit))
        const suffix = query.toString() ? `?${query.toString()}` : ''
        const res = await fetch(`${API_BASE_URL}/wiki/diagnostics/conversation${suffix}`, {
            headers: getAuthHeader(),
        })
        await ensureOk(res, 'failed_to_list_conversation_diagnostics')
        return res.json()
    },
}

// ============ [M3.5] 类型 ============

export interface WikiDomainBucket {
    domain: string
    page_count: number
    sample_titles: string[]
}

export interface WikiDomainListResponse {
    items: WikiDomainBucket[]
}

export interface WikiPathBucket {
    count: number
    avg_latency_ms: number
    wiki_gap_count: number
}

export interface WikiRouteSummary {
    window_days: number
    since: string
    total: number
    by_path: Record<string, WikiPathBucket>
    wiki_hit_rate: number
    wiki_gap_rate: number
    avg_latency_ms: number
    wiki_fallback_to_rag_count?: number
    wrong_tool_repair_count?: number
    wiki_hit_but_not_used_count?: number
}

export interface ConversationDiagnosticContextItem {
    source: string | null
    title: string | null
    slug: string | null
    file_name: string | null
    page_number: number | null
    preview: string
}

export interface ConversationDiagnosticItem {
    id: string
    created_at: string
    session_id: string | null
    message_id: string | null
    knowledge_path: string
    knowledge_path_source: string | null
    knowledge_path_reason: string | null
    user_query: string
    chunks_used: number
    wiki_chunks_count: number
    wiki_chunks_used: number
    rag_chunks_used: number
    wiki_fallback_to_rag: boolean
    answer_context_source: string
    issues: string[]
    final_context: ConversationDiagnosticContextItem[]
    tool_repair_trace: Record<string, unknown>[]
    wrong_tool_repair: boolean
    route_action: string | null
    repair_reason: string | null
}

export interface ConversationDiagnosticsResponse {
    items: ConversationDiagnosticItem[]
}

// ============ [M4.2] 配额类型 ============

export interface WikiQuotaPagesUsage {
    current: number
    limit: number
    usage_pct: number
}

export interface WikiQuotaLinksUsage {
    max_per_page: number
    max_outgoing_observed: number
}

export interface WikiQuotaStatus {
    workspace_id: string
    max_pages_per_workspace: number
    max_links_per_page: number
    pages: WikiQuotaPagesUsage
    links: WikiQuotaLinksUsage
    near_limit: boolean
}
