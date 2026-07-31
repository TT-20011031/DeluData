import { useMemo, useState } from 'react'
import {
    AlertTriangle,
    ChevronDown,
    CircleCheck,
    Database,
    GitBranch,
    Loader2,
    Lock,
    Network,
    Pencil,
    PlayCircle,
    RefreshCw,
    Save,
    Search,
    ShieldCheck,
    Sigma,
    type LucideIcon,
    Wand2,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { useSemanticModels } from '@/hooks/extendConfig'
import { readinessGuidance, readinessLabel } from './semanticTrustUtils'
import { SemanticAccessPolicyWorkspaceV2 } from './SemanticAccessPolicyWorkspaceV2'
import type {
    SemanticColumn,
    SemanticEvalChainResult,
    SemanticGovernanceEditableField,
    SemanticGovernanceCandidate,
    SemanticMetric,
    SemanticMetricForm,
    SemanticRelationshipForm,
    SemanticStatus,
    SemanticSyncState,
    SemanticTable,
} from '@/types/extendConfig'

const STATUS_LABEL: Record<SemanticStatus, string> = {
    suggested: '建议',
    confirmed: '启用',
    disabled: '停用',
}

const AGGREGATION_OPTIONS = [
    { value: 'sum', label: '求和' },
    { value: 'count', label: '计数' },
    { value: 'count_distinct', label: '去重计数' },
    { value: 'avg', label: '平均' },
    { value: 'min', label: '最小' },
    { value: 'max', label: '最大' },
    { value: 'custom', label: '自定义' },
]

const GRAIN_OPTIONS = [
    { value: 'day', label: '按日' },
    { value: 'month', label: '按月' },
    { value: 'year', label: '按年' },
]

const EMPTY_METRIC: SemanticMetricForm = {
    name: '',
    business_name: '',
    formula: '',
    aggregation: 'sum',
    table_id: null,
    column_id: null,
    time_column_id: null,
    default_grain: 'month',
    description: '',
    synonyms: '',
    status: 'confirmed',
    is_queryable: true,
    is_sensitive: false,
}

const EMPTY_RELATIONSHIP: SemanticRelationshipForm = {
    left_table_id: null,
    right_table_id: null,
    left_column_id: null,
    right_column_id: null,
    relationship_type: 'many_to_one',
    confidence: 0.8,
    description: '',
    status: 'confirmed',
}

type SuggestionDraft = {
    businessName: string
    description: string
    synonyms: string
}

export type SemanticWorkspace = 'governance' | 'review' | 'access-policies' | 'tables' | 'metrics' | 'relationships' | 'runs' | 'evaluation'

type PendingReviewAsset = {
    modelType: 'tables' | 'columns' | 'metrics' | 'relationships'
    id: number
    kind: '表' | '字段' | '指标' | '关系'
    name: string
    physicalReference: string
    syncState: 'stale' | 'orphaned'
    reason: string
    scanId: number | null
}

const WORKSPACE_META: Record<SemanticWorkspace, { title: string; description: string }> = {
    governance: {
        title: '候选治理中心',
        description: '查看每条建议的支持证据、冲突证据与评分，集中接受、修改或拒绝；人工决策会沉淀为长期证据。',
    },
    review: {
        title: '待复核资产',
        description: '集中检查因 Schema 变化而过期或失联的资产；确认安全后重新启用，失联对象需先完成改绑。',
    },
    'access-policies': {
        title: '问数数据权限',
        description: '按全体用户、角色或用户配置表、字段、指标和行数据范围；自然语言只负责填写结构化表单。',
    },
    tables: {
        title: '表字段',
        description: '维护语义表、字段业务名、同义词、敏感分类和启停状态；权限统一在问数数据权限中配置。',
    },
    metrics: {
        title: '指标',
        description: '配置指标公式、聚合方式、时间口径、权限范围和启停状态。',
    },
    relationships: {
        title: '关系',
        description: '维护语义表之间的关联字段，让跨表问数具备稳定连接路径。',
    },
    runs: {
        title: '运行记录',
        description: '查看语义预览与问数链路的执行状态、错误类型、行数和耗时。',
    },
    evaluation: {
        title: '评估回归',
        description: '固定运行 15 个问数评估用例，对比语义链路与旧 XiYan 链路的 SQL、状态和结果体检提示。',
    },
}

function reviewReason(reasonJson: Record<string, unknown>, syncState: 'stale' | 'orphaned') {
    const reason = String(reasonJson?.reason || '')
    return {
        column_type_changed: '字段类型发生变化，需要确认业务含义、公式和关联是否仍然成立。',
        physical_column_removed: '物理字段已删除，需要改绑到替代字段。',
        physical_table_removed: '物理表已删除，需要改绑到替代表。',
        schema_dependency_changed: '依赖的表或字段发生变化，需要检查公式或关联路径。',
        physical_object_restored: '物理对象已恢复，需要确认恢复后的结构仍符合原有语义。',
    }[reason] || (syncState === 'orphaned' ? '物理对象已不存在，需要改绑后再启用。' : 'Schema 依赖发生变化，需要人工确认。')
}

function statusBadge(status: SemanticStatus) {
    const styles = {
        suggested: 'border-amber-500/30 text-amber-500 bg-amber-500/10',
        confirmed: 'border-emerald-500/30 text-emerald-500 bg-emerald-500/10',
        disabled: 'border-manus-border text-manus-muted bg-manus-tertiary',
    }[status]
    return (
        <Badge
            variant="outline"
            className={cn('h-7 w-16 shrink-0 justify-center whitespace-nowrap px-0 text-xs leading-none', styles)}
        >
            {STATUS_LABEL[status]}
        </Badge>
    )
}

function syncBadge(state: SemanticSyncState) {
    const config = {
        current: ['同步', 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600'],
        stale: ['过期', 'border-amber-500/30 bg-amber-500/10 text-amber-600'],
        orphaned: ['失联', 'border-rose-500/30 bg-rose-500/10 text-rose-600'],
    }[state || 'current']
    return <Badge variant="outline" className={cn('h-6 rounded-full px-2 text-[11px]', config[1])}>{config[0]}</Badge>
}

function semanticObjectKey(objectType: 'table' | 'column', id: number) {
    return `${objectType}:${id}`
}

function synonymsText(values: string[]) {
    return values?.join(', ') || ''
}

function accessBadges(isSensitive: boolean, semanticReview?: string) {
    return (
        <div className="flex flex-wrap items-center gap-1.5">
            {isSensitive && (
                <Badge variant="outline" className="h-6 border-rose-500/30 bg-rose-500/10 text-xs text-rose-400">
                    敏感
                </Badge>
            )}
            {semanticReview && (
                <Badge
                    variant="outline"
                    className={cn(
                        'h-6 text-xs',
                        semanticReview === 'confirmed'
                            ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500'
                            : semanticReview === 'stale'
                                ? 'border-amber-500/30 bg-amber-500/10 text-amber-500'
                                : 'border-manus-border bg-manus-tertiary text-manus-muted',
                    )}
                >
                    {semanticReview === 'confirmed' ? '语义已审核' : semanticReview === 'stale' ? '语义已过期' : '语义待审核'}
                </Badge>
            )}
            <Badge variant="outline" className="h-6 border-manus-border bg-manus-tertiary text-xs text-manus-muted">
                策略中心
            </Badge>
        </div>
    )
}

function candidateEditableFields(candidate: SemanticGovernanceCandidate): SemanticGovernanceEditableField[] {
    if (candidate.editable_fields?.length) return candidate.editable_fields
    const patch = candidate.proposed_patch || {}
    if (candidate.candidate_type === 'business_semantics') {
        return [
            { key: 'business_name', label: '业务名', type: 'text', value: patch.business_name || '' },
            { key: 'description', label: '业务说明', type: 'textarea', value: patch.description || '' },
            { key: 'synonyms', label: '同义词', type: 'tags', value: Array.isArray(patch.synonyms) ? patch.synonyms : [] },
        ]
    }
    if (candidate.candidate_type === 'mark_sensitive') {
        return [{ key: 'is_sensitive', label: '标记为敏感', type: 'boolean', value: Boolean(patch.is_sensitive) }]
    }
    return Object.entries(patch).map(([key, value]) => ({
        key,
        label: {
            status: '状态',
            is_queryable: '允许问数',
            sync_state: '同步状态',
        }[key] || key,
        type: typeof value === 'boolean' ? 'boolean' : 'text',
        value,
    }))
}

function candidateDraft(candidate: SemanticGovernanceCandidate): Record<string, unknown> {
    return candidateEditableFields(candidate).reduce<Record<string, unknown>>((draft, field) => {
        draft[field.key] = field.type === 'tags' && Array.isArray(field.value) ? field.value.join(', ') : field.value
        return draft
    }, {})
}

function candidatePatchFromDraft(candidate: SemanticGovernanceCandidate, draft: Record<string, unknown>) {
    const patch: Record<string, unknown> = {}
    candidateEditableFields(candidate).forEach(field => {
        const value = draft[field.key]
        patch[field.key] = field.type === 'tags'
            ? String(value || '').split(',').map(item => item.trim()).filter(Boolean)
            : value
    })
    return patch
}

function localEvidenceSummary(item: Record<string, unknown>) {
    const source = String(item.source_type || 'evidence')
    const claim = String(item.claim_type || '')
    const direction = item.direction === 'conflict' ? '冲突证据' : '支持证据'
    if (source === 'golden_sql') return `${direction}: 黄金 SQL 曾引用该对象。`
    if (source === 'shadow_equivalent') return `${direction}: Shadow 对照结果一致。`
    if (source === 'evaluation_equivalent') return `${direction}: 回归评估结果等价。`
    if (source === 'runtime_pattern') return `${direction}: 最近问数运行中稳定命中。`
    if (source === 'aggregate_profile') return `${direction}: 字段画像提供聚合证据。`
    if (source === 'deterministic_validation') return `${direction}: 通过确定性结构或格式校验。`
    if (source === 'human_confirmation') return `${direction}: 管理员历史决策已沉淀为反馈。`
    if (source === 'llm_proposal') return `${direction}: LLM 根据结构与注释提出建议，需人工确认。`
    if (source === 'naming_rule') return `${direction}: 命名规则提出建议。`
    return `${direction}: ${source}${claim ? ` · ${claim}` : ''}。`
}

function candidateEvidenceSummaries(candidate: SemanticGovernanceCandidate) {
    if (candidate.evidence_summaries?.length) return candidate.evidence_summaries
    return [...candidate.supporting_evidence, ...candidate.conflicting_evidence].slice(0, 8).map(localEvidenceSummary)
}

function contextValue(context: Record<string, unknown> | undefined, key: string) {
    const value = context?.[key]
    return value == null || value === '' ? '' : String(value)
}

function candidateTargetText(candidate: SemanticGovernanceCandidate) {
    const context = candidate.target_context || {}
    if (candidate.target_type === 'tables') {
        return [contextValue(context, 'physical_name'), contextValue(context, 'business_name')].filter(Boolean).join(' · ')
    }
    if (candidate.target_type === 'columns') {
        const table = contextValue(context, 'table_physical_name') || contextValue(context, 'table_business_name')
        const column = contextValue(context, 'column_physical_name')
        const dataType = contextValue(context, 'data_type')
        return [table && column ? `${table}.${column}` : column, dataType].filter(Boolean).join(' · ')
    }
    if (candidate.target_type === 'metrics') {
        return [
            contextValue(context, 'main_table') || contextValue(context, 'table_business_name'),
            contextValue(context, 'metric_column') || contextValue(context, 'column_business_name'),
            contextValue(context, 'time_column'),
            contextValue(context, 'formula'),
        ].filter(Boolean).join(' · ')
    }
    if (candidate.target_type === 'relationships') {
        const left = [contextValue(context, 'left_table'), contextValue(context, 'left_column')].filter(Boolean).join('.')
        const right = [contextValue(context, 'right_table'), contextValue(context, 'right_column')].filter(Boolean).join('.')
        return [left && right ? `${left} -> ${right}` : '', contextValue(context, 'relationship_type')].filter(Boolean).join(' · ')
    }
    return `${candidate.target_type} #${candidate.target_id}`
}

function metricToForm(metric: SemanticMetric): SemanticMetricForm {
    return {
        name: metric.name,
        business_name: metric.business_name,
        formula: metric.formula,
        aggregation: metric.aggregation || 'custom',
        table_id: metric.table_id,
        column_id: metric.column_id,
        time_column_id: metric.time_column_id,
        default_grain: metric.default_grain || 'month',
        description: metric.description || '',
        synonyms: synonymsText(metric.synonyms),
        status: metric.status,
        is_queryable: metric.is_queryable,
        is_sensitive: metric.is_sensitive,
    }
}

function metricPatch(form: SemanticMetricForm) {
    return {
        name: form.name,
        business_name: form.business_name || form.name,
        formula: form.formula,
        aggregation: form.aggregation || null,
        table_id: form.table_id,
        column_id: form.column_id,
        time_column_id: form.time_column_id,
        default_grain: form.default_grain || null,
        description: form.description,
        synonyms: form.synonyms.split(',').map(item => item.trim()).filter(Boolean),
        status: form.status,
        is_queryable: form.is_queryable,
        is_sensitive: form.is_sensitive,
    }
}

function JsonBlock({ value }: { value: unknown }) {
    return (
        <pre className="max-h-72 overflow-auto rounded-md bg-manus p-3 text-xs leading-5 text-manus-text">
            {JSON.stringify(value, null, 2)}
        </pre>
    )
}

function latestRunByCase<T extends { case_id: string; created_at: string }>(runs: T[]) {
    const map = new Map<string, T>()
    runs.forEach(run => {
        const current = map.get(run.case_id)
        if (!current || new Date(run.created_at).getTime() > new Date(current.created_at).getTime()) {
            map.set(run.case_id, run)
        }
    })
    return map
}

function chainStatusClass(result?: SemanticEvalChainResult) {
    if (!result) return 'border-manus-border text-manus-muted'
    if (result.success) return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500'
    return 'border-rose-500/30 bg-rose-500/10 text-rose-400'
}

function verdictClass(verdict?: string) {
    if (verdict === 'pass') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500'
    if (verdict === 'pass_with_warnings' || verdict === 'partial') return 'border-amber-500/30 bg-amber-500/10 text-amber-400'
    if (verdict === 'fail') return 'border-rose-500/30 bg-rose-500/10 text-rose-400'
    return 'border-manus-border text-manus-muted'
}

export function SemanticModelManager({
    initialWorkspace = null,
}: {
    initialWorkspace?: SemanticWorkspace | null
} = {}) {
    const {
        data,
        isLoading,
        isSaving,
        isPreviewing,
        isRunningEvaluation,
        previewResult,
        previewError,
        evalCases,
        evalRuns,
        activeTable,
        activeTableId,
        setActiveTableId,
        columnsByTable,
        readiness,
        scanPreview,
        setScanPreview,
        questionReadiness,
        governanceCandidates,
        governancePolicy,
        governanceEvidence,
        load,
        loadTrustFoundation,
        loadEvaluation,
        scan,
        applyScan,
        setRuntimeMode,
        checkQuestionReadiness,
        loadGovernanceEvidence,
        acceptGovernanceCandidate,
        rejectGovernanceCandidate,
        rollbackGovernanceCandidate,
        batchGovernanceCandidates,
        setGovernanceObserveOnly,
        updateModel,
        previewSemanticQuery,
        clearPreview,
        runEvaluation,
        createMetric,
        createRelationship,
        generateBusinessSuggestions,
        acceptTableBusinessSuggestions,
        generateMetricSuggestions,
        generateRelationshipSuggestions,
    } = useSemanticModels({
        accessOnly: initialWorkspace === 'access-policies',
    })

    const [activeWorkspace, setActiveWorkspace] = useState<SemanticWorkspace | null>(initialWorkspace)
    const [metricForm, setMetricForm] = useState<SemanticMetricForm>(EMPTY_METRIC)
    const [editingMetricId, setEditingMetricId] = useState<number | null>(null)
    const [editingMetricForm, setEditingMetricForm] = useState<SemanticMetricForm>(EMPTY_METRIC)
    const [relationshipForm, setRelationshipForm] = useState<SemanticRelationshipForm>(EMPTY_RELATIONSHIP)
    const [semanticDrafts, setSemanticDrafts] = useState<Record<string, SuggestionDraft>>({})
    const [editingObjects, setEditingObjects] = useState<Record<string, boolean>>({})
    const [previewQuestion, setPreviewQuestion] = useState('')
    const [isPreviewDialogOpen, setIsPreviewDialogOpen] = useState(false)
    const [isScanConfirmOpen, setIsScanConfirmOpen] = useState(false)
    const [selectedGovernanceCandidate, setSelectedGovernanceCandidate] = useState<SemanticGovernanceCandidate | null>(null)
    const [candidateStatusFilter, setCandidateStatusFilter] = useState('open')
    const [candidateRiskFilter, setCandidateRiskFilter] = useState('all')
    const [candidateFormDraft, setCandidateFormDraft] = useState<Record<string, unknown>>({})
    const [candidatePatchDraft, setCandidatePatchDraft] = useState('')
    const [isCandidateJsonOpen, setIsCandidateJsonOpen] = useState(false)
    const [rejectCategory, setRejectCategory] = useState('incorrect_mapping')
    const [rejectReason, setRejectReason] = useState('')
    const [selectedCandidateIds, setSelectedCandidateIds] = useState<number[]>([])

    const activeColumns = useMemo(
        () => (activeTable ? columnsByTable[activeTable.id] || [] : []),
        [activeTable, columnsByTable]
    )
    const activeTableSuggestions = useMemo(() => {
        if (!activeTable) return []
        return data.business_suggestions.filter(suggestion => (
            suggestion.status === 'pending' &&
            (
                (suggestion.object_type === 'table' && suggestion.object_id === activeTable.id) ||
                (suggestion.object_type === 'column' && Number(suggestion.target_context?.table_id) === activeTable.id)
            )
        ))
    }, [activeTable, data.business_suggestions])
    const businessSuggestionForObject = (objectType: 'table' | 'column', objectId: number) =>
        activeTableSuggestions.find(suggestion => suggestion.object_type === objectType && suggestion.object_id === objectId)
    const confirmedTables = data.tables.filter(table => table.status === 'confirmed' && table.is_queryable && (table.sync_state || 'current') === 'current')
    const metricColumns = metricForm.table_id ? columnsByTable[metricForm.table_id] || [] : []
    const editMetricColumns = editingMetricForm.table_id ? columnsByTable[editingMetricForm.table_id] || [] : []
    const confirmedColumns = data.columns.filter(column => column.status === 'confirmed' && column.is_queryable && (column.sync_state || 'current') === 'current')
    const confirmedMetrics = data.metrics.filter(metric => metric.status === 'confirmed' && metric.is_queryable && (metric.sync_state || 'current') === 'current')
    const confirmedRelationships = data.relationships.filter(relationship => relationship.status === 'confirmed' && relationship.is_queryable && (relationship.sync_state || 'current') === 'current')
    const pendingReviewAssets = useMemo<PendingReviewAsset[]>(() => {
        const tableNames = new Map(data.tables.map(table => [table.id, table.business_name]))
        const columnNames = new Map(data.columns.map(column => [column.id, column.business_name]))
        const scanId = (reason: Record<string, unknown>, fallback: number | null) => typeof reason?.scan_id === 'number' ? reason.scan_id : fallback
        const isPending = (state: SemanticSyncState) => state === 'stale' || state === 'orphaned'
        const assets: PendingReviewAsset[] = []

        data.tables.filter(table => isPending(table.sync_state)).forEach(table => assets.push({
            modelType: 'tables',
            id: table.id,
            kind: '表',
            name: table.business_name,
            physicalReference: table.physical_name,
            syncState: table.sync_state as 'stale' | 'orphaned',
            reason: reviewReason(table.stale_reason_json, table.sync_state as 'stale' | 'orphaned'),
            scanId: scanId(table.stale_reason_json, table.last_seen_scan_id),
        }))
        data.columns.filter(column => isPending(column.sync_state)).forEach(column => assets.push({
            modelType: 'columns',
            id: column.id,
            kind: '字段',
            name: column.business_name,
            physicalReference: `${column.physical_table}.${column.physical_name}`,
            syncState: column.sync_state as 'stale' | 'orphaned',
            reason: reviewReason(column.stale_reason_json, column.sync_state as 'stale' | 'orphaned'),
            scanId: scanId(column.stale_reason_json, column.last_seen_scan_id),
        }))
        data.metrics.filter(metric => isPending(metric.sync_state)).forEach(metric => assets.push({
            modelType: 'metrics',
            id: metric.id,
            kind: '指标',
            name: metric.business_name,
            physicalReference: metric.formula,
            syncState: metric.sync_state as 'stale' | 'orphaned',
            reason: reviewReason(metric.stale_reason_json, metric.sync_state as 'stale' | 'orphaned'),
            scanId: scanId(metric.stale_reason_json, metric.last_seen_scan_id),
        }))
        data.relationships.filter(relationship => isPending(relationship.sync_state)).forEach(relationship => assets.push({
            modelType: 'relationships',
            id: relationship.id,
            kind: '关系',
            name: `${tableNames.get(relationship.left_table_id) || '-'} → ${tableNames.get(relationship.right_table_id) || '-'}`,
            physicalReference: `${columnNames.get(relationship.left_column_id) || '-'} → ${columnNames.get(relationship.right_column_id) || '-'}`,
            syncState: relationship.sync_state as 'stale' | 'orphaned',
            reason: reviewReason(relationship.stale_reason_json, relationship.sync_state as 'stale' | 'orphaned'),
            scanId: scanId(relationship.stale_reason_json, relationship.last_seen_scan_id),
        }))
        return assets.sort((left, right) => Number(right.syncState === 'orphaned') - Number(left.syncState === 'orphaned') || left.kind.localeCompare(right.kind, 'zh-CN'))
    }, [data.columns, data.metrics, data.relationships, data.tables])
    const staleReviewCount = pendingReviewAssets.filter(asset => asset.syncState === 'stale').length
    const orphanedReviewCount = pendingReviewAssets.filter(asset => asset.syncState === 'orphaned').length
    const latestEvalRuns = useMemo(() => latestRunByCase(evalRuns), [evalRuns])
    const activeWorkspaceMeta = activeWorkspace ? WORKSPACE_META[activeWorkspace] : null
    const openGovernanceCandidates = governanceCandidates.filter(candidate => ['proposed', 'needs_review', 'blocked'].includes(candidate.status))
    const activeWorkspaceDescription = activeWorkspace === 'governance'
        ? `${openGovernanceCandidates.length} 条待治理候选`
        : activeWorkspaceMeta?.description
    const visibleGovernanceCandidates = governanceCandidates.filter(candidate => {
        const statusMatches = candidateStatusFilter === 'all'
            || (candidateStatusFilter === 'open' && ['proposed', 'needs_review', 'blocked'].includes(candidate.status))
            || candidate.status === candidateStatusFilter
        return statusMatches && (candidateRiskFilter === 'all' || candidate.risk_level === candidateRiskFilter)
    })
    const inspectGovernanceCandidate = async (candidate: SemanticGovernanceCandidate) => {
        setSelectedGovernanceCandidate(candidate)
        setCandidateFormDraft(candidateDraft(candidate))
        setCandidatePatchDraft(JSON.stringify(candidate.proposed_patch, null, 2))
        setIsCandidateJsonOpen(false)
        setRejectReason('')
        await loadGovernanceEvidence(candidate.target_type, candidate.target_id)
    }
    const acceptSelectedGovernanceCandidate = async () => {
        if (!selectedGovernanceCandidate) return
        try {
            const editedPatch = isCandidateJsonOpen
                ? JSON.parse(candidatePatchDraft) as Record<string, unknown>
                : candidatePatchFromDraft(selectedGovernanceCandidate, candidateFormDraft)
            const success = await acceptGovernanceCandidate(selectedGovernanceCandidate.candidate_id, editedPatch, '管理员在证据中心复核通过')
            if (success) setSelectedGovernanceCandidate(null)
        } catch {
            return
        }
    }
    const rejectSelectedGovernanceCandidate = async () => {
        if (!selectedGovernanceCandidate || !rejectReason.trim()) return
        const success = await rejectGovernanceCandidate(selectedGovernanceCandidate.candidate_id, rejectCategory, rejectReason.trim())
        if (success) setSelectedGovernanceCandidate(null)
    }
    const batchDecideGovernanceCandidates = async (action: 'accept' | 'reject') => {
        const result = await batchGovernanceCandidates(
            selectedCandidateIds,
            action,
            action === 'reject' ? (rejectReason.trim() || '批量复核未通过') : '管理员批量复核通过',
            rejectCategory,
        )
        if (result) setSelectedCandidateIds(result.failed.map(item => item.candidate_id))
    }
    const renderCandidateField = (field: SemanticGovernanceEditableField, disabled: boolean) => {
        const value = candidateFormDraft[field.key]
        const setValue = (next: unknown) => setCandidateFormDraft(current => ({ ...current, [field.key]: next }))
        if (field.type === 'boolean') {
            return (
                <div key={field.key} className="flex items-center justify-between gap-3 rounded-md border border-manus-border bg-manus px-3 py-2">
                    <Label className="text-sm text-manus-text">{field.label}</Label>
                    <Switch checked={Boolean(value)} disabled={disabled} onCheckedChange={setValue} />
                </div>
            )
        }
        if (field.type === 'textarea') {
            return (
                <div key={field.key} className="space-y-1.5">
                    <Label className="text-xs text-manus-muted">{field.label}</Label>
                    <Textarea value={String(value || '')} onChange={event => setValue(event.target.value)} disabled={disabled} className="min-h-24 border-manus-border bg-manus text-sm text-manus-text" />
                </div>
            )
        }
        if (field.type === 'select' || ['status', 'sync_state'].includes(field.key)) {
            const options = field.key === 'sync_state'
                ? [['current', '同步'], ['stale', '过期'], ['orphaned', '失联']]
                : [['confirmed', '启用'], ['suggested', '建议'], ['disabled', '停用']]
            return (
                <div key={field.key} className="space-y-1.5">
                    <Label className="text-xs text-manus-muted">{field.label}</Label>
                    <Select value={String(value || '')} onValueChange={setValue} disabled={disabled}>
                        <SelectTrigger className="h-9 border-manus-border bg-manus text-sm"><SelectValue /></SelectTrigger>
                        <SelectContent>{options.map(([optionValue, label]) => <SelectItem key={optionValue} value={optionValue}>{label}</SelectItem>)}</SelectContent>
                    </Select>
                </div>
            )
        }
        return (
            <div key={field.key} className="space-y-1.5">
                <Label className="text-xs text-manus-muted">{field.label}</Label>
                <Input
                    value={String(value || '')}
                    onChange={event => setValue(event.target.value)}
                    disabled={disabled}
                    placeholder={field.type === 'tags' ? '用逗号分隔多个同义词' : undefined}
                    className="h-9 border-manus-border bg-manus text-sm text-manus-text"
                />
            </div>
        )
    }
    const workspaceEntries: Array<{
        id: SemanticWorkspace
        label: string
        count: number
        detail: string
        icon: LucideIcon
    }> = [
        {
            id: 'access-policies',
            label: '问数权限',
            count: data.tables.length,
            detail: '按用户与角色配置可见资产和行范围',
            icon: Lock,
        },
        {
            id: 'tables',
            label: '表字段',
            count: data.tables.length,
            detail: `${confirmedTables.length} 张表、${confirmedColumns.length} 个字段已启用`,
            icon: GitBranch,
        },
        {
            id: 'metrics',
            label: '指标',
            count: data.metrics.length,
            detail: `${confirmedMetrics.length} 个指标可查询`,
            icon: Sigma,
        },
        {
            id: 'relationships',
            label: '关系',
            count: data.relationships.length,
            detail: `${confirmedRelationships.length} 条关系已确认`,
            icon: ShieldCheck,
        },
        {
            id: 'runs',
            label: '运行记录',
            count: data.recent_runs.length,
            detail: `${data.recent_runs.filter(run => run.status === 'success').length} 次最近成功`,
            icon: PlayCircle,
        },
        {
            id: 'evaluation',
            label: '评估回归',
            count: evalCases.length,
            detail: `${evalRuns.length} 条运行记录 · ${latestEvalRuns.size} 个最近用例`,
            icon: PlayCircle,
        },
    ]
    const columnsForRelationship = useMemo(() => ({
        left: relationshipForm.left_table_id ? columnsByTable[relationshipForm.left_table_id] || [] : [],
        right: relationshipForm.right_table_id ? columnsByTable[relationshipForm.right_table_id] || [] : [],
    }), [columnsByTable, relationshipForm.left_table_id, relationshipForm.right_table_id])

    const tableName = (id: number | null) => data.tables.find(table => table.id === id)?.business_name || '-'
    const columnName = (id: number | null) => data.columns.find(column => column.id === id)?.business_name || '-'
    const isEditingObject = (objectType: 'table' | 'column', id: number) => Boolean(editingObjects[semanticObjectKey(objectType, id)])

    const inspectReviewAsset = (asset: PendingReviewAsset) => {
        if (asset.modelType === 'tables') {
            setActiveTableId(asset.id)
            setActiveWorkspace('tables')
            return
        }
        if (asset.modelType === 'columns') {
            const column = data.columns.find(item => item.id === asset.id)
            if (column) setActiveTableId(column.table_id)
            setActiveWorkspace('tables')
            return
        }
        if (asset.modelType === 'metrics') {
            const metric = data.metrics.find(item => item.id === asset.id)
            if (metric) {
                setEditingMetricId(metric.id)
                setEditingMetricForm(metricToForm(metric))
            }
            setActiveWorkspace('metrics')
            return
        }
        setActiveWorkspace('relationships')
    }

    const draftForObject = (
        objectType: 'table' | 'column',
        object: SemanticTable | SemanticColumn
    ): SuggestionDraft => {
        const objectKey = semanticObjectKey(objectType, object.id)
        if (semanticDrafts[objectKey]) return semanticDrafts[objectKey]
        const suggestion = businessSuggestionForObject(objectType, object.id)
        if (suggestion?.suggested_business_name?.trim()) {
            return {
                businessName: suggestion.suggested_business_name,
                description: suggestion.suggested_description || '',
                synonyms: synonymsText(suggestion.suggested_synonyms),
            }
        }
        return {
            businessName: object.business_name,
            description: object.description || '',
            synonyms: synonymsText(object.synonyms),
        }
    }

    const setObjectDraft = (objectType: 'table' | 'column', id: number, patch: Partial<SuggestionDraft>) => {
        const objectKey = semanticObjectKey(objectType, id)
        setSemanticDrafts(current => ({
            ...current,
            [objectKey]: {
                ...(current[objectKey] || { businessName: '', description: '', synonyms: '' }),
                ...patch,
            },
        }))
    }

    const startObjectEdit = (
        objectType: 'table' | 'column',
        object: SemanticTable | SemanticColumn
    ) => {
        const objectKey = semanticObjectKey(objectType, object.id)
        setSemanticDrafts(current => ({
            ...current,
            [objectKey]: current[objectKey] || draftForObject(objectType, object),
        }))
        setEditingObjects(current => ({ ...current, [objectKey]: true }))
    }

    const stopObjectEdit = (objectType: 'table' | 'column', id: number) => {
        const objectKey = semanticObjectKey(objectType, id)
        setEditingObjects(current => {
            const next = { ...current }
            delete next[objectKey]
            return next
        })
        setSemanticDrafts(current => {
            const next = { ...current }
            delete next[objectKey]
            return next
        })
    }

    const draftPayload = (draft: SuggestionDraft) => ({
        business_name: draft.businessName,
        description: draft.description,
        synonyms: draft.synonyms.split(',').map(item => item.trim()).filter(Boolean),
    })

    const saveObjectEdit = async (
        objectType: 'table' | 'column',
        object: SemanticTable | SemanticColumn,
        draft: SuggestionDraft
    ) => {
        const ok = await updateModel(objectType === 'table' ? 'tables' : 'columns', object.id, draftPayload(draft))
        if (ok) stopObjectEdit(objectType, object.id)
    }

    const submitMetric = async () => {
        if (!metricForm.name.trim() || !metricForm.formula.trim() || !metricForm.table_id) return
        const ok = await createMetric(metricForm)
        if (ok) setMetricForm(EMPTY_METRIC)
    }

    const submitMetricEdit = async () => {
        if (!editingMetricId || !editingMetricForm.name.trim() || !editingMetricForm.formula.trim() || !editingMetricForm.table_id) return
        const ok = await updateModel('metrics', editingMetricId, metricPatch(editingMetricForm))
        if (ok) setEditingMetricId(null)
    }

    const submitRelationship = async () => {
        if (!relationshipForm.left_table_id || !relationshipForm.right_table_id || !relationshipForm.left_column_id || !relationshipForm.right_column_id) return
        const ok = await createRelationship(relationshipForm)
        if (ok) setRelationshipForm(EMPTY_RELATIONSHIP)
    }

    const runPreview = async () => {
        if (!previewQuestion.trim()) return
        setIsPreviewDialogOpen(true)
        await previewSemanticQuery(previewQuestion)
    }

    const confirmScan = async () => {
        if (!scanPreview) return
        const ok = await applyScan(scanPreview.scan_id)
        if (ok) setIsScanConfirmOpen(false)
    }

    const openScanPreview = async () => {
        setScanPreview(null)
        setIsScanConfirmOpen(true)
        const result = await scan()
        if (!result) setIsScanConfirmOpen(false)
    }

    const runReadinessCheck = async () => {
        if (!previewQuestion.trim()) return
        await checkQuestionReadiness(previewQuestion)
    }

    const clearPreviewResult = () => {
        clearPreview()
        setIsPreviewDialogOpen(false)
    }

    const renderMetricForm = (
        form: SemanticMetricForm,
        setForm: (next: SemanticMetricForm) => void,
        columns: SemanticColumn[],
        submitLabel: string,
        onSubmit: () => void
    ) => (
        <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
                <Input value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} placeholder="metric_name" />
                <Input value={form.business_name} onChange={event => setForm({ ...form, business_name: event.target.value })} placeholder="业务名称" />
            </div>
            <Textarea
                value={form.formula}
                onChange={event => setForm({ ...form, formula: event.target.value })}
                placeholder="SUM({amount})"
                className="min-h-[88px] border-manus-border bg-manus-secondary text-manus-text"
            />
            <div className="grid grid-cols-2 gap-2">
                <Select
                    value={form.table_id?.toString() || ''}
                    onValueChange={value => setForm({ ...form, table_id: Number(value), column_id: null, time_column_id: null })}
                >
                    <SelectTrigger className="border-manus-border bg-manus-secondary"><SelectValue placeholder="选择主表" /></SelectTrigger>
                    <SelectContent>{confirmedTables.map(table => <SelectItem key={table.id} value={table.id.toString()}>{table.business_name}</SelectItem>)}</SelectContent>
                </Select>
                <Select value={form.column_id?.toString() || 'none'} onValueChange={value => setForm({ ...form, column_id: value === 'none' ? null : Number(value) })}>
                    <SelectTrigger className="border-manus-border bg-manus-secondary"><SelectValue placeholder="指标字段" /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="none">不绑定字段</SelectItem>
                        {columns.map(column => <SelectItem key={column.id} value={column.id.toString()}>{column.business_name}</SelectItem>)}
                    </SelectContent>
                </Select>
                <Select value={form.aggregation || 'custom'} onValueChange={value => setForm({ ...form, aggregation: value })}>
                    <SelectTrigger className="border-manus-border bg-manus-secondary"><SelectValue placeholder="聚合方式" /></SelectTrigger>
                    <SelectContent>{AGGREGATION_OPTIONS.map(option => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent>
                </Select>
                <Select value={form.status} onValueChange={value => setForm({ ...form, status: value as SemanticStatus, is_queryable: value === 'confirmed' })}>
                    <SelectTrigger className="border-manus-border bg-manus-secondary"><SelectValue placeholder="状态" /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="suggested">建议</SelectItem>
                        <SelectItem value="confirmed">启用</SelectItem>
                        <SelectItem value="disabled">停用</SelectItem>
                    </SelectContent>
                </Select>
                <Select value={form.time_column_id?.toString() || 'none'} onValueChange={value => setForm({ ...form, time_column_id: value === 'none' ? null : Number(value) })}>
                    <SelectTrigger className="border-manus-border bg-manus-secondary"><SelectValue placeholder="时间字段" /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="none">不绑定时间字段</SelectItem>
                        {columns.map(column => <SelectItem key={column.id} value={column.id.toString()}>{column.business_name}</SelectItem>)}
                    </SelectContent>
                </Select>
                <Select value={form.default_grain || 'month'} onValueChange={value => setForm({ ...form, default_grain: value })}>
                    <SelectTrigger className="border-manus-border bg-manus-secondary"><SelectValue placeholder="默认粒度" /></SelectTrigger>
                    <SelectContent>{GRAIN_OPTIONS.map(option => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent>
                </Select>
            </div>
            <Input value={form.synonyms} onChange={event => setForm({ ...form, synonyms: event.target.value })} placeholder="同义词，逗号分隔" />
            <Textarea
                value={form.description}
                onChange={event => setForm({ ...form, description: event.target.value })}
                placeholder="指标说明"
                className="min-h-[64px] border-manus-border bg-manus-secondary text-manus-text"
            />
            <div className="rounded-md border border-manus-border bg-manus-secondary p-3">
                <div className="mb-3 flex items-center justify-between gap-3">
                    <Label className="text-manus-text">敏感指标</Label>
                    <Switch checked={form.is_sensitive} onCheckedChange={checked => setForm({ ...form, is_sensitive: checked })} />
                </div>
                <div className="border-l-2 border-manus-border pl-3 text-xs leading-5 text-manus-muted">
                    敏感指标默认拒绝普通成员访问，授权范围在语义访问策略中心集中维护。
                </div>
            </div>
            <Button onClick={onSubmit} disabled={isSaving || !form.name.trim() || !form.formula.trim() || !form.table_id} className="w-full bg-accent text-white hover:bg-accent/90">
                {isSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sigma className="mr-2 h-4 w-4" />}
                {submitLabel}
            </Button>
        </div>
    )

    if (isLoading) {
        return (
            <div className="flex items-center justify-center py-12">
                <Loader2 className="h-8 w-8 animate-spin text-accent" />
            </div>
        )
    }

    return (
        <>
            <section className="mx-auto flex h-full w-full max-w-7xl flex-col overflow-hidden bg-transparent text-[#1f1f1d]">
                <div className="relative min-h-0 flex-1 overflow-hidden px-5 py-4 sm:py-5">
                    <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgba(31,31,29,0.026)_1px,transparent_1px),linear-gradient(90deg,rgba(31,31,29,0.026)_1px,transparent_1px)] bg-[size:48px_48px]" />
                    <div className="pointer-events-none absolute inset-x-0 bottom-0 h-32 bg-[linear-gradient(180deg,rgba(247,247,245,0)_0%,rgba(239,236,229,0.42)_100%)]" />
                    <div className="relative z-10 flex h-full min-h-0 flex-col gap-3">
                        <h2
                            className="text-center text-[30px] font-semibold leading-tight tracking-normal text-[#151514] sm:text-[36px]"
                            style={{ fontFamily: '"Noto Serif SC", "Source Han Serif SC", "Songti SC", SimSun, serif' }}
                        >
                            语义治理控制台
                        </h2>

                        <div className="mx-auto flex w-fit max-w-full flex-wrap items-center justify-center gap-2 rounded-lg border border-[#e6e3dc] bg-[#fbfbfa]/90 px-3 py-2 shadow-[0_14px_36px_rgba(31,31,29,0.07)] backdrop-blur-sm">
                            {data.datasource && (
                                <div className="flex h-10 items-center gap-2 rounded-md border border-[#e6e3dc] bg-white px-2 shadow-sm">
                                    <span className="pl-1 text-xs font-semibold uppercase tracking-[0.14em] text-[#7d786f]">Runtime</span>
                                    <Select value={readiness?.runtime_mode || data.datasource.runtime_mode || 'disabled'} onValueChange={value => setRuntimeMode(value as 'disabled' | 'shadow' | 'trusted')} disabled={isSaving}>
                                        <SelectTrigger className="h-8 w-28 border-0 bg-[#f3f1ed] text-xs font-semibold shadow-none"><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="disabled">Disabled</SelectItem>
                                            <SelectItem value="shadow">Shadow</SelectItem>
                                            <SelectItem value="trusted">Trusted</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            )}

                            <div className="flex flex-wrap items-center justify-center gap-2">
                                <Button size="sm" variant="outline" onClick={() => Promise.all([load(), loadTrustFoundation()])} disabled={isSaving} className="h-10 border-[#dedad2] bg-white px-3 text-[#2c2a27] shadow-sm hover:bg-[#f2f0ec]">
                                    <RefreshCw className="mr-2 h-4 w-4" />
                                    刷新
                                </Button>
                                <Button size="sm" onClick={openScanPreview} disabled={isSaving} className="h-10 bg-[#111111] px-3 text-white shadow-sm hover:bg-[#2a2a2a]">
                                    {isSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Database className="mr-2 h-4 w-4" />}
                                    扫描
                                </Button>
                            </div>
                        </div>

                        <div className="grid gap-3 md:grid-cols-3">
                            <div className="rounded-lg border border-[#e6e3dc] bg-[#171716] p-4 text-white shadow-[0_18px_44px_rgba(20,20,18,0.16)]">
                                <div className="flex items-center justify-between">
                                    <span className="text-xs font-semibold uppercase tracking-[0.16em] text-white/55">整体就绪度</span>
                                    {readiness?.status === 'ready' ? <CircleCheck className="h-5 w-5 text-emerald-400" /> : <AlertTriangle className="h-5 w-5 text-amber-400" />}
                                </div>
                                <div className="mt-4 text-2xl font-semibold">{readiness ? readinessLabel(readiness.status) : '加载中'}</div>
                                <div className="mt-2 line-clamp-2 text-xs leading-5 text-white/60">{readiness ? readinessGuidance(readiness) : '正在检查结构、语义与安全条件。'}</div>
                            </div>
                            <button
                                type="button"
                                onClick={() => setActiveWorkspace('review')}
                                className="group rounded-lg border border-[#e6e3dc] bg-[#fbfbfa]/95 p-4 text-left shadow-[0_14px_36px_rgba(31,31,29,0.07)] transition-all hover:-translate-y-0.5 hover:border-amber-500/40 hover:shadow-[0_18px_42px_rgba(31,31,29,0.11)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60"
                            >
                                <div className="flex items-center justify-between">
                                    <span className="text-xs font-semibold uppercase tracking-[0.16em] text-[#8a857d]">待复核资产</span>
                                    <AlertTriangle className={cn('h-5 w-5', pendingReviewAssets.length ? 'text-amber-500' : 'text-emerald-500')} />
                                </div>
                                <div className="mt-3 flex items-end justify-between gap-4">
                                    <div>
                                        <div className="text-3xl font-semibold text-[#252421]">{pendingReviewAssets.length}</div>
                                        <div className="mt-1 text-xs text-[#777268]">{staleReviewCount} 个过期 · {orphanedReviewCount} 个失联</div>
                                    </div>
                                    <span className="pb-1 text-xs font-medium text-amber-700 transition-transform group-hover:translate-x-0.5">进入复核 →</span>
                                </div>
                            </button>
                            <button type="button" onClick={() => setActiveWorkspace('governance')} className="rounded-lg border border-[#e6e3dc] bg-[#fbfbfa]/95 p-4 text-left shadow-[0_14px_36px_rgba(31,31,29,0.07)] transition-all hover:-translate-y-0.5 hover:bg-white">
                                <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-[0.16em] text-[#8a857d]"><span>候选治理中心</span><span>{governancePolicy?.observe_only ? '观察模式' : '自动模式'}</span></div>
                                <div className="mt-3 flex items-end justify-between gap-4">
                                    <div>
                                        <div className="text-3xl font-semibold leading-none text-[#252421]">{openGovernanceCandidates.length}</div>
                                        <div className="mt-2 text-xs leading-5 text-[#777268]">待治理候选</div>
                                    </div>
                                    <span className="pb-1 text-xs font-medium text-[#252421]">进入治理 →</span>
                                </div>
                            </button>
                        </div>

                        <div className="grid min-h-0 flex-1 content-start gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
                            {workspaceEntries.map(entry => (
                                <button
                                    key={entry.id}
                                    type="button"
                                    onClick={() => setActiveWorkspace(entry.id)}
                                    className="group flex min-h-[132px] min-w-0 flex-col justify-between rounded-lg border border-[#e6e3dc] bg-[#fbfbfa]/95 p-4 text-left shadow-[0_14px_36px_rgba(31,31,29,0.07)] backdrop-blur-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-[#d7d1c7] hover:bg-white hover:shadow-[0_18px_40px_rgba(31,31,29,0.1)]"
                                >
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="flex min-w-0 items-center gap-2.5">
                                            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-[#e6e3dc] bg-[#f3f1ed] text-[#252421] transition-colors group-hover:bg-[#111111] group-hover:text-white">
                                                <entry.icon className="h-4 w-4" />
                                            </span>
                                            <span className="whitespace-nowrap text-sm font-semibold text-[#252421]">{entry.label}</span>
                                        </div>
                                        <span className="shrink-0 text-2xl font-semibold leading-none text-[#151514]">{entry.count}</span>
                                    </div>
                                    <div className="mt-4 line-clamp-2 text-xs leading-5 text-[#777268]">{entry.detail}</div>
                                    <div className="mt-3 text-xs font-medium text-[#252421] opacity-0 transition-opacity group-hover:opacity-100">进入管理</div>
                                </button>
                            ))}

                            <div className="min-h-0 rounded-lg border border-[#e6e3dc] bg-[#fbfbfa]/95 p-3 shadow-[0_14px_36px_rgba(31,31,29,0.07)] backdrop-blur-sm sm:col-span-2 lg:col-span-3 xl:col-span-6">
                                    <div className="flex flex-col gap-2 md:flex-row">
                                        <div className="relative min-w-0 flex-1">
                                            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#8a857d]" />
                                            <Input
                                                value={previewQuestion}
                                                onChange={event => setPreviewQuestion(event.target.value)}
                                                placeholder="输入问数问题，先诊断可信可回答性，再按需执行预览"
                                                className="h-11 border-[#dedad2] bg-white pl-9 text-[#252421] shadow-inner placeholder:text-[#9a948a] focus-visible:ring-[#111111]/20"
                                            />
                                        </div>
                                        <div className="flex shrink-0 gap-2">
                                            <Button variant="outline" onClick={runReadinessCheck} disabled={isPreviewing || !previewQuestion.trim()} className="h-11 border-[#dedad2] bg-white px-4 text-[#2c2a27] hover:bg-[#f2f0ec]">
                                                <ShieldCheck className="mr-2 h-4 w-4" />可信诊断
                                            </Button>
                                            <Button onClick={runPreview} disabled={isPreviewing || !previewQuestion.trim()} className="h-11 bg-[#111111] px-5 text-white shadow-sm hover:bg-[#2a2a2a] md:min-w-24">
                                                {isPreviewing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Search className="mr-2 h-4 w-4" />}
                                                预览
                                            </Button>
                                            {(previewResult || previewError) && (
                                                <Button variant="outline" onClick={clearPreviewResult} className="h-11 border-[#dedad2] bg-white px-4 text-[#2c2a27] hover:bg-[#f2f0ec]">
                                                    清除
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                    {questionReadiness && (
                                        <div className={cn('mt-2 flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs', questionReadiness.answerable ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700' : questionReadiness.status === 'unsafe' ? 'border-rose-500/30 bg-rose-500/10 text-rose-700' : 'border-amber-500/30 bg-amber-500/10 text-amber-700')}>
                                            {questionReadiness.answerable ? <CircleCheck className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
                                            <span className="font-semibold">{questionReadiness.answerable ? '可由当前可信语义回答' : questionReadiness.error_type || '需要治理'}</span>
                                            <span>{questionReadiness.blockers[0] || questionReadiness.required_actions[0]}</span>
                                        </div>
                                    )}
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            <Dialog open={isPreviewDialogOpen} onOpenChange={setIsPreviewDialogOpen}>
                <DialogContent className="flex h-[min(760px,calc(100vh-72px))] max-w-6xl flex-col overflow-hidden border-manus-border bg-manus-secondary p-0 text-manus-text">
                    <DialogHeader className="shrink-0 border-b border-manus-border px-4 py-3 pr-12">
                        <DialogTitle className="flex items-center gap-2 text-lg">
                            <Search className="h-5 w-5 text-accent" />
                            问题预览
                        </DialogTitle>
                        <div className="truncate text-sm text-manus-muted">{previewQuestion || '预览语义计划、SQL 和只读执行结果'}</div>
                    </DialogHeader>
                    <div className="min-h-0 flex-1 overflow-y-auto p-4">
                        {isPreviewing && (
                            <div className="flex h-full min-h-[320px] items-center justify-center gap-3 text-manus-muted">
                                <Loader2 className="h-5 w-5 animate-spin text-accent" />
                                正在生成语义计划并执行只读查询
                            </div>
                        )}
                        {!isPreviewing && previewResult && (
                            <div className="grid gap-3 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
                                <div className="space-y-3">
                                    <div className="rounded-md bg-manus p-3">
                                        <div className="mb-2 text-xs text-manus-muted">SQL</div>
                                        <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs text-manus-text">{previewResult.sql}</pre>
                                    </div>
                                    <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                                        <div className="rounded-md bg-manus p-2 text-manus-muted">行数 <span className="ml-1 text-manus-text">{previewResult.row_count}</span></div>
                                        <div className="rounded-md bg-manus p-2 text-manus-muted">耗时 <span className="ml-1 text-manus-text">{previewResult.execution_time_ms}ms</span></div>
                                        <div className="rounded-md bg-manus p-2 text-manus-muted">运行 <span className="ml-1 text-manus-text">{previewResult.run_id || '-'}</span></div>
                                        <div className="rounded-md bg-manus p-2 text-manus-muted">表 <span className="ml-1 text-manus-text">{previewResult.referenced_tables.length}</span></div>
                                    </div>
                                    <div className="max-h-[360px] overflow-auto rounded-md border border-manus-border">
                                        <table className="w-full text-xs">
                                            <thead className="sticky top-0 bg-manus text-manus-muted">
                                                <tr>{previewResult.columns.map(column => <th key={column} className="px-3 py-2 text-left font-medium">{column}</th>)}</tr>
                                            </thead>
                                            <tbody>
                                                {previewResult.data.slice(0, 20).map((row, index) => (
                                                    <tr key={index} className="border-t border-manus-border">
                                                        {previewResult.columns.map(column => <td key={column} className="px-3 py-2 text-manus-text">{String(row[column] ?? '')}</td>)}
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
                                    <div>
                                        <div className="mb-1 text-xs text-manus-muted">Intent</div>
                                        <JsonBlock value={previewResult.intent} />
                                    </div>
                                    <div>
                                        <div className="mb-1 text-xs text-manus-muted">Plan</div>
                                        <JsonBlock value={previewResult.plan} />
                                    </div>
                                </div>
                            </div>
                        )}
                        {!isPreviewing && previewError && (
                            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
                                <div className="mb-2 text-sm font-medium text-amber-300">{previewError.error_type || 'preview_error'}: {previewError.message || '预览失败'}</div>
                                <JsonBlock value={previewError} />
                            </div>
                        )}
                    </div>
                </DialogContent>
            </Dialog>

            <Dialog open={activeWorkspace !== null} onOpenChange={open => {
                if (!open && activeWorkspace !== 'access-policies') setActiveWorkspace(null)
            }}>
                <DialogContent
                    hideCloseButton
                    className={cn(
                        'flex flex-col overflow-hidden bg-manus-secondary p-0 text-manus-text',
                        activeWorkspace === 'access-policies'
                            ? 'h-screen w-screen max-w-none rounded-none border-0 sm:rounded-none'
                            : 'h-[calc(100vh-32px)] w-[calc(100vw-32px)] max-w-7xl border-manus-border'
                    )}
                >
                    {activeWorkspace !== 'access-policies' && <DialogHeader className="shrink-0 border-b border-manus-border px-4 py-3">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="min-w-0">
                                <DialogTitle className="flex items-center gap-2 text-lg">
                                    <Network className="h-5 w-5 text-accent" />
                                    {activeWorkspaceMeta?.title || '语义工作区'}
                                </DialogTitle>
                                <div className="mt-1 text-sm text-manus-muted">{activeWorkspaceDescription}</div>
                            </div>
                            <Button size="sm" variant="outline" onClick={() => setActiveWorkspace(null)} className="border-manus-border bg-manus-tertiary text-manus-text">
                                返回首页
                            </Button>
                        </div>
                    </DialogHeader>}
                    <div className={cn('flex min-h-0 flex-1 flex-col', activeWorkspace === 'access-policies' ? 'p-0' : 'p-4')}>
                    {activeWorkspace === 'governance' && (
                        <div className="flex min-h-0 flex-1 flex-col gap-3">
                            <div className="grid shrink-0 gap-3 md:grid-cols-4">
                                <div className="rounded-md border border-manus-border bg-manus px-4 py-3">
                                    <div className="text-xs text-manus-muted">画像覆盖率</div>
                                    <div className="mt-1 text-2xl font-semibold text-manus-text">{Math.round((readiness?.governance?.profile_coverage || 0) * 100)}%</div>
                                </div>
                                <div className="rounded-md border border-manus-border bg-manus px-4 py-3">
                                    <div className="text-xs text-manus-muted">待复核候选</div>
                                    <div className="mt-1 text-2xl font-semibold text-manus-text">{openGovernanceCandidates.length}</div>
                                </div>
                                <div className="rounded-md border border-rose-500/25 bg-rose-500/10 px-4 py-3">
                                    <div className="text-xs text-rose-700">高风险候选</div>
                                    <div className="mt-1 text-2xl font-semibold text-rose-700">{readiness?.governance?.high_risk_candidates || 0}</div>
                                </div>
                                <div className="flex flex-col justify-between rounded-md border border-manus-border bg-manus px-4 py-3">
                                    <div className="flex items-center justify-between gap-3">
                                        <div>
                                            <div className="text-xs text-manus-muted">自动应用策略</div>
                                            <div className="mt-1 text-sm font-semibold text-manus-text">{governancePolicy?.observe_only ? '观察模式' : '安全自动治理'}</div>
                                        </div>
                                        <Switch checked={!governancePolicy?.observe_only} onCheckedChange={checked => setGovernanceObserveOnly(!checked)} disabled={isSaving || !governancePolicy} />
                                    </div>
                                    <div className="mt-3 border-t border-manus-border/70 pt-2 text-[11px] leading-4 text-manus-muted">
                                        候选由问数运行证据自动沉淀；观察模式不会自动应用变更。
                                    </div>
                                </div>
                            </div>

                            <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-md border border-manus-border bg-manus px-3 py-2">
                                <div className="flex flex-wrap items-center gap-2">
                                    <Select value={candidateStatusFilter} onValueChange={setCandidateStatusFilter}>
                                        <SelectTrigger className="h-8 w-32 border-manus-border bg-manus-secondary text-xs"><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="open">待处理</SelectItem><SelectItem value="all">全部</SelectItem><SelectItem value="accepted">已接受</SelectItem><SelectItem value="rejected">已拒绝</SelectItem><SelectItem value="auto_applied">自动应用</SelectItem><SelectItem value="rolled_back">已回滚</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <Select value={candidateRiskFilter} onValueChange={setCandidateRiskFilter}>
                                        <SelectTrigger className="h-8 w-32 border-manus-border bg-manus-secondary text-xs"><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">全部风险</SelectItem><SelectItem value="critical">严重</SelectItem><SelectItem value="high">高风险</SelectItem><SelectItem value="medium">中风险</SelectItem><SelectItem value="low">低风险</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <span className="text-xs text-manus-muted">仅展示评分 ≥ {governancePolicy?.review_threshold ?? 0.65} 的主待办</span>
                                </div>
                                <div className="flex gap-2">
                                    {selectedCandidateIds.length > 0 && <><Button size="sm" variant="outline" onClick={() => batchDecideGovernanceCandidates('reject')} disabled={isSaving} className="border-rose-500/30 text-rose-500">批量拒绝 {selectedCandidateIds.length}</Button><Button size="sm" variant="outline" onClick={() => batchDecideGovernanceCandidates('accept')} disabled={isSaving} className="border-emerald-500/30 text-emerald-600">批量接受 {selectedCandidateIds.length}</Button></>}
                                    <Button size="sm" variant="outline" onClick={loadTrustFoundation} disabled={isSaving} className="border-manus-border bg-manus-tertiary text-manus-text"><RefreshCw className="mr-1.5 h-4 w-4" />刷新</Button>
                                </div>
                            </div>

                            <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[minmax(340px,0.9fr)_minmax(0,1.35fr)]">
                                <div className="min-h-0 overflow-y-auto rounded-md border border-manus-border bg-manus-secondary">
                                    {visibleGovernanceCandidates.length === 0 ? (
                                        <div className="flex min-h-64 flex-col items-center justify-center gap-2 p-8 text-center"><CircleCheck className="h-8 w-8 text-emerald-500" /><div className="font-medium text-manus-text">当前筛选下没有候选</div><div className="text-sm text-manus-muted">问数过程中沉淀的证据达到阈值后，建议会自动进入这里。</div></div>
                                    ) : visibleGovernanceCandidates.map(candidate => (
                                        <div key={candidate.candidate_id} className={cn('grid grid-cols-[auto_minmax(0,1fr)] items-start border-b border-manus-border/70 last:border-b-0', selectedGovernanceCandidate?.candidate_id === candidate.candidate_id && 'bg-accent/10')}>
                                            <div className="px-3 pt-4"><Checkbox checked={selectedCandidateIds.includes(candidate.candidate_id)} disabled={!['proposed', 'needs_review', 'blocked'].includes(candidate.status)} onCheckedChange={checked => setSelectedCandidateIds(current => checked ? [...new Set([...current, candidate.candidate_id])] : current.filter(id => id !== candidate.candidate_id))} /></div>
                                            <button type="button" onClick={() => inspectGovernanceCandidate(candidate)} className="min-w-0 px-1 py-3 pr-4 text-left transition-colors hover:bg-manus-hover">
                                                <div className="flex items-start justify-between gap-3">
                                                    <div className="min-w-0">
                                                        <div className="truncate text-sm font-semibold text-manus-text">{candidate.title}</div>
                                                        <div className="mt-1 truncate text-xs text-manus-muted">{candidate.candidate_type} · {candidateTargetText(candidate)}</div>
                                                    </div>
                                                    <div className="text-right"><div className="text-lg font-semibold text-manus-text">{Math.round(candidate.score * 100)}%</div><div className={cn('text-[11px]', ['critical', 'high'].includes(candidate.risk_level) ? 'text-rose-500' : candidate.risk_level === 'medium' ? 'text-amber-500' : 'text-emerald-500')}>{candidate.risk_level}</div></div>
                                                </div>
                                                <div className="mt-2 flex flex-wrap gap-1.5">{candidate.source_types.map(source => <Badge key={source} variant="outline" className="h-5 border-manus-border bg-manus text-[10px] text-manus-muted">{source}</Badge>)}{candidate.conflicting_evidence.length > 0 && <Badge variant="outline" className="h-5 border-rose-500/30 bg-rose-500/10 text-[10px] text-rose-500">有冲突</Badge>}</div>
                                            </button>
                                        </div>
                                    ))}
                                </div>

                                <div className="min-h-0 overflow-y-auto rounded-md border border-manus-border bg-manus-secondary p-4">
                                    {!selectedGovernanceCandidate ? (
                                        <div className="flex min-h-72 flex-col items-center justify-center gap-2 text-center text-manus-muted"><Search className="h-8 w-8" /><div className="text-sm">选择一条候选查看建议 Diff 与证据时间线</div></div>
                                    ) : (
                                        <div className="space-y-4">
                                            <div className="flex flex-wrap items-start justify-between gap-3">
                                                <div>
                                                    <div className="text-lg font-semibold text-manus-text">{selectedGovernanceCandidate.title}</div>
                                                    <div className="mt-1 text-xs text-manus-muted">评分版本 {selectedGovernanceCandidate.score_version} · 策略 {selectedGovernanceCandidate.policy_version}</div>
                                                </div>
                                                <Badge variant="outline" className="border-accent/30 bg-accent/10 text-accent">置信度 {Math.round(selectedGovernanceCandidate.score * 100)}%</Badge>
                                            </div>
                                            <div className="rounded-md border border-accent/20 bg-accent/5 px-3 py-2 text-sm text-manus-text">
                                                {selectedGovernanceCandidate.priority_reason || '系统已按风险、影响面和证据强度排序该候选。'}
                                            </div>
                                            <div className="rounded-md border border-manus-border bg-manus px-3 py-2 text-xs text-manus-muted">
                                                <span className="mr-2 font-semibold text-manus-text">涉及对象</span>
                                                {candidateTargetText(selectedGovernanceCandidate)}
                                            </div>
                                            <div className="space-y-3 rounded-md border border-manus-border bg-manus p-3">
                                                <div className="text-xs font-semibold text-manus-muted">建议改什么</div>
                                                {candidateEditableFields(selectedGovernanceCandidate).length ? (
                                                    <div className="grid gap-3">
                                                        {candidateEditableFields(selectedGovernanceCandidate).map(field => renderCandidateField(field, !['proposed', 'needs_review', 'blocked'].includes(selectedGovernanceCandidate.status)))}
                                                    </div>
                                                ) : (
                                                    <div className="text-xs text-manus-muted">该候选没有可编辑字段。</div>
                                                )}
                                            </div>
                                            <div className="rounded-md border border-emerald-500/20 bg-emerald-500/5 p-3">
                                                <div className="text-xs font-semibold text-emerald-600">为什么可信</div>
                                                <div className="mt-2 space-y-1.5 text-xs text-manus-text">
                                                    {candidateEvidenceSummaries(selectedGovernanceCandidate).length ? candidateEvidenceSummaries(selectedGovernanceCandidate).map((summary, index) => (
                                                        <div key={index} className="leading-5">{summary}</div>
                                                    )) : <div className="text-manus-muted">暂无可判定证据说明。</div>}
                                                </div>
                                            </div>
                                            {selectedGovernanceCandidate.conflicting_evidence.length > 0 && (
                                                <div className="rounded-md border border-rose-500/20 bg-rose-500/5 p-3 text-xs text-rose-600">
                                                    存在 {selectedGovernanceCandidate.conflicting_evidence.length} 条冲突证据，建议先拒绝或修正后再接受。
                                                </div>
                                            )}
                                            <Collapsible open={isCandidateJsonOpen} onOpenChange={setIsCandidateJsonOpen}>
                                                <CollapsibleTrigger asChild>
                                                    <Button variant="outline" size="sm" className="w-full justify-between border-manus-border bg-manus-tertiary text-manus-text">
                                                        高级：查看或编辑原始 JSON Patch
                                                        <ChevronDown className={cn('h-4 w-4 transition-transform', isCandidateJsonOpen && 'rotate-180')} />
                                                    </Button>
                                                </CollapsibleTrigger>
                                                <CollapsibleContent className="mt-2 grid gap-3 md:grid-cols-2">
                                                    <div><div className="mb-1 text-xs font-medium text-manus-muted">变更前</div><pre className="max-h-48 overflow-auto rounded-md border border-manus-border bg-manus p-3 text-xs text-manus-text">{JSON.stringify(selectedGovernanceCandidate.before, null, 2)}</pre></div>
                                                    <div><div className="mb-1 text-xs font-medium text-manus-muted">建议变更</div><Textarea value={candidatePatchDraft} onChange={event => setCandidatePatchDraft(event.target.value)} className="min-h-48 border-manus-border bg-manus font-mono text-xs text-manus-text" disabled={!['proposed', 'needs_review', 'blocked'].includes(selectedGovernanceCandidate.status)} /></div>
                                                </CollapsibleContent>
                                            </Collapsible>
                                            <div><div className="mb-2 text-xs font-semibold text-manus-muted">原始证据时间线</div><div className="space-y-2">{governanceEvidence.length ? governanceEvidence.map(fact => <div key={fact.evidence_id} className="flex gap-3 rounded-md border border-manus-border bg-manus p-3 text-xs"><span className={cn('mt-1 h-2 w-2 shrink-0 rounded-full', fact.direction === 'conflict' ? 'bg-rose-500' : 'bg-emerald-500')} /><div className="min-w-0"><div className="font-medium text-manus-text">{fact.source_type} · 可靠度 {Math.round(fact.reliability * 100)}%</div><div className="mt-1 text-manus-muted">{fact.claim_type} · {new Date(fact.observed_at).toLocaleString()}</div></div></div>) : <div className="text-xs text-manus-muted">暂无已持久化证据</div>}</div></div>
                                            {['proposed', 'needs_review', 'blocked'].includes(selectedGovernanceCandidate.status) && <div className="space-y-2 border-t border-manus-border pt-3"><div className="flex flex-wrap gap-2"><Select value={rejectCategory} onValueChange={setRejectCategory}><SelectTrigger className="h-9 w-36 border-manus-border bg-manus"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="incorrect_mapping">错误映射</SelectItem><SelectItem value="wrong_formula">错误公式</SelectItem><SelectItem value="unsafe">不安全</SelectItem><SelectItem value="duplicate">重复</SelectItem><SelectItem value="not_relevant">不相关</SelectItem></SelectContent></Select><Input value={rejectReason} onChange={event => setRejectReason(event.target.value)} placeholder="填写拒绝原因" className="h-9 min-w-48 flex-1 border-manus-border bg-manus" /><Button variant="outline" onClick={rejectSelectedGovernanceCandidate} disabled={isSaving || !rejectReason.trim()} className="border-rose-500/30 text-rose-500 hover:bg-rose-500/10">拒绝</Button><Button onClick={acceptSelectedGovernanceCandidate} disabled={isSaving} className="bg-accent text-white">接受当前修改</Button></div><div className="text-[11px] text-manus-muted">接受后资产将固定为人工管理；LLM 单一证据永远不会触发自动应用。</div></div>}
                                            {selectedGovernanceCandidate.status === 'auto_applied' && <Button variant="outline" onClick={() => rollbackGovernanceCandidate(selectedGovernanceCandidate.candidate_id)} disabled={isSaving} className="border-amber-500/30 text-amber-600">回滚自动变更</Button>}
                                        </div>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}
                    {activeWorkspace === 'review' && (
                        <div className="flex min-h-0 flex-1 flex-col gap-3">
                            <div className="grid shrink-0 gap-3 sm:grid-cols-3">
                                <div className="rounded-md border border-manus-border bg-manus px-4 py-3">
                                    <div className="text-xs text-manus-muted">全部待复核</div>
                                    <div className="mt-1 text-2xl font-semibold text-manus-text">{pendingReviewAssets.length}</div>
                                </div>
                                <div className="rounded-md border border-amber-500/25 bg-amber-500/10 px-4 py-3">
                                    <div className="text-xs text-amber-700">过期，可检查后启用</div>
                                    <div className="mt-1 text-2xl font-semibold text-amber-700">{staleReviewCount}</div>
                                </div>
                                <div className="rounded-md border border-rose-500/25 bg-rose-500/10 px-4 py-3">
                                    <div className="text-xs text-rose-700">失联，必须先改绑</div>
                                    <div className="mt-1 text-2xl font-semibold text-rose-700">{orphanedReviewCount}</div>
                                </div>
                            </div>

                            <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-manus-border bg-manus-secondary">
                                {pendingReviewAssets.length === 0 ? (
                                    <div className="flex h-full min-h-72 flex-col items-center justify-center gap-3 p-8 text-center">
                                        <CircleCheck className="h-9 w-9 text-emerald-500" />
                                        <div className="font-medium text-manus-text">没有待复核资产</div>
                                        <div className="max-w-md text-sm text-manus-muted">当前语义目录与已应用的 Schema 基线一致。</div>
                                    </div>
                                ) : pendingReviewAssets.map(asset => (
                                    <div key={`${asset.modelType}-${asset.id}`} className="grid gap-3 border-b border-manus-border/70 px-4 py-4 last:border-b-0 lg:grid-cols-[minmax(180px,0.8fr)_minmax(260px,1.4fr)_auto] lg:items-center">
                                        <div className="min-w-0">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge variant="outline" className="h-6 border-manus-border bg-manus-tertiary text-[11px] text-manus-muted">{asset.kind}</Badge>
                                                {syncBadge(asset.syncState)}
                                                {asset.scanId && <span className="text-[11px] text-manus-muted">扫描 #{asset.scanId}</span>}
                                            </div>
                                            <div className="mt-2 truncate text-sm font-semibold text-manus-text">{asset.name}</div>
                                            <div className="mt-1 truncate font-mono text-xs text-manus-muted">{asset.physicalReference}</div>
                                        </div>
                                        <div className="rounded-md border border-manus-border/70 bg-manus px-3 py-2 text-sm leading-6 text-manus-muted">
                                            {asset.reason}
                                        </div>
                                        <div className="flex flex-wrap justify-end gap-2">
                                            <Button size="sm" variant="outline" onClick={() => inspectReviewAsset(asset)} className="border-manus-border bg-manus-tertiary text-manus-text">
                                                检查详情
                                            </Button>
                                            {asset.syncState === 'stale' ? (
                                                <Button size="sm" onClick={() => updateModel(asset.modelType, asset.id, { status: 'confirmed', is_queryable: true, sync_state: 'current' })} disabled={isSaving} className="bg-amber-600 text-white hover:bg-amber-700">
                                                    {isSaving && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
                                                    复核启用
                                                </Button>
                                            ) : (
                                                <span className="self-center text-xs font-medium text-rose-600">需改绑后处理</span>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                    {activeWorkspace === 'access-policies' && (
                        <SemanticAccessPolicyWorkspaceV2
                            datasourceId={data.datasource?.id || 0}
                            datasourceName={data.datasource?.name}
                            tables={data.tables}
                            columns={data.columns}
                            metrics={data.metrics}
                            onClose={() => setActiveWorkspace(null)}
                        />
                    )}
                    {activeWorkspace === 'tables' && (
                        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 xl:grid-cols-[280px_minmax(0,1fr)]">
                            <div className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-md border border-manus-border">
                                <div className="shrink-0 bg-manus px-3 py-2 text-xs text-manus-muted">语义表</div>
                                <div className="min-h-0 flex-1 overflow-y-auto">
                                    {data.tables.map(table => (
                                        <button
                                            key={table.id}
                                            onClick={() => setActiveTableId(table.id)}
                                            className={cn('w-full border-t border-manus-border px-3 py-2.5 text-left transition-colors hover:bg-manus-hover', activeTableId === table.id && 'bg-accent/10')}
                                        >
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="truncate text-sm font-medium text-manus-text">{table.business_name}</span>
                                                <span className="flex items-center gap-1">{syncBadge(table.sync_state)}{statusBadge(table.status)}</span>
                                            </div>
                                            <div className="mt-1 truncate text-xs text-manus-muted">{table.physical_name}</div>
                                        </button>
                                    ))}
                                </div>
                            </div>

                            <div className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-md border border-manus-border">
                                {activeTable ? (
                                    <>
                                        <div className="shrink-0 space-y-3 border-b border-manus-border bg-manus px-4 py-3">
                                            <div className="flex flex-wrap items-center justify-between gap-3">
                                                <div className="min-w-0">
                                                    <div className="flex flex-wrap items-center gap-2">
                                                        <span className="break-all font-medium text-manus-text">{activeTable.business_name}</span>
                                                        {syncBadge(activeTable.sync_state)}
                                                        {statusBadge(activeTable.status)}
                                                        {accessBadges(activeTable.is_sensitive, activeTable.business_semantics_status)}
                                                    </div>
                                                    <div className="mt-1 break-all text-xs text-manus-muted">{activeTable.physical_name}</div>
                                                </div>
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        onClick={() => generateBusinessSuggestions('table', activeTable.id, true)}
                                                        disabled={isSaving}
                                                        className="border-manus-border bg-manus-tertiary text-manus-text"
                                                    >
                                                        <Wand2 className="mr-1 h-4 w-4" />
                                                        生成语义建议
                                                    </Button>
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        onClick={() => acceptTableBusinessSuggestions(activeTable.id)}
                                                        disabled={isSaving || activeTableSuggestions.length === 0}
                                                        className="border-emerald-500/30 text-emerald-600"
                                                    >
                                                        接受本表全部建议
                                                    </Button>
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        onClick={() => setActiveWorkspace('access-policies')}
                                                        className="border-manus-border bg-manus-tertiary text-manus-text"
                                                    >
                                                        <Lock className="mr-1 h-4 w-4" />
                                                        权限
                                                    </Button>
                                                    <Switch
                                                        checked={activeTable.status === 'confirmed' && activeTable.is_queryable}
                                                        disabled={isSaving || activeTable.sync_state === 'orphaned'}
                                                        onCheckedChange={checked => updateModel('tables', activeTable.id, { status: checked ? 'confirmed' : 'disabled', is_queryable: checked, ...(checked ? { sync_state: 'current' } : {}) })}
                                                    />
                                                </div>
                                            </div>
                                            {(() => {
                                                const tableDraft = draftForObject('table', activeTable)
                                                const tableEditing = isEditingObject('table', activeTable.id)
                                                return tableEditing ? (
                                                    <div className="grid gap-2 lg:grid-cols-[180px_minmax(0,1fr)_220px_auto]">
                                                        <Input value={tableDraft.businessName} onChange={event => setObjectDraft('table', activeTable.id, { businessName: event.target.value })} placeholder="业务表名" />
                                                        <Input value={tableDraft.description} onChange={event => setObjectDraft('table', activeTable.id, { description: event.target.value })} placeholder="业务说明" />
                                                        <Input value={tableDraft.synonyms} onChange={event => setObjectDraft('table', activeTable.id, { synonyms: event.target.value })} placeholder="同义词，逗号分隔" />
                                                        <Button size="sm" variant="outline" onClick={() => saveObjectEdit('table', activeTable, tableDraft)} disabled={isSaving || !tableDraft.businessName.trim()} className="border-manus-border bg-manus-tertiary text-manus-text">
                                                            <Save className="mr-1 h-4 w-4" />
                                                            保存
                                                        </Button>
                                                    </div>
                                                ) : (
                                                    <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
                                                        <div className="grid min-w-0 flex-1 gap-x-5 gap-y-1 lg:grid-cols-[180px_minmax(0,1fr)_240px]">
                                                            <div><span className="mr-2 text-xs text-manus-muted">业务名</span><span className="break-words text-manus-text">{tableDraft.businessName || '-'}</span></div>
                                                            <div><span className="mr-2 text-xs text-manus-muted">说明</span><span className="break-words text-manus-text">{tableDraft.description || '-'}</span></div>
                                                            <div><span className="mr-2 text-xs text-manus-muted">同义词</span><span className="break-words text-manus-text">{tableDraft.synonyms || '-'}</span></div>
                                                        </div>
                                                        <div className="flex gap-2">
                                                            <Button size="sm" variant="outline" onClick={() => startObjectEdit('table', activeTable)} disabled={isSaving} className="border-manus-border bg-manus-tertiary text-manus-text">
                                                                <Pencil className="mr-1 h-4 w-4" />
                                                                编辑
                                                            </Button>
                                                        </div>
                                                    </div>
                                                )
                                            })()}
                                            <div className="grid gap-3 border-t border-manus-border/70 pt-3 lg:grid-cols-[180px_minmax(0,1fr)_220px]">
                                                <div>
                                                    <div className="text-sm font-medium text-manus-text">行级权限</div>
                                                    <div className="mt-1 text-xs text-manus-muted">由统一语义访问策略编译执行</div>
                                                </div>
                                                <div className="rounded border border-manus-border bg-manus-secondary px-3 py-2 text-sm text-manus-muted">
                                                    当前模式：
                                                    <span className="ml-1 text-manus-text">
                                                        统一策略中心
                                                    </span>
                                                    <span className="ml-2 text-xs">支持本人、部门和静态条件模板。</span>
                                                </div>
                                                <Button size="sm" variant="outline" onClick={() => setActiveWorkspace('access-policies')} className="border-manus-border bg-manus-tertiary text-manus-text">
                                                    打开权限策略
                                                </Button>
                                            </div>
                                        </div>

                                        <div className="grid shrink-0 grid-cols-[minmax(170px,0.9fr)_minmax(260px,1.5fr)_150px_230px] border-b border-manus-border bg-manus-secondary px-4 py-2 text-sm text-manus-muted">
                                            <div>字段</div>
                                            <div>业务语义</div>
                                            <div>权限</div>
                                            <div className="text-right">操作</div>
                                        </div>
                                        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
                                            {activeColumns.map(column => {
                                                const tableEnabled = activeTable.status === 'confirmed' && activeTable.is_queryable
                                                const columnEnabled = tableEnabled && column.status === 'confirmed' && column.is_queryable
                                                const columnDraft = draftForObject('column', column)
                                                const columnEditing = isEditingObject('column', column.id)
                                                return (
                                                    <div key={column.id} className="grid grid-cols-[minmax(170px,0.9fr)_minmax(260px,1.5fr)_150px_230px] items-start gap-3 border-b border-manus-border/70 px-4 py-3 text-sm">
                                                        <div className="min-w-0">
                                                            <div className="flex flex-wrap items-center gap-2">
                                                                <span className="break-all font-medium text-manus-text">{column.physical_name}</span>
                                                                {syncBadge(column.sync_state)}
                                                            </div>
                                                            <div className="mt-1 break-all text-xs text-manus-muted">{column.data_type}</div>
                                                            <div className="mt-1 flex items-center gap-2 text-[11px] text-manus-muted">
                                                                {column.is_primary_key && <span>主键</span>}
                                                                {column.is_indexed && <span>索引</span>}
                                                            </div>
                                                        </div>
                                                        <div className="min-w-0">
                                                            {columnEditing ? (
                                                                <div className="space-y-2">
                                                                    <Input value={columnDraft.businessName} onChange={event => setObjectDraft('column', column.id, { businessName: event.target.value })} placeholder="业务字段名" className="h-8 border-manus-border bg-manus-secondary" />
                                                                    <Input value={columnDraft.description} onChange={event => setObjectDraft('column', column.id, { description: event.target.value })} placeholder="字段说明" className="h-8 border-manus-border bg-manus-secondary" />
                                                                    <Input value={columnDraft.synonyms} onChange={event => setObjectDraft('column', column.id, { synonyms: event.target.value })} placeholder="同义词，逗号分隔" className="h-8 border-manus-border bg-manus-secondary" />
                                                                </div>
                                                            ) : (
                                                                <div className="space-y-1.5">
                                                                    <div><span className="mr-2 text-xs text-manus-muted">业务名</span><span className="break-words font-medium text-manus-text">{columnDraft.businessName || '-'}</span></div>
                                                                    <div><span className="mr-2 text-xs text-manus-muted">说明</span><span className="break-words text-manus-text">{columnDraft.description || '-'}</span></div>
                                                                    <div><span className="mr-2 text-xs text-manus-muted">同义词</span><span className="break-words text-manus-text">{columnDraft.synonyms || '-'}</span></div>
                                                                </div>
                                                            )}
                                                        </div>
                                                        <div>{accessBadges(column.is_sensitive, column.business_semantics_status)}</div>
                                                        <div className="flex flex-wrap items-center justify-end gap-2">
                                                            {columnEditing ? (
                                                                <Button size="sm" variant="outline" onClick={() => saveObjectEdit('column', column, columnDraft)} disabled={isSaving || !columnDraft.businessName.trim()} className="border-manus-border bg-manus-tertiary text-manus-text">
                                                                    <Save className="mr-1 h-4 w-4" />
                                                                    保存
                                                                </Button>
                                                            ) : (
                                                                <Button size="sm" variant="outline" onClick={() => startObjectEdit('column', column)} disabled={isSaving} className="border-manus-border bg-manus-tertiary text-manus-text">
                                                                    <Pencil className="mr-1 h-4 w-4" />
                                                                    编辑
                                                                </Button>
                                                            )}
                                                            <Button
                                                                size="sm"
                                                                variant="outline"
                                                                onClick={() => setActiveWorkspace('access-policies')}
                                                                className="border-manus-border bg-manus-tertiary text-manus-text"
                                                            >
                                                                <Lock className="h-4 w-4" />
                                                            </Button>
                                                            <Switch checked={columnEnabled} disabled={isSaving || !tableEnabled || column.sync_state === 'orphaned'} onCheckedChange={checked => updateModel('columns', column.id, { status: checked ? 'confirmed' : 'disabled', is_queryable: checked, ...(checked ? { sync_state: 'current' } : {}) })} />
                                                        </div>
                                                    </div>
                                                )
                                            })}
                                        </div>
                                    </>
                                ) : (
                                    <div className="p-8 text-center text-manus-muted">暂无表</div>
                                )}
                            </div>
                        </div>
                    )}

                    {activeWorkspace === 'metrics' && (
                        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden xl:grid-cols-[380px_minmax(0,1fr)]">
                            <div className="overflow-y-auto rounded-md border border-manus-border bg-manus p-4">
                                <div className="mb-3 font-medium text-manus-text">新建指标</div>
                                {renderMetricForm(metricForm, setMetricForm, metricColumns, '保存指标', submitMetric)}
                            </div>
                            <div className="overflow-auto rounded-md border border-manus-border">
                                <div className="flex items-center justify-between gap-3 border-b border-manus-border bg-manus px-4 py-3">
                                    <div>
                                        <div className="text-sm font-medium text-manus-text">指标建议</div>
                                        <div className="mt-1 text-xs text-manus-muted">生成后为待确认状态，确认后才允许问数。</div>
                                    </div>
                                    <Button size="sm" variant="outline" onClick={generateMetricSuggestions} disabled={isSaving} className="border-manus-border bg-manus-tertiary text-manus-text">
                                        <Wand2 className="mr-1 h-4 w-4" />
                                        生成语义建议
                                    </Button>
                                </div>
                                <table className="w-full text-sm">
                                    <thead className="bg-manus">
                                        <tr className="text-left text-manus-muted">
                                            <th className="px-4 py-2 font-medium">指标</th>
                                            <th className="px-4 py-2 font-medium">公式</th>
                                            <th className="px-4 py-2 font-medium">时间口径</th>
                                            <th className="px-4 py-2 font-medium">权限</th>
                                            <th className="px-4 py-2 font-medium">状态</th>
                                            <th className="px-4 py-2 font-medium text-right">操作</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.metrics.map(metric => {
                                            const context = (metric.evidence_json?.target_context || {}) as Record<string, unknown>
                                            return (
                                            <tr key={metric.id} className="border-t border-manus-border align-top">
                                                <td className="px-4 py-3">
                                                    <div className="font-medium text-manus-text">{metric.business_name}</div>
                                                    <div className="text-xs text-manus-muted">{metric.name}</div>
                                                    <div className="mt-1 text-xs text-manus-muted">主表：{contextValue(context, 'table_physical_name') || tableName(metric.table_id)}</div>
                                                    <div className="text-xs text-manus-muted">指标字段：{contextValue(context, 'column_physical_name') || columnName(metric.column_id)}</div>
                                                </td>
                                                <td className="max-w-[340px] px-4 py-3">
                                                    <div className="font-mono text-xs text-manus-muted">{metric.formula}</div>
                                                    {contextValue(context, 'basis') && <div className="mt-1 text-xs text-manus-muted">依据：{contextValue(context, 'basis')}</div>}
                                                </td>
                                                <td className="px-4 py-3 text-manus-muted">
                                                    <div>{contextValue(context, 'time_column_physical_name') || columnName(metric.time_column_id)}</div>
                                                    <div className="text-xs">{metric.default_grain || '-'}</div>
                                                </td>
                                                <td className="px-4 py-3">{accessBadges(metric.is_sensitive)}</td>
                                                <td className="px-4 py-3"><div className="flex gap-1">{syncBadge(metric.sync_state)}{statusBadge(metric.status)}</div></td>
                                                <td className="px-4 py-3">
                                                    <div className="flex justify-end gap-2">
                                                        <Button size="sm" variant="outline" onClick={() => { setEditingMetricId(metric.id); setEditingMetricForm(metricToForm(metric)) }} className="border-manus-border bg-manus-tertiary text-manus-text">
                                                            <Pencil className="mr-1 h-4 w-4" />
                                                            编辑
                                                        </Button>
                                                        <Button
                                                            size="sm"
                                                            variant="outline"
                                                            onClick={() => setActiveWorkspace('access-policies')}
                                                            className="border-manus-border bg-manus-tertiary text-manus-text"
                                                        >
                                                            <Lock className="h-4 w-4" />
                                                        </Button>
                                                        <Button size="sm" variant="outline" onClick={() => updateModel('metrics', metric.id, { status: 'confirmed', is_queryable: true, sync_state: 'current' })} disabled={isSaving || (metric.status === 'confirmed' && metric.is_queryable && (metric.sync_state || 'current') === 'current')} className="border-manus-border bg-manus-tertiary text-manus-text">
                                                            {(metric.sync_state || 'current') === 'current' ? '确认' : '复核启用'}
                                                        </Button>
                                                    </div>
                                                </td>
                                            </tr>
                                            )
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    {activeWorkspace === 'relationships' && (
                        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden xl:grid-cols-[380px_minmax(0,1fr)]">
                            <div className="space-y-3 overflow-y-auto rounded-md border border-manus-border bg-manus p-4">
                                <div className="font-medium text-manus-text">新建关系</div>
                                <div className="grid grid-cols-2 gap-2">
                                    <Select value={relationshipForm.left_table_id?.toString() || ''} onValueChange={value => setRelationshipForm({ ...relationshipForm, left_table_id: Number(value), left_column_id: null })}>
                                        <SelectTrigger className="border-manus-border bg-manus-secondary"><SelectValue placeholder="左表" /></SelectTrigger>
                                        <SelectContent>{confirmedTables.map(table => <SelectItem key={table.id} value={table.id.toString()}>{table.business_name}</SelectItem>)}</SelectContent>
                                    </Select>
                                    <Select value={relationshipForm.right_table_id?.toString() || ''} onValueChange={value => setRelationshipForm({ ...relationshipForm, right_table_id: Number(value), right_column_id: null })}>
                                        <SelectTrigger className="border-manus-border bg-manus-secondary"><SelectValue placeholder="右表" /></SelectTrigger>
                                        <SelectContent>{confirmedTables.map(table => <SelectItem key={table.id} value={table.id.toString()}>{table.business_name}</SelectItem>)}</SelectContent>
                                    </Select>
                                    <Select value={relationshipForm.left_column_id?.toString() || ''} onValueChange={value => setRelationshipForm({ ...relationshipForm, left_column_id: Number(value) })}>
                                        <SelectTrigger className="border-manus-border bg-manus-secondary"><SelectValue placeholder="左字段" /></SelectTrigger>
                                        <SelectContent>{columnsForRelationship.left.map(column => <SelectItem key={column.id} value={column.id.toString()}>{column.business_name}</SelectItem>)}</SelectContent>
                                    </Select>
                                    <Select value={relationshipForm.right_column_id?.toString() || ''} onValueChange={value => setRelationshipForm({ ...relationshipForm, right_column_id: Number(value) })}>
                                        <SelectTrigger className="border-manus-border bg-manus-secondary"><SelectValue placeholder="右字段" /></SelectTrigger>
                                        <SelectContent>{columnsForRelationship.right.map(column => <SelectItem key={column.id} value={column.id.toString()}>{column.business_name}</SelectItem>)}</SelectContent>
                                    </Select>
                                </div>
                                <Textarea value={relationshipForm.description} onChange={event => setRelationshipForm({ ...relationshipForm, description: event.target.value })} placeholder="关系说明" className="border-manus-border bg-manus-secondary text-manus-text" />
                                <Button onClick={submitRelationship} disabled={isSaving} className="w-full bg-accent text-white hover:bg-accent/90">
                                    <GitBranch className="mr-2 h-4 w-4" />
                                    保存关系
                                </Button>
                            </div>
                            <div className="overflow-auto rounded-md border border-manus-border">
                                <div className="flex items-center justify-between gap-3 border-b border-manus-border bg-manus px-4 py-3">
                                    <div>
                                        <div className="text-sm font-medium text-manus-text">关系建议</div>
                                        <div className="mt-1 text-xs text-manus-muted">生成后为待确认状态，确认后才启用跨表关联。</div>
                                    </div>
                                    <Button size="sm" variant="outline" onClick={generateRelationshipSuggestions} disabled={isSaving} className="border-manus-border bg-manus-tertiary text-manus-text">
                                        <Wand2 className="mr-1 h-4 w-4" />
                                        生成语义建议
                                    </Button>
                                </div>
                                <table className="w-full text-sm">
                                    <thead className="bg-manus">
                                        <tr className="text-left text-manus-muted">
                                            <th className="px-4 py-2 font-medium">左侧</th>
                                            <th className="px-4 py-2 font-medium">右侧</th>
                                            <th className="px-4 py-2 font-medium">类型</th>
                                            <th className="px-4 py-2 font-medium">状态</th>
                                            <th className="px-4 py-2 font-medium text-right">操作</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.relationships.map(rel => {
                                            const context = (rel.evidence_json?.target_context || {}) as Record<string, unknown>
                                            const leftTable = contextValue(context, 'left_table_physical_name') || tableName(rel.left_table_id)
                                            const leftColumn = contextValue(context, 'left_column_physical_name') || columnName(rel.left_column_id)
                                            const rightTable = contextValue(context, 'right_table_physical_name') || tableName(rel.right_table_id)
                                            const rightColumn = contextValue(context, 'right_column_physical_name') || columnName(rel.right_column_id)
                                            return (
                                            <tr key={rel.id} className="border-t border-manus-border">
                                                <td className="px-4 py-3 text-manus-text">
                                                    <div>{leftTable}.{leftColumn}</div>
                                                    <div className="mt-1 text-xs text-manus-muted">{contextValue(context, 'left_table_business_name') || tableName(rel.left_table_id)}</div>
                                                </td>
                                                <td className="px-4 py-3 text-manus-text">
                                                    <div>{rightTable}.{rightColumn}</div>
                                                    <div className="mt-1 text-xs text-manus-muted">{contextValue(context, 'right_table_business_name') || tableName(rel.right_table_id)}</div>
                                                </td>
                                                <td className="px-4 py-3 text-manus-muted">
                                                    <div>{rel.relationship_type}</div>
                                                    {contextValue(context, 'basis') && <div className="mt-1 text-xs">依据：{contextValue(context, 'basis')}</div>}
                                                </td>
                                                <td className="px-4 py-3"><div className="flex gap-1">{syncBadge(rel.sync_state)}{statusBadge(rel.status)}</div></td>
                                                <td className="px-4 py-3 text-right">
                                                    <Button size="sm" variant="outline" onClick={() => updateModel('relationships', rel.id, { status: 'confirmed', is_queryable: true, sync_state: 'current' })} disabled={isSaving || (rel.status === 'confirmed' && rel.is_queryable && (rel.sync_state || 'current') === 'current')} className="border-manus-border bg-manus-tertiary text-manus-text">
                                                        {(rel.sync_state || 'current') === 'current' ? '确认' : '复核启用'}
                                                    </Button>
                                                </td>
                                            </tr>
                                            )
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    {activeWorkspace === 'evaluation' && (
                        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden">
                            <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 rounded-md border border-manus-border bg-manus p-3">
                                <div>
                                    <div className="text-sm font-medium text-manus-text">15 问评估闭环</div>
                                    <div className="mt-1 text-xs text-manus-muted">评估通过状态只看语义链路；旧 XiYan 链路仅保留为历史对照。</div>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    <Button size="sm" variant="outline" onClick={loadEvaluation} disabled={isRunningEvaluation} className="border-manus-border bg-manus-tertiary text-manus-text">
                                        <RefreshCw className="mr-1 h-4 w-4" />
                                        刷新
                                    </Button>
                                    <Button size="sm" onClick={() => runEvaluation()} disabled={isRunningEvaluation || evalCases.length === 0} className="bg-accent text-white hover:bg-accent/90">
                                        {isRunningEvaluation ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <PlayCircle className="mr-1 h-4 w-4" />}
                                        运行全部
                                    </Button>
                                </div>
                            </div>

                            <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-manus-border">
                                {evalCases.map(testCase => {
                                    const latestRun = latestEvalRuns.get(testCase.case_id)
                                    const semanticResult = latestRun?.semantic_result
                                    const legacyResult = latestRun?.legacy_result
                                    const semanticVerdict = latestRun?.semantic_verdict || latestRun?.verdict
                                    return (
                                        <div key={testCase.case_id} className="border-b border-manus-border p-4 last:border-b-0">
                                            <div className="flex flex-wrap items-start justify-between gap-3">
                                                <div className="min-w-0 flex-1">
                                                    <div className="flex flex-wrap items-center gap-2">
                                                        <Badge variant="outline" className="border-manus-border bg-manus-tertiary text-xs text-manus-muted">{testCase.case_id}</Badge>
                                                        <Badge variant="outline" className="border-manus-border bg-manus-tertiary text-xs text-manus-muted">{testCase.test_dimension}</Badge>
                                                        <Badge variant="outline" className={cn('text-xs', verdictClass(semanticVerdict))}>{semanticVerdict || 'not_run'}</Badge>
                                                    </div>
                                                    <div className="mt-2 break-words text-sm font-medium text-manus-text">{testCase.question}</div>
                                                    <div className="mt-1 break-words text-xs text-manus-muted">{testCase.expected_focus.join(' / ')}</div>
                                                </div>
                                                <Button size="sm" variant="outline" onClick={() => runEvaluation([testCase.case_id])} disabled={isRunningEvaluation} className="border-manus-border bg-manus-tertiary text-manus-text">
                                                    {isRunningEvaluation ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <PlayCircle className="mr-1 h-4 w-4" />}
                                                    运行单题
                                                </Button>
                                            </div>

                                            {latestRun ? (
                                                <div className="mt-3 grid gap-3 xl:grid-cols-2">
                                                    {[
                                                        { title: '语义链路', result: semanticResult },
                                                        { title: '旧 XiYan 链路', result: legacyResult },
                                                    ].map(item => (
                                                        <div key={item.title} className="rounded-md border border-manus-border bg-manus p-3">
                                                            <div className="flex flex-wrap items-center justify-between gap-2">
                                                                <div className="text-sm font-medium text-manus-text">{item.title}</div>
                                                                <Badge variant="outline" className={cn('text-xs', chainStatusClass(item.result))}>
                                                                    {item.result?.error_type || item.result?.status || 'not_run'}
                                                                </Badge>
                                                            </div>
                                                            <div className="mt-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-3">
                                                                <div className="rounded-md bg-manus-secondary p-2 text-manus-muted">行数 <span className="ml-1 text-manus-text">{item.result?.row_count ?? '-'}</span></div>
                                                                <div className="rounded-md bg-manus-secondary p-2 text-manus-muted">耗时 <span className="ml-1 text-manus-text">{item.result?.execution_time_ms ?? '-'}ms</span></div>
                                                                <div className="rounded-md bg-manus-secondary p-2 text-manus-muted">诊断 <span className="ml-1 text-manus-text">{item.result?.diagnostics?.length ?? 0}</span></div>
                                                            </div>
                                                            {item.result?.error && (
                                                                <div className="mt-2 rounded-md border border-rose-500/20 bg-rose-500/10 p-2 text-xs text-rose-300">{item.result.error}</div>
                                                            )}
                                                            {Boolean(item.result?.diagnostics?.length) && (
                                                                <div className="mt-2 space-y-1">
                                                                    {item.result!.diagnostics.map((diagnostic, index) => (
                                                                        <div key={`${diagnostic.type}-${index}`} className="rounded-md border border-amber-500/20 bg-amber-500/10 p-2 text-xs text-amber-200">
                                                                            {diagnostic.message}
                                                                        </div>
                                                                    ))}
                                                                </div>
                                                            )}
                                                            <div className="mt-2 space-y-2">
                                                                <details className="rounded-md border border-manus-border bg-manus-secondary">
                                                                    <summary className="cursor-pointer px-3 py-2 text-xs text-manus-muted">SQL</summary>
                                                                    <pre className="max-h-44 overflow-auto whitespace-pre-wrap px-3 pb-3 text-xs text-manus-text">{item.result?.sql || '-'}</pre>
                                                                </details>
                                                                <details className="rounded-md border border-manus-border bg-manus-secondary">
                                                                    <summary className="cursor-pointer px-3 py-2 text-xs text-manus-muted">结果预览</summary>
                                                                    <div className="px-3 pb-3"><JsonBlock value={item.result?.result_preview || null} /></div>
                                                                </details>
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <div className="mt-3 rounded-md border border-manus-border bg-manus p-3 text-sm text-manus-muted">尚未运行</div>
                                            )}
                                        </div>
                                    )
                                })}
                                {evalCases.length === 0 && (
                                    <div className="flex items-center justify-center gap-2 py-8 text-manus-muted">
                                        <PlayCircle className="h-4 w-4" />
                                        暂无评估用例
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {activeWorkspace === 'runs' && (
                        <div className="min-h-0 flex-1 overflow-auto rounded-md border border-manus-border">
                            <table className="w-full text-sm">
                                <thead className="bg-manus">
                                    <tr className="text-left text-manus-muted">
                                        <th className="px-4 py-2 font-medium">问题</th>
                                        <th className="px-4 py-2 font-medium">状态</th>
                                        <th className="px-4 py-2 font-medium">兜底</th>
                                        <th className="px-4 py-2 font-medium">行数</th>
                                        <th className="px-4 py-2 font-medium">耗时</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.recent_runs.map(run => (
                                        <tr key={run.id} className="border-t border-manus-border">
                                            <td className="max-w-[460px] truncate px-4 py-3 text-manus-text">{run.question}</td>
                                            <td className="px-4 py-3">
                                                <Badge variant="outline" className={cn(run.status === 'success' ? 'border-emerald-500/30 text-emerald-500' : 'border-amber-500/30 text-amber-500')}>
                                                    {run.error_type || run.status}
                                                </Badge>
                                            </td>
                                            <td className="px-4 py-3 text-manus-muted">{run.fallback_used ? '是' : '否'}</td>
                                            <td className="px-4 py-3 text-manus-muted">{run.row_count}</td>
                                            <td className="px-4 py-3 text-manus-muted">{run.execution_time_ms} ms</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            {data.recent_runs.length === 0 && (
                                <div className="flex items-center justify-center gap-2 py-8 text-manus-muted">
                                    <PlayCircle className="h-4 w-4" />
                                    暂无运行记录
                                </div>
                            )}
                        </div>
                    )}
                    </div>
                </DialogContent>
            </Dialog>

            <Dialog open={isScanConfirmOpen} onOpenChange={setIsScanConfirmOpen}>
                <DialogContent className="max-w-5xl border-manus-border bg-manus-secondary text-manus-text">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2"><Database className="h-5 w-5 text-accent" />Schema Diff 可信预览</DialogTitle>
                    </DialogHeader>
                    {!scanPreview ? (
                        <div className="flex min-h-64 items-center justify-center gap-3 text-manus-muted"><Loader2 className="h-5 w-5 animate-spin text-accent" />正在只读扫描数据库结构</div>
                    ) : (
                        <div className="space-y-4">
                            <div className="grid grid-cols-4 gap-2">
                                {[
                                    ['新增', scanPreview.summary.added || 0, 'text-emerald-500'],
                                    ['修改', scanPreview.summary.modified || 0, 'text-amber-500'],
                                    ['移除', scanPreview.summary.removed || 0, 'text-rose-500'],
                                    ['影响资产', scanPreview.affected_assets.length, 'text-accent'],
                                ].map(([label, value, tone]) => (
                                    <div key={String(label)} className="rounded-md border border-manus-border bg-manus p-3"><div className="text-xs text-manus-muted">{label}</div><div className={cn('mt-2 text-2xl font-semibold', tone)}>{value}</div></div>
                                ))}
                            </div>
                            <div className="max-h-[420px] overflow-auto rounded-md border border-manus-border">
                                <table className="w-full text-xs">
                                    <thead className="sticky top-0 bg-manus text-manus-muted"><tr><th className="px-3 py-2 text-left">变化</th><th className="px-3 py-2 text-left">物理对象</th><th className="px-3 py-2 text-left">风险</th><th className="px-3 py-2 text-left">说明</th></tr></thead>
                                    <tbody>
                                        {scanPreview.diff_items.map((item, index) => (
                                            <tr key={`${item.physical_identity}-${index}`} className="border-t border-manus-border">
                                                <td className="px-3 py-2"><Badge variant="outline" className={cn(item.change_type === 'added' ? 'text-emerald-500' : item.change_type === 'removed' ? 'text-rose-500' : 'text-amber-500')}>{item.change_type}</Badge></td>
                                                <td className="px-3 py-2 font-mono text-manus-text">{item.physical_identity}</td>
                                                <td className="px-3 py-2">{item.blocking ? <span className="text-rose-500">关键漂移</span> : <span className="text-manus-muted">非破坏</span>}</td>
                                                <td className="max-w-sm px-3 py-2 text-manus-muted">{item.change_type === 'removed' ? '对象将标记失联；依赖资产停用，需改绑后复核。' : item.blocking ? '依赖资产将标记过期；应用后检查公式或关联字段，再复核启用。' : '仅同步物理元数据。'}</td>
                                            </tr>
                                        ))}
                                        {scanPreview.diff_items.length === 0 && <tr><td colSpan={4} className="px-3 py-12 text-center text-manus-muted">Schema 与已应用基线一致</td></tr>}
                                    </tbody>
                                </table>
                            </div>
                            <div className="rounded-md border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-600">整批应用采用单一事务。人工维护的业务名、公式、说明、权限和状态不会被扫描覆盖。</div>
                        </div>
                    )}
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setIsScanConfirmOpen(false)}>取消</Button>
                        <Button onClick={confirmScan} disabled={isSaving || !scanPreview || scanPreview.status === 'applied'} className="bg-[#111111] text-white hover:bg-[#2a2a2a]">{isSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{scanPreview?.status === 'applied' ? '已应用' : '应用全部变化'}</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={editingMetricId !== null} onOpenChange={open => !open && setEditingMetricId(null)}>
                <DialogContent className="max-w-3xl border-manus-border bg-manus-secondary text-manus-text">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Sigma className="h-5 w-5 text-accent" />
                            编辑指标
                        </DialogTitle>
                    </DialogHeader>
                    {renderMetricForm(editingMetricForm, setEditingMetricForm, editMetricColumns, '保存修改', submitMetricEdit)}
                </DialogContent>
            </Dialog>
        </>
    )
}

