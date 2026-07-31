/**
 * Wiki 模块类型定义（Karpathy LLM Wiki 模式）
 */

export type WikiDomain =
    | 'general'
    | 'policy'
    | 'product'
    | 'customer'
    | 'term'
    | 'decision'
    | string

export type WikiPageStatus =
    | 'candidate'
    | 'draft'
    | 'published'
    | 'verified'
    | 'deprecated'
    | 'archived'

export type WikiLinkType =
    | 'mentions'
    | 'related'
    | 'supersedes'
    | 'contradicts'
    | string

export type WikiLinkStatus =
    | 'active'
    | 'pending_review'
    | 'resolved'
    | 'dismissed'
    | string

export interface WikiGovernanceSnapshot {
    score: number
    priority: 'high' | 'medium' | 'low' | string
    signals: Record<string, unknown>
    risks: string[]
    can_publish: boolean
    publish_blockers: string[]
    source_security: Record<string, unknown>
    metadata_scope?: Record<string, unknown>
    manual_flags: Record<string, unknown>
}

export interface WikiSourceScope {
    source_count: number
    department_ids: number[]
    is_cross_department: boolean
    is_unclassified: boolean
    visibility_counts: Record<string, number>
    document_type_counts: Record<string, number>
    business_domain_counts: Record<string, number>
    confidentiality_counts: Record<string, number>
    expired_source_count: number
    has_expired_source: boolean
}

// =============================================================================
// 列表 / 详情
// =============================================================================

export interface WikiPageSummary {
    id: string
    slug: string
    title: string
    summary: string | null
    domain: WikiDomain
    status: WikiPageStatus
    version: number
    char_count: number
    last_compiled_at: string | null
    updated_at: string | null
    governance?: WikiGovernanceSnapshot | null
    source_scope?: WikiSourceScope | null
}

export interface WikiPageListResponse {
    total: number
    items: WikiPageSummary[]
}

export interface WikiGraphNode {
    id: string
    node_type?: 'entity' | 'file'
    slug: string
    title: string
    summary: string | null
    domain: WikiDomain
    status: WikiPageStatus | string
    degree: number
    source_scope?: WikiSourceScope | null
    updated_at: string | null
    file_id?: string | null
    file_type?: string | null
    file_name?: string | null
    source_count?: number
}

export interface WikiGraphEdge {
    id: string
    source: string
    target: string
    link_type: WikiLinkType | 'source'
    status: WikiLinkStatus | string
    confidence: number
    note: string | null
    evidence_count: number
}

export interface WikiGraphStats {
    page_count: number
    link_count: number
    isolated_count: number
    domain_counts: Record<string, number>
    link_type_counts: Record<string, number>
    entity_count?: number
    file_count?: number
    wiki_link_count?: number
    source_edge_count?: number
    total_edge_count?: number
}

export interface WikiGraphResponse {
    nodes: WikiGraphNode[]
    edges: WikiGraphEdge[]
    stats: WikiGraphStats
}

export interface WikiLinkBrief {
    id: string
    link_type: WikiLinkType
    status: WikiLinkStatus
    note: string | null
    target_slug?: string
    target_title?: string
    source_slug?: string
    source_title?: string
}

export interface WikiEvidenceChunkBrief {
    chunk_id: string
    file_id: string
    file_name: string | null
    page_number: number | null
    preview: string
}

export interface WikiSourceBrief {
    id: string
    file_id: string
    file_name: string | null
    chunk_ids: string[]
    evidence_chunks?: WikiEvidenceChunkBrief[]
    excerpt: string | null
    visibility?: string | null
    department_id?: number | null
    owner_id?: string | null
    document_type?: string | null
    business_domain?: string | null
    confidentiality_level?: string | null
    effective_from?: string | null
    effective_until?: string | null
    external_ref?: string | null
}

export interface WikiRevisionBrief {
    version: number
    committed_by: string
    commit_message: string | null
    committed_at: string | null
}

export interface WikiCompileMeta {
    model?: string
    elapsed_seconds?: number
    operation?: string
    prompt_tokens?: number
    completion_tokens?: number
    total_tokens?: number
    evidence_chunks?: number
    truncated?: boolean
    outgoing_links_count?: number
    candidate_merge_count?: number
    governance?: Record<string, unknown>
}

export interface WikiPageDetail {
    id: string
    slug: string
    title: string
    aliases: string[]
    domain: WikiDomain
    status: WikiPageStatus
    summary: string | null
    markdown_body: string
    version: number
    char_count: number
    token_count: number
    last_compiled_at: string | null
    last_compiled_by: string | null
    compile_meta: WikiCompileMeta | null
    outgoing_links: WikiLinkBrief[]
    incoming_links: WikiLinkBrief[]
    sources: WikiSourceBrief[]
    revisions: WikiRevisionBrief[]
    governance?: WikiGovernanceSnapshot | null
    source_scope?: WikiSourceScope | null
}

// =============================================================================
// 请求体
// =============================================================================

export interface WikiCompileRequest {
    file_ids?: string[]
    scope?: 'workspace' | null
}

export interface WikiPageUpdateRequest {
    markdown_body?: string
    summary?: string
    title?: string
    domain?: WikiDomain
    status?: WikiPageStatus
    commit_message?: string
}

export interface WikiCandidateGovernanceResponse {
    total: number
    items: Array<WikiPageSummary & { governance: WikiGovernanceSnapshot }>
}

export type WikiGovernanceAction =
    | 'publish'
    | 'verify'
    | 'draft'
    | 'archive'
    | 'deprecate'
    | 'boost'
    | 'unboost'
    | 'mark_duplicate'
    | 'mark_conflict'
    | 'merge'

export interface WikiGovernanceActionRequest {
    action: WikiGovernanceAction
    target_slug?: string
    note?: string
    commit_message?: string
}

// =============================================================================
// Compile 结果
// =============================================================================

export interface WikiCompileLinkStats {
    created: number
    deleted: number
    broken: number
    reverse_added: number
}

export interface WikiCompilePageResult {
    operation: 'create' | 'update' | string
    page_id: string
    slug: string
    version: number
    status?: WikiPageStatus | string
    link_stats?: WikiCompileLinkStats
    conflicts_added?: number
}

export interface WikiCompileApplied {
    created: number
    updated: number
    links: WikiCompileLinkStats
    conflicts_added: number
    pages: WikiCompilePageResult[]
}

export interface WikiCompileResponse {
    success: boolean
    trigger?: string
    user_id?: string | null
    files: number
    candidates: number
    applied: WikiCompileApplied
    skipped: Array<{ slug: string; title: string; reason: string }>
    errors: string[]
    elapsed_seconds: number
    error?: string
}

// ============ [体验] 异步编译：入队 + 任务状态 ============

export type WikiCompileTaskState =
    | 'pending'
    | 'running'
    | 'cancel_requested'
    | 'succeeded'
    | 'failed'
    | 'cancelled'

/** POST /api/wiki/compile?async=true 入队成功后返回。 */
export interface WikiCompileTaskAccepted {
    task_id: string
    status: WikiCompileTaskState
    trigger_type: string
    workspace_id: string
    /** True 表示同 workspace + trigger 已有 active 任务，本次复用了它。 */
    deduplicated: boolean
}

/** GET /api/wiki/tasks/{task_id} 任务状态轮询返回。 */
export interface WikiCompileTaskStatus {
    task_id: string
    workspace_id: string
    trigger_type: string
    status: WikiCompileTaskState
    stage: string | null
    progress: number
    attempt: number
    max_attempts: number
    started_at: string | null
    finished_at: string | null
    error_message: string | null
    /** 终态摘要（succeeded / cancelled 均含核心统计字段）。 */
    result: {
        files?: number
        candidates?: number
        elapsed_seconds?: number
        created?: number
        updated?: number
        links?: WikiCompileLinkStats
        conflicts_added?: number
        skipped?: number
        errors?: string[]
        total_prompt_tokens?: number
        total_completion_tokens?: number
        truncated_pages?: number
        candidate_merges?: number
        /** 仅 cancelled 终态为 true；succeeded 时为 false/undefined。 */
        cancelled?: boolean
    } | null
}

/** POST /api/wiki/tasks/{task_id}/cancel 取消请求响应。 */
export type WikiCompileCancelOutcome =
    | 'cancelled_immediately'
    | 'cancel_requested'
    | 'already_cancelling'
    | 'already_terminal'
    | 'not_found'

export interface WikiCompileTaskCancelResponse {
    task_id: string
    /** 取消前的 status；not_found 时为 null。 */
    previous_status: WikiCompileTaskState | null
    outcome: WikiCompileCancelOutcome
}

// =============================================================================
// Lint 报告
// =============================================================================

export type WikiLintIssueType =
    | 'orphan'
    | 'broken_link'
    | 'open_conflict'
    | 'stale'
    | string

export type WikiLintSeverity = 'low' | 'medium' | 'high' | string

export interface WikiLintIssue {
    issue_id: string
    issue_type: WikiLintIssueType
    severity: WikiLintSeverity
    page_id: string | null
    page_slug: string | null
    page_title: string | null
    payload: Record<string, unknown>
}

export interface WikiLintReport {
    workspace_id: string
    generated_at: string
    page_count: number
    link_count: number
    open_conflicts: number
    issues: WikiLintIssue[]
}

// =============================================================================
// Retrieval diagnostics
// =============================================================================

export type KnowledgePath = 'rag' | 'wiki' | 'both'

export interface RetrievalDiagnosticsRequest {
    query: string
    knowledge_path?: KnowledgePath
    doc_scope?: Record<string, unknown> | null
    top_k?: number
    deep_search?: boolean
}

export interface RetrievalDiagnosticHit {
    source: 'rag' | 'wiki' | string
    chunk_id: string | null
    title: string | null
    file_id: string | null
    file_name: string | null
    page_number: number | null
    score: number
    rerank_score: number | null
    status: string | null
    domain: string | null
    kept: boolean
    reason: string | null
    preview: string
    content: string
}

export interface RetrievalDiagnosticsResponse {
    query: string
    knowledge_path: KnowledgePath | string
    knowledge_path_source: string
    knowledge_path_reason: string
    permissions: Record<string, unknown>
    metadata_filter_summary?: Record<string, unknown>
    route_trace?: Record<string, unknown>
    counts: Record<string, number>
    hits: RetrievalDiagnosticHit[]
    final_context: RetrievalDiagnosticHit[]
    issues: string[]
}
