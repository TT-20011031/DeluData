/**
 * 扩展配置相关类型定义
 * 
 * 包含 SQL 示例和文档模板的类型
 */

// =============================================================================
// SQL 示例相关
// =============================================================================

/** SQL 分组 */
export interface SqlGroup {
    id: number
    name: string
    description: string | null
    color: string
    example_count: number
    created_at: string
    updated_at: string
}

/** SQL 示例 */
export type SqlExampleValidationStatus = 'draft' | 'valid' | 'invalid' | 'stale'

export interface SqlExampleParameter {
    key: string
    label: string
    data_type: 'text' | 'number' | 'date' | string
    table_id: number
    column_id: number
    field: string
    required: boolean
    aliases: string[]
    example_value: string
    operator?: 'eq' | 'contains' | 'month_range' | string
}

export interface SqlExample {
    id: number
    question: string
    sql: string
    description: string | null
    tables: string | null
    is_active: boolean
    group_id: number | null
    validation_status: SqlExampleValidationStatus
    validation_errors: Array<{ code: string; message: string; [key: string]: unknown }>
    parameters: SqlExampleParameter[]
    normalized_question: string
    validated_at: string | null
    last_matched_at: string | null
    match_count: number
    created_at: string
    updated_at: string
}

/** SQL 示例表单 */
export interface SqlExampleForm {
    question: string
    sql: string
    description: string
    tables: string
    group_id: number | null
    parameters: SqlExampleParameter[]
    is_active: boolean
}

export interface SqlExampleValidationResult {
    status: SqlExampleValidationStatus
    errors: Array<{ code: string; message: string; [key: string]: unknown }>
    parameters: SqlExampleParameter[]
    normalized_question: string
    preview_sql: string
}

/** SQL 分组表单 */
export interface SqlGroupForm {
    name: string
    description: string
    color: string
}

// =============================================================================
// 模板相关
// =============================================================================

// =============================================================================
// 语义模型相关
// =============================================================================

export type SemanticStatus = 'suggested' | 'confirmed' | 'disabled'
export type SemanticSyncState = 'current' | 'stale' | 'orphaned'
export type SemanticRuntimeMode = 'disabled' | 'shadow' | 'trusted'

export interface SemanticLifecycle {
    sync_state: SemanticSyncState
    origin_source: string
    management_mode: 'system' | 'human' | string
    confidence: number | null
    evidence_json: Record<string, unknown>
    stale_reason_json: Record<string, unknown>
    schema_fingerprint: string | null
    last_seen_scan_id: number | null
    confirmed_by: string | null
    confirmed_at: string | null
    business_semantics_status?: 'pending' | 'confirmed' | 'stale'
    business_semantics_revision?: number
    business_semantics_reviewed_by?: string | null
    business_semantics_reviewed_at?: string | null
}

export interface SemanticDatasource {
    id: number
    workspace_id: string
    name: string
    host: string
    port: number
    database: string
    dialect: string
    is_active: boolean
    semantic_sql_enabled: boolean
    semantic_sql_fallback_enabled: boolean
    runtime_mode: SemanticRuntimeMode
    access_bootstrap_required?: boolean
    active_evidence_set_id?: number | null
    schema_fingerprint: string | null
    last_applied_scan_id: number | null
    last_scan_at: string | null
    created_at: string
    updated_at: string
}

export interface SemanticTable extends SemanticLifecycle {
    id: number
    workspace_id: string
    datasource_id: number
    physical_name: string
    business_name: string
    description: string | null
    physical_comment: string | null
    synonyms: string[]
    status: SemanticStatus
    is_queryable: boolean
    is_sensitive: boolean
}

export type RowPermissionMode = 'off' | 'required'
export type RowPermissionSubjectType = 'role' | 'user'

export type ConditionRule = {
    column_id: number
    operator: string
    value?: unknown
    value_source?: string
}

export type ConditionGroup = {
    op: 'AND' | 'OR'
    rules: Array<ConditionRule | ConditionGroup>
}

export type RowPermissionRule = {
    id: number
    table_id: number
    subject_type: RowPermissionSubjectType
    subject_id: string
    enabled: boolean
    condition_json: ConditionGroup
}

export type SemanticAccessPolicyStatus = 'draft' | 'active' | 'disabled' | 'archived'
export type SemanticAccessSubjectType = 'all' | 'role' | 'user'
export type SemanticAccessAssetType = 'table' | 'column' | 'metric'
export type SemanticAccessEffectType = 'visible' | 'hidden' | 'row_filter'

export interface SemanticAccessSubject {
    type: SemanticAccessSubjectType
    id?: string
    label?: string
}

export type SemanticAccessRowScope =
    | { type: 'authorization' }
    | { type: 'all' }
    | { type: 'self'; column_id: number; identity: 'user_id' | 'username' }
    | { type: 'department'; column_id: number; include_descendants: boolean }
    | { type: 'target_org'; unowned_access?: 'table_grantees' }
    | { type: 'target_org_tree'; unowned_access?: 'table_grantees' }
    | { type: 'primary_assignment' }
    | { type: 'all_assignments' }
    | { type: 'custom_org'; org_unit_ids: number[]; include_descendants: boolean }
    | { type: 'custom'; condition: ConditionRule | ConditionGroup }

export interface SemanticAccessTableRule {
    table_id: number
    decision?: 'visible' | 'hidden'
    hidden_column_ids: number[]
    hidden_metric_ids: number[]
    row_scope?: SemanticAccessRowScope
}

export interface SemanticAccessValidationIssue {
    code: string
    message: string
}

export interface SemanticAccessPolicyEffect {
    policy_id: number
    effect_key: string
    subject_type: SemanticAccessSubjectType
    subject_id: string
    asset_type: SemanticAccessAssetType
    asset_id: number
    effect_type: SemanticAccessEffectType
    condition_json: Record<string, unknown>
    compiled_sql: string | null
    priority: number
}

export interface SemanticAccessPolicy {
    id: number
    workspace_id: string
    datasource_id: number
    name: string
    description: string | null
    source_type: 'natural_language' | 'template' | 'legacy_import' | 'manual'
    source_text: string | null
    source_ref: string | null
    status: SemanticAccessPolicyStatus
    subject: SemanticAccessSubject
    tables: SemanticAccessTableRule[]
    compile_summary_json: {
        effect_count?: number
        subject_count?: number
        affected_assets?: Partial<Record<SemanticAccessAssetType, number[]>>
        schema_fingerprint?: string | null
        model_version?: number
    }
    validation_json: {
        blockers?: SemanticAccessValidationIssue[]
        warnings?: SemanticAccessValidationIssue[]
    }
    model_version: number
    policy_version: number
    schema_fingerprint: string | null
    created_by: string | null
    activated_by: string | null
    activated_at: string | null
    created_at: string
    updated_at: string
    preview_effects?: SemanticAccessPolicyEffect[]
}

export interface SemanticAccessPolicyDraftInput {
    datasource_id?: number
    name?: string
    description?: string
    source_text?: string
    subject: SemanticAccessSubject
    tables: SemanticAccessTableRule[]
}

export interface SemanticAccessNaturalLanguageResult {
    draft_patch: Omit<SemanticAccessPolicyDraftInput, 'datasource_id'>
    validation: SemanticAccessPolicy['validation_json']
    summary: SemanticAccessPolicy['compile_summary_json']
}

export interface SemanticAccessCompileResult {
    policy: SemanticAccessPolicy
    effects: SemanticAccessPolicyEffect[]
    validation: SemanticAccessPolicy['validation_json']
    summary: SemanticAccessPolicy['compile_summary_json']
}

export interface SemanticAccessDecision {
    asset_type: SemanticAccessAssetType
    asset_id: number
    table_id: number
    asset_name: string
    allowed: boolean
    code?: string
    reason?: string
    action?: string
    policy_ids?: number[]
    sources?: Array<{
        subject_type: SemanticAccessSubjectType
        subject_id: string
        policy_id: number
    }>
    dependency_column_ids?: number[]
}

export interface SemanticAccessEffectivePreview {
    user: {
        user_id: string
        username: string | null
        role_ids: string[]
        role_names: string[]
        data_scope: number
        dept_id: number | null
        scope_dept_ids: number[]
    }
    policy_ids: number[]
    decisions: SemanticAccessDecision[]
    row_predicates: Array<{
        table_id: number
        status: 'ready' | 'blocked'
        scope?: 'all' | 'filtered' | 'none'
        sql?: string
        message?: string
    }>
}

export interface SemanticAssetTag {
    id?: number
    asset_type: SemanticAccessAssetType
    asset_id: number
    tag_type: string
    tag_value: string
    source?: string
    confidence?: number | null
}

export interface SemanticColumn extends SemanticLifecycle {
    id: number
    workspace_id: string
    datasource_id: number
    table_id: number
    physical_table: string
    physical_name: string
    data_type: string
    business_name: string
    description: string | null
    physical_comment: string | null
    synonyms: string[]
    status: SemanticStatus
    is_queryable: boolean
    is_sensitive: boolean
    is_primary_key: boolean
    is_indexed: boolean
    ordinal_position: number
}

export interface SemanticMetric extends SemanticLifecycle {
    id: number
    workspace_id: string
    datasource_id: number
    name: string
    business_name: string
    description: string | null
    formula: string
    aggregation: string | null
    table_id: number
    column_id: number | null
    time_column_id: number | null
    default_grain: string | null
    synonyms: string[]
    status: SemanticStatus
    is_queryable: boolean
    is_sensitive: boolean
}

export interface SemanticRelationship extends Omit<SemanticLifecycle, 'confidence'> {
    id: number
    workspace_id: string
    datasource_id: number
    left_table_id: number
    right_table_id: number
    left_column_id: number
    right_column_id: number
    relationship_type: string
    confidence: number
    status: SemanticStatus
    is_queryable: boolean
    description: string | null
}

export interface SemanticQueryRun {
    id: number
    workspace_id: string
    user_id: string | null
    session_id: string | null
    question: string
    semantic_enabled: boolean
    fallback_used: boolean
    status: string
    error_type: string | null
    sql: string | null
    referenced_tables: string[]
    row_count: number
    execution_time_ms: number
    runtime_mode: SemanticRuntimeMode
    correlation_id: string | null
    semantic_result_json: Record<string, unknown> | null
    legacy_result_json: Record<string, unknown> | null
    comparison_json: Record<string, unknown> | null
    returned_chain: string | null
    created_at: string
}

export interface SemanticSchemaDiffItem {
    object_type: 'table' | 'column'
    change_type: 'added' | 'modified' | 'removed'
    physical_identity: string
    table_name?: string
    before: Record<string, unknown> | null
    after: Record<string, unknown> | null
    severity: 'info' | 'warning' | 'critical'
    blocking: boolean
}

export interface SemanticScanRun {
    scan_id: number
    status: 'previewed' | 'applied' | 'expired' | 'failed'
    base_fingerprint: string | null
    target_fingerprint: string
    expires_at: string | null
    applied_at: string | null
    summary: Record<string, number>
    diff_items: SemanticSchemaDiffItem[]
    affected_assets: Array<Record<string, unknown>>
    blocking_changes: SemanticSchemaDiffItem[]
}

export interface SemanticReadiness {
    status: 'not_scanned' | 'degraded' | 'partially_ready' | 'ready'
    runtime_mode: SemanticRuntimeMode
    structure: Record<string, unknown>
    semantics: {
        queryable_tables: number
        queryable_columns: number
        confirmed_metrics: number
        confirmed_relationships: number
        stale_assets: number
    }
    security: { readonly_configured: boolean; blocking: boolean }
    governance: SemanticGovernanceReadiness
    blockers: string[]
}

export interface SemanticGovernanceReadiness {
    last_run: SemanticGovernanceRun | null
    evidence_freshness: 'fresh' | 'stale' | 'missing'
    profile_coverage: number
    pending_candidates: number
    high_risk_candidates: number
    recent_auto_applied: number
}

export interface SemanticGovernanceRun {
    run_id: number
    status: 'pending' | 'running' | 'cancel_requested' | 'completed' | 'partial' | 'failed' | 'cancelled'
    stage: string
    progress: number
    trigger_type: 'manual' | 'schema_apply' | 'retry' | string
    scan_id: number | null
    schema_fingerprint: string | null
    observe_only: boolean
    summary: Record<string, unknown>
    budget: Record<string, number>
    error_message: string | null
    worker_id?: string | null
    attempt?: number
    max_attempts?: number
    lease_expires_at?: string | null
    heartbeat_at?: string | null
    cancel_requested_at?: string | null
    retryable?: boolean
    created_at: string
    started_at: string | null
    completed_at: string | null
    coalesced?: boolean
}

export type SemanticGovernanceCandidateStatus =
    | 'proposed'
    | 'needs_review'
    | 'accepted'
    | 'rejected'
    | 'auto_applied'
    | 'blocked'
    | 'superseded'
    | 'expired'
    | 'rolled_back'

export interface SemanticGovernanceEvidenceFact {
    evidence_id: number
    run_id: number | null
    subject_type: string
    subject_id: number | null
    claim_type: string
    claim_key: string
    value: Record<string, unknown>
    source_type: string
    source_ref: string | null
    direction: 'support' | 'conflict'
    reliability: number
    strength: number
    observed_at: string
    expires_at: string | null
}

export interface SemanticGovernanceEditableField {
    key: string
    label: string
    type: 'text' | 'textarea' | 'tags' | 'boolean' | 'select' | string
    value: unknown
}

export interface SemanticGovernanceCandidate {
    candidate_id: number
    run_id: number | null
    target_type: 'tables' | 'columns' | 'metrics' | 'relationships' | string
    target_id: number
    candidate_type: string
    title: string
    before: Record<string, unknown>
    proposed_patch: Record<string, unknown>
    applied_patch: Record<string, unknown>
    supporting_evidence: Array<Record<string, unknown>>
    conflicting_evidence: Array<Record<string, unknown>>
    source_types: string[]
    score: number
    score_version: string
    risk_level: 'low' | 'medium' | 'high' | 'critical' | string
    status: SemanticGovernanceCandidateStatus
    auto_eligible: boolean
    deterministic_check_passed: boolean
    policy_version: string
    decision_reason: string | null
    decided_by: string | null
    decided_at: string | null
    applied_at: string | null
    created_at: string
    updated_at: string
    priority_reason?: string
    evidence_summaries?: string[]
    editable_fields?: SemanticGovernanceEditableField[]
    target_context?: Record<string, unknown>
}

export interface SemanticGovernancePolicy {
    id: number
    enabled: boolean
    observe_only: boolean
    run_after_scan?: boolean
    exact_row_threshold: number
    sample_row_limit: number
    table_timeout_sec: number
    max_tables_per_run: number
    max_run_seconds: number
    review_threshold: number
    high_confidence_threshold: number
    auto_apply_threshold: number
    min_auto_evidence_sources: number
    auto_action_types: string[]
    policy_version: string
    updated_at: string | null
}

export interface SemanticQuestionReadiness {
    answerable: boolean
    status: 'ready' | 'needs_clarification' | 'blocked' | 'unsafe'
    error_type: string | null
    blockers: string[]
    required_actions: string[]
    referenced_objects: Record<string, unknown>
    intent?: Record<string, unknown>
}

export interface SemanticResultDiagnostic {
    type: string
    severity: 'info' | 'warning' | 'error' | string
    message: string
    [key: string]: unknown
}

export interface SemanticEvalCase {
    case_id: string
    question: string
    test_dimension: string
    expected_focus: string[]
    expected_limit: number | null
}

export interface SemanticEvalChainResult {
    chain: 'semantic' | 'legacy' | string
    status: string
    success: boolean
    sql: string | null
    original_sql?: string | null
    error_type: string | null
    error: string | null
    row_count: number
    execution_time_ms: number
    diagnostics: SemanticResultDiagnostic[]
    result_preview: unknown
}

export interface SemanticEvalRun {
    id: number
    workspace_id: string
    user_id: string | null
    case_id: string
    question: string
    test_dimension: string
    expected_focus: string[]
    expected_limit: number | null
    semantic_result: SemanticEvalChainResult
    legacy_result: SemanticEvalChainResult
    verdict: string
    semantic_verdict?: string
    comparison_verdict?: string
    diagnostics: SemanticResultDiagnostic[]
    created_at: string
}

export interface SemanticAccessOption {
    id: string
    name: string
    description?: string | null
}

export interface SemanticAccessOptions {
    roles: SemanticAccessOption[]
    users: SemanticAccessOption[]
}

export interface SemanticPreviewResult {
    success: boolean
    sql: string
    intent: Record<string, unknown>
    plan: Record<string, unknown>
    data: Record<string, unknown>[]
    columns: string[]
    row_count: number
    result_text: string
    diagnostics?: SemanticResultDiagnostic[]
    referenced_tables: string[]
    execution_time_ms: number
    run_id: number | null
}

export interface SemanticPreviewErrorDetail {
    error_type?: string
    message?: string
    retryable?: boolean
    safe_to_fallback?: boolean
    details?: Record<string, unknown>
}

export interface SemanticBusinessSuggestion {
    id: number
    workspace_id: string
    datasource_id: number
    object_type: 'table' | 'column'
    object_id: number
    physical_name: string
    suggested_business_name: string
    suggested_description: string | null
    suggested_synonyms: string[]
    confidence: number
    source: string
    status: 'pending' | 'accepted' | 'rejected' | 'expired'
    evidence_json: Record<string, unknown>
    needs_manual_input: boolean
    reason: string | null
    profile_summary: Record<string, unknown>
    target_context: Record<string, unknown>
    created_at: string
    updated_at: string
    accepted_at: string | null
}

export interface SemanticMatchingObjectRef {
    object_type: 'table' | 'column' | 'metric'
    object_id: number
    table_id: number
    business_name: string
    source: 'business_name' | 'synonym'
}

export interface SemanticMatchingDiagnostic {
    type: 'generic_term' | 'duplicate_term'
    severity: 'info' | 'warning' | 'error'
    term: string
    objects: SemanticMatchingObjectRef[]
}

export interface SemanticModelsPayload {
    datasource: SemanticDatasource | null
    tables: SemanticTable[]
    columns: SemanticColumn[]
    metrics: SemanticMetric[]
    relationships: SemanticRelationship[]
    business_suggestions: SemanticBusinessSuggestion[]
    recent_runs: SemanticQueryRun[]
    matching_diagnostics: SemanticMatchingDiagnostic[]
}

export interface SemanticModelCounts {
    tables: number
    queryable_tables: number
    columns: number
    queryable_columns: number
    metrics: number
    queryable_metrics: number
    relationships: number
    queryable_relationships: number
    stale_assets: number
    orphaned_assets: number
    recent_runs: number
    recent_success_runs: number
}

export interface SemanticModelOverview {
    datasource: SemanticDatasource | null
    counts: SemanticModelCounts
}

export interface SemanticMetricForm {
    name: string
    business_name: string
    formula: string
    aggregation: string
    table_id: number | null
    column_id: number | null
    time_column_id: number | null
    default_grain: string
    description: string
    synonyms: string
    status: SemanticStatus
    is_queryable: boolean
    is_sensitive: boolean
}

export interface SemanticRelationshipForm {
    left_table_id: number | null
    right_table_id: number | null
    left_column_id: number | null
    right_column_id: number | null
    relationship_type: string
    confidence: number
    description: string
    status: SemanticStatus
}

/** 模板 */
export interface Template {
    id: number
    name: string
    description: string | null
    file_path: string
    file_type: string
    keywords: string | null
    variables_schema: Record<string, { type: string; desc?: string; location?: TemplateVariableLocation }> | null
    example_context: Record<string, unknown> | null
    is_active: boolean
    group_id: number | null
    created_at: string
    updated_at: string
}

/** 模板分组 */
export interface TemplateGroup {
    id: number
    name: string
    description: string | null
    color: string
    example_count: number
    created_at: string
    updated_at: string
}

/** 模板表单 */
export interface TemplateForm {
    name: string
    description: string
    keywords: string
    group_id: number | null
}

/** 模板分组表单 */
export interface TemplateGroupForm {
    name: string
    description: string
    color: string
}

/** 模板更新载荷 */
export interface TemplateUpdatePayload {
    name?: string
    description?: string
    keywords?: string
    variables_schema?: Record<string, { type: string; desc: string; location?: TemplateVariableLocation }>
    example_context?: Record<string, string>
    is_active?: boolean
}

/** 变量定义 */
export interface VariableItem {
    name: string
    type: string
    desc: string
    exampleValue: string
    location?: TemplateVariableLocation
}

export interface TemplateVariableLocation {
    type: 'paragraph' | 'table_cell'
    paragraph_index?: number
    table_index?: number
    row_index?: number
    cell_index?: number
    selected_text?: string
    /** 与 HTML data-id 一致的标识符，用于前端联动高亮 */
    mapping_id?: string
}

// =============================================================================
// 通用类型
// =============================================================================

/** 分组基础接口（用于 GroupFilter 复用） */
export interface GroupBase {
    id: number
    name: string
    color: string
    example_count: number
}

// =============================================================================
// 常量
// =============================================================================

/** 变量类型选项 */
export const VARIABLE_TYPES = [
    { value: 'text', label: '文本' },
    { value: 'number', label: '数字' },
    { value: 'date', label: '日期' },
    { value: 'image', label: '图片' },
    { value: 'table', label: '表格' },
] as const

/** 分组颜色选项 */
export const GROUP_COLORS = [
    '#3B82F6', '#10B981', '#F59E0B', '#EF4444',
    '#8B5CF6', '#EC4899', '#06B6D4', '#6366F1'
] as const

/** 空表单常量 */
export const EMPTY_SQL_EXAMPLE_FORM: SqlExampleForm = {
    question: '',
    sql: '',
    description: '',
    tables: '',
    group_id: null,
    parameters: [],
    is_active: false,
}

export const EMPTY_SQL_GROUP_FORM: SqlGroupForm = {
    name: '',
    description: '',
    color: '#3B82F6'
}

export const EMPTY_TEMPLATE_FORM: TemplateForm = {
    name: '',
    description: '',
    keywords: '',
    group_id: null
}

export const EMPTY_TEMPLATE_GROUP_FORM: TemplateGroupForm = {
    name: '',
    description: '',
    color: '#3B82F6'
}

// =============================================================================
// TemplateDialog 专用类型
// =============================================================================

/** 预览映射项 - 用于文档元素与变量的绑定 */
export interface PreviewMappingItem {
    id: string
    type: 'paragraph' | 'table_cell'
    text?: string
    paragraph_index?: number
    table_index?: number
    row_index?: number
    cell_index?: number
}

/** 工作流状态 - 控制 TemplateDialog 的 UI 状态 */
export type WorkflowStatus = 'idle' | 'processing' | 'previewing' | 'success' | 'error'

/** 处理步骤 - 进度动画的阶段 */
export type ProcessingStep = 'uploading' | 'analyzing' | 'generating'

/** 工作流状态对象 */
export interface WorkflowState {
    status: WorkflowStatus
    step: ProcessingStep
    progress: number
    errorMessage: string | null
}

/** TemplateDialog Props */
export interface TemplateDialogProps {
    open: boolean
    onClose: () => void
    form: TemplateForm
    onFormChange: (form: TemplateForm) => void
    variables: VariableItem[]
    onVariablesChange: (variables: VariableItem[]) => void
    file: File | null
    onFileChange: (file: File | null) => void
    onSave: () => void
    isSaving: boolean
    isEditing: boolean
    groups: TemplateGroup[]
    templateId?: number | null
    templateFileType?: string
}

// =============================================================================
// 空白检测相关
// =============================================================================

/** 候选字段（空白检测结果） */
export interface CandidateField {
    key: string
    label: string
    type: string
    location: TemplateVariableLocation
    confidence: number
    source: string
    context: string
}

/** 空白字段检测响应 */
export interface BlankFieldDetectResponse {
    candidates: CandidateField[]
    total: number
    high_confidence_count: number
}



