import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useToast } from '@/components/ui/toast'
import { extendConfigService } from '@/services/extendConfigService'
import type {
    SemanticAccessOptions,
    SemanticBusinessSuggestion,
    SemanticColumn,
    SemanticEvalCase,
    SemanticEvalRun,
    SemanticGovernanceCandidate,
    SemanticGovernanceEvidenceFact,
    SemanticGovernancePolicy,
    SemanticGovernanceRun,
    SemanticModelCounts,
    SemanticMetricForm,
    SemanticModelsPayload,
    SemanticPreviewErrorDetail,
    SemanticPreviewResult,
    SemanticRelationshipForm,
    SemanticQuestionReadiness,
    SemanticReadiness,
    SemanticRuntimeMode,
    SemanticScanRun,
    SemanticTable,
} from '@/types/extendConfig'

const EMPTY_PAYLOAD: SemanticModelsPayload = {
    datasource: null,
    tables: [],
    columns: [],
    metrics: [],
    relationships: [],
    business_suggestions: [],
    recent_runs: [],
    matching_diagnostics: [],
}

const EMPTY_ACCESS_OPTIONS: SemanticAccessOptions = {
    roles: [],
    users: [],
}

const EMPTY_MODEL_COUNTS: SemanticModelCounts = {
    tables: 0,
    queryable_tables: 0,
    columns: 0,
    queryable_columns: 0,
    metrics: 0,
    queryable_metrics: 0,
    relationships: 0,
    queryable_relationships: 0,
    stale_assets: 0,
    orphaned_assets: 0,
    recent_runs: 0,
    recent_success_runs: 0,
}

const ACTIVE_GOVERNANCE_RUN_STATUSES = ['pending', 'running', 'cancel_requested'] as const
const TERMINAL_GOVERNANCE_RUN_STATUSES = ['completed', 'partial', 'failed', 'cancelled'] as const

function summaryNumber(summary: Record<string, unknown>, key: string): number {
    const value = summary[key]
    return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function governanceRunDoneToast(run: SemanticGovernanceRun, openCandidateCount: number) {
    const generated = summaryNumber(run.summary, 'candidates_generated')
    const profiledTables = summaryNumber(run.summary, 'profiled_tables')
    const failedTables = summaryNumber(run.summary, 'failed_tables')
    const connectionError = typeof run.summary.connection_error === 'string' ? run.summary.connection_error : ''

    if (run.status === 'cancelled') {
        return {
            type: 'warning' as const,
            title: '治理候选生成已取消',
            description: '已保留取消前生成的证据和候选。',
        }
    }
    if (run.status === 'failed') {
        return {
            type: 'error' as const,
            title: '治理候选生成失败',
            description: run.error_message || connectionError || '请稍后重试，或检查业务数据库连接。',
        }
    }
    if (run.status === 'partial') {
        return {
            type: 'warning' as const,
            title: '治理候选已部分生成',
            description: `已刷新 ${generated} 条候选证据，当前待复核 ${openCandidateCount} 条；${failedTables} 张表画像失败。`,
        }
    }
    return {
        type: 'success' as const,
        title: openCandidateCount > 0 ? '治理候选已生成' : '未发现新的待复核候选',
        description: openCandidateCount > 0
            ? `已刷新 ${generated} 条候选证据，当前待复核 ${openCandidateCount} 条；已画像 ${profiledTables} 张表。`
            : `已刷新 ${generated} 条候选证据，暂未发现新的管理员待办；已画像 ${profiledTables} 张表。`,
    }
}

export function useSemanticModels(options: { accessOnly?: boolean } = {}) {
    const accessOnly = Boolean(options.accessOnly)
    const { toast } = useToast()
    const [data, setData] = useState<SemanticModelsPayload>(EMPTY_PAYLOAD)
    const [tableBusinessSuggestions, setTableBusinessSuggestions] = useState<SemanticBusinessSuggestion[]>([])
    const [modelCounts, setModelCounts] = useState<SemanticModelCounts>(EMPTY_MODEL_COUNTS)
    const [hasLoadedModels, setHasLoadedModels] = useState(false)
    const [accessOptions, setAccessOptions] = useState<SemanticAccessOptions>(EMPTY_ACCESS_OPTIONS)
    const [isLoading, setIsLoading] = useState(true)
    const [isSaving, setIsSaving] = useState(false)
    const [isPreviewing, setIsPreviewing] = useState(false)
    const [isRunningEvaluation, setIsRunningEvaluation] = useState(false)
    const [previewResult, setPreviewResult] = useState<SemanticPreviewResult | null>(null)
    const [previewError, setPreviewError] = useState<SemanticPreviewErrorDetail | null>(null)
    const [evalCases, setEvalCases] = useState<SemanticEvalCase[]>([])
    const [evalRuns, setEvalRuns] = useState<SemanticEvalRun[]>([])
    const [activeTableId, setActiveTableId] = useState<number | null>(null)
    const [readiness, setReadiness] = useState<SemanticReadiness | null>(null)
    const [scanPreview, setScanPreview] = useState<SemanticScanRun | null>(null)
    const [questionReadiness, setQuestionReadiness] = useState<SemanticQuestionReadiness | null>(null)
    const [governanceRuns, setGovernanceRuns] = useState<SemanticGovernanceRun[]>([])
    const [governanceCandidates, setGovernanceCandidates] = useState<SemanticGovernanceCandidate[]>([])
    const [governancePolicy, setGovernancePolicy] = useState<SemanticGovernancePolicy | null>(null)
    const [governanceEvidence, setGovernanceEvidence] = useState<SemanticGovernanceEvidenceFact[]>([])
    const activeGovernanceRunRef = useRef<number | null>(null)
    const notifiedGovernanceRunIdsRef = useRef<Set<number>>(new Set())
    const tableSuggestionRequestRef = useRef(0)

    const load = useCallback(async (options?: { showLoading?: boolean }) => {
        const showLoading = options?.showLoading ?? true
        if (showLoading) setIsLoading(true)
        try {
            const payload = accessOnly
                ? await extendConfigService.getSemanticAccessAssets()
                : await extendConfigService.getSemanticModels()
            setData({
                ...payload,
                matching_diagnostics: payload.matching_diagnostics || [],
            })
            setHasLoadedModels(true)
            setActiveTableId(current =>
                payload.tables.some(table => table.id === current)
                    ? current
                    : payload.tables[0]?.id ?? null
            )
        } catch (error) {
            toast({
                type: 'error',
                title: '语义模型加载失败',
                description: error instanceof Error ? error.message : '请稍后重试',
            })
        } finally {
            if (showLoading) setIsLoading(false)
        }
    }, [accessOnly, toast])

    const loadOverview = useCallback(async () => {
        setIsLoading(true)
        try {
            const overview = await extendConfigService.getSemanticModelOverview()
            setModelCounts(overview.counts)
            setData(current => ({ ...current, datasource: overview.datasource }))
        } catch (error) {
            toast({
                type: 'error',
                title: '语义模型概览加载失败',
                description: error instanceof Error ? error.message : '请稍后重试',
            })
        } finally {
            setIsLoading(false)
        }
    }, [toast])

    useEffect(() => {
        loadOverview()
    }, [loadOverview])

    const loadTableBusinessSuggestions = useCallback(async (tableId: number) => {
        const requestToken = ++tableSuggestionRequestRef.current
        try {
            const suggestions = await extendConfigService.getTableBusinessSuggestions(tableId)
            if (requestToken === tableSuggestionRequestRef.current) {
                setTableBusinessSuggestions(suggestions)
            }
        } catch (error) {
            if (requestToken === tableSuggestionRequestRef.current) {
                setTableBusinessSuggestions([])
            }
            toast({
                type: 'error',
                title: '当前表语义建议加载失败',
                description: error instanceof Error ? error.message : '请稍后重试',
            })
        }
    }, [toast])

    useEffect(() => {
        if (accessOnly || !activeTableId) {
            tableSuggestionRequestRef.current += 1
            setTableBusinessSuggestions([])
            return
        }
        setTableBusinessSuggestions([])
        void loadTableBusinessSuggestions(activeTableId)
    }, [accessOnly, activeTableId, loadTableBusinessSuggestions])

    const refreshSemanticData = useCallback(async (tableId?: number | null) => {
        await Promise.all([
            load({ showLoading: false }),
            tableId ? loadTableBusinessSuggestions(tableId) : Promise.resolve(),
        ])
    }, [load, loadTableBusinessSuggestions])

    const loadAccessOptions = useCallback(async () => {
        try {
            const payload = await extendConfigService.getSemanticAccessOptions()
            setAccessOptions(payload)
        } catch (error) {
            toast({
                type: 'error',
                title: '权限选项加载失败',
                description: error instanceof Error ? error.message : '请检查用户和角色权限',
            })
        }
    }, [toast])

    useEffect(() => {
        if (accessOnly) return
        loadAccessOptions()
    }, [accessOnly, loadAccessOptions])

    const loadEvaluation = useCallback(async () => {
        try {
            const [cases, runs] = await Promise.all([
                extendConfigService.getSemanticEvaluationCases(),
                extendConfigService.getSemanticEvaluationRuns(),
            ])
            setEvalCases(cases)
            setEvalRuns(runs)
        } catch (error) {
            toast({
                type: 'error',
                title: '评估回归加载失败',
                description: error instanceof Error ? error.message : '请稍后重试',
            })
        }
    }, [toast])

    useEffect(() => {
        if (accessOnly) return
        loadEvaluation()
    }, [accessOnly, loadEvaluation])

    const loadTrustFoundation = useCallback(async () => {
        try {
            const [nextReadiness, nextGovernanceRuns, nextCandidates, nextPolicy] = await Promise.all([
                extendConfigService.getSemanticReadiness(),
                extendConfigService.listSemanticGovernanceRuns(),
                extendConfigService.listSemanticGovernanceCandidates({ min_score: 0.65 }),
                extendConfigService.getSemanticGovernancePolicy(),
            ])
            setReadiness(nextReadiness)
            setGovernanceRuns(nextGovernanceRuns)
            setGovernanceCandidates(nextCandidates)
            setGovernancePolicy(nextPolicy)
        } catch (error) {
            toast({ type: 'error', title: '可信治理状态加载失败', description: error instanceof Error ? error.message : '请稍后重试' })
        }
    }, [toast])

    const cancelGovernanceRun = useCallback(async (runId: number) => {
        setIsSaving(true)
        try {
            const result = await extendConfigService.cancelSemanticGovernanceRun(runId)
            await loadTrustFoundation()
            toast({ type: 'success', title: '已取消治理候选生成' })
            return result
        } catch (error) {
            toast({ type: 'error', title: '取消治理候选生成失败', description: error instanceof Error ? error.message : '请稍后重试' })
            return null
        } finally {
            setIsSaving(false)
        }
    }, [loadTrustFoundation, toast])

    const retryGovernanceRun = useCallback(async (runId: number) => {
        setIsSaving(true)
        try {
            const result = await extendConfigService.retrySemanticGovernanceRun(runId)
            if (result.run_id) {
                activeGovernanceRunRef.current = result.run_id
            }
            await loadTrustFoundation()
            toast({ type: 'success', title: '已重新运行证据治理任务' })
            return result
        } catch (error) {
            toast({ type: 'error', title: '重试治理候选生成失败', description: error instanceof Error ? error.message : '请稍后重试' })
            return null
        } finally {
            setIsSaving(false)
        }
    }, [loadTrustFoundation, toast])

    useEffect(() => {
        const latestRun = governanceRuns[0]
        if (!latestRun || !ACTIVE_GOVERNANCE_RUN_STATUSES.includes(latestRun.status as typeof ACTIVE_GOVERNANCE_RUN_STATUSES[number])) return
        activeGovernanceRunRef.current = latestRun.run_id
        const timer = window.setInterval(() => {
            void loadTrustFoundation()
        }, 3000)
        return () => window.clearInterval(timer)
    }, [governanceRuns, loadTrustFoundation])

    useEffect(() => {
        const latestRun = governanceRuns[0]
        if (!latestRun || !TERMINAL_GOVERNANCE_RUN_STATUSES.includes(latestRun.status as typeof TERMINAL_GOVERNANCE_RUN_STATUSES[number])) return
        if (activeGovernanceRunRef.current !== latestRun.run_id) return
        if (notifiedGovernanceRunIdsRef.current.has(latestRun.run_id)) return

        notifiedGovernanceRunIdsRef.current.add(latestRun.run_id)
        activeGovernanceRunRef.current = null
        toast(governanceRunDoneToast(latestRun, governanceCandidates.length))
    }, [governanceCandidates.length, governanceRuns, toast])

    const loadGovernanceEvidence = useCallback(async (objectType: string, objectId: number) => {
        try {
            const evidence = await extendConfigService.getSemanticGovernanceEvidence(objectType, objectId)
            setGovernanceEvidence(evidence)
            return evidence
        } catch (error) {
            toast({ type: 'error', title: '证据加载失败', description: error instanceof Error ? error.message : '请稍后重试' })
            return []
        }
    }, [toast])

    const acceptGovernanceCandidate = useCallback(async (candidateId: number, editedPatch?: Record<string, unknown>, reason?: string) => {
        setIsSaving(true)
        try {
            await extendConfigService.acceptSemanticGovernanceCandidate(candidateId, editedPatch, reason)
            await Promise.all([load({ showLoading: false }), loadTrustFoundation()])
            toast({ type: 'success', title: '候选已接受', description: '该资产已转为人工管理，后续自动治理不会覆盖。' })
            return true
        } catch (error) {
            toast({ type: 'error', title: '接受候选失败', description: error instanceof Error ? error.message : '请刷新后重试' })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [load, loadTrustFoundation, toast])

    const rejectGovernanceCandidate = useCallback(async (candidateId: number, category: string, reason: string) => {
        setIsSaving(true)
        try {
            await extendConfigService.rejectSemanticGovernanceCandidate(candidateId, category, reason)
            await loadTrustFoundation()
            toast({ type: 'success', title: '候选已拒绝', description: '拒绝原因已沉淀为长期人工反馈证据。' })
            return true
        } catch (error) {
            toast({ type: 'error', title: '拒绝候选失败', description: error instanceof Error ? error.message : '请刷新后重试' })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [loadTrustFoundation, toast])

    const rollbackGovernanceCandidate = useCallback(async (candidateId: number) => {
        setIsSaving(true)
        try {
            await extendConfigService.rollbackSemanticGovernanceCandidate(candidateId)
            await Promise.all([load({ showLoading: false }), loadTrustFoundation()])
            toast({ type: 'success', title: '自动变更已回滚' })
            return true
        } catch (error) {
            toast({ type: 'error', title: '回滚失败', description: error instanceof Error ? error.message : '对象可能已被人工修改' })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [load, loadTrustFoundation, toast])

    const batchGovernanceCandidates = useCallback(async (ids: number[], action: 'accept' | 'reject', reason = '', category = 'not_relevant') => {
        if (!ids.length) return null
        setIsSaving(true)
        try {
            const result = await extendConfigService.batchSemanticGovernanceCandidates(ids, action, { reason, category })
            await Promise.all([load({ showLoading: false }), loadTrustFoundation()])
            toast({
                type: result.failed.length ? 'warning' : 'success',
                title: `已处理 ${result.succeeded.length} 条候选`,
                description: result.failed.length ? `${result.failed.length} 条因状态变化或冲突未处理。` : undefined,
            })
            return result
        } catch (error) {
            toast({ type: 'error', title: '批量处理失败', description: error instanceof Error ? error.message : '请刷新后重试' })
            return null
        } finally {
            setIsSaving(false)
        }
    }, [load, loadTrustFoundation, toast])

    const setGovernanceObserveOnly = useCallback(async (observeOnly: boolean) => {
        setIsSaving(true)
        try {
            const policy = await extendConfigService.updateSemanticGovernancePolicy({ observe_only: observeOnly })
            setGovernancePolicy(policy)
            await loadTrustFoundation()
            toast({ type: 'success', title: observeOnly ? '已切换到观察模式' : '已开放安全自动治理' })
            return true
        } catch (error) {
            toast({ type: 'error', title: '策略更新失败', description: error instanceof Error ? error.message : '请稍后重试' })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [loadTrustFoundation, toast])

    useEffect(() => {
        if (accessOnly) return
        loadTrustFoundation()
    }, [accessOnly, loadTrustFoundation])

    const columnsByTable = useMemo(() => {
        const grouped: Record<number, SemanticColumn[]> = {}
        data.columns.forEach(column => {
            grouped[column.table_id] = grouped[column.table_id] || []
            grouped[column.table_id].push(column)
        })
        Object.values(grouped).forEach(columns => {
            columns.sort((a, b) => a.ordinal_position - b.ordinal_position)
        })
        return grouped
    }, [data.columns])

    const activeTable = useMemo(
        () => data.tables.find(table => table.id === activeTableId) || null,
        [activeTableId, data.tables]
    )

    const updateModel = useCallback(async (modelType: string, id: number, patch: Record<string, unknown>) => {
        setIsSaving(true)
        try {
            await extendConfigService.updateSemanticModel(modelType, id, patch)
            await refreshSemanticData(activeTableId)
            toast({ type: 'success', title: '语义模型已更新' })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '更新失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [activeTableId, refreshSemanticData, toast])

    const scan = useCallback(async () => {
        setIsSaving(true)
        try {
            const result = await extendConfigService.previewSemanticScan()
            setScanPreview(result)
            await loadTrustFoundation()
            toast({
                type: 'success',
                title: 'Schema Diff 已生成',
                description: `${result.summary.total || 0} 项变化，其中 ${result.summary.blocking || 0} 项需要重点关注。`,
            })
            return result
        } catch (error) {
            toast({
                type: 'error',
                title: '扫描失败',
                description: error instanceof Error ? error.message : '请检查数据库连接',
            })
            return null
        } finally {
            setIsSaving(false)
        }
    }, [loadTrustFoundation, toast])

    const applyScan = useCallback(async (scanId: number) => {
        setIsSaving(true)
        try {
            const result = await extendConfigService.applySemanticScan(scanId)
            setScanPreview(result)
            await Promise.all([load({ showLoading: false }), loadTrustFoundation()])
            toast({ type: 'success', title: 'Schema 变化已原子应用', description: '人工治理资产未被覆盖。' })
            return true
        } catch (error) {
            toast({ type: 'error', title: '应用失败', description: error instanceof Error ? error.message : '请重新扫描' })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [load, loadTrustFoundation, toast])

    const setRuntimeMode = useCallback(async (mode: SemanticRuntimeMode) => {
        setIsSaving(true)
        try {
            const result = await extendConfigService.setSemanticRuntimeMode(mode)
            await Promise.all([load({ showLoading: false }), loadTrustFoundation()])
            toast({ type: 'success', title: `运行模式已切换为 ${mode}`, description: result.warning || undefined })
            return true
        } catch (error) {
            toast({ type: 'error', title: '模式切换失败', description: error instanceof Error ? error.message : '操作失败' })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [load, loadTrustFoundation, toast])

    const checkQuestionReadiness = useCallback(async (question: string) => {
        const text = question.trim()
        if (!text) return null
        setIsPreviewing(true)
        try {
            const result = await extendConfigService.checkSemanticQuestion(text)
            setQuestionReadiness(result)
            return result
        } catch (error) {
            toast({ type: 'error', title: '问题诊断失败', description: error instanceof Error ? error.message : '操作失败' })
            return null
        } finally {
            setIsPreviewing(false)
        }
    }, [toast])

    const createMetric = useCallback(async (form: SemanticMetricForm) => {
        setIsSaving(true)
        try {
            await extendConfigService.createSemanticMetric(form)
            await load()
            toast({ type: 'success', title: '指标已创建' })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '创建指标失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [load, toast])

    const createRelationship = useCallback(async (form: SemanticRelationshipForm) => {
        setIsSaving(true)
        try {
            await extendConfigService.createSemanticRelationship(form)
            await load()
            toast({ type: 'success', title: '表关系已创建' })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '创建关系失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [load, toast])

    const previewSemanticQuery = useCallback(async (question: string) => {
        const text = question.trim()
        if (!text) return
        setIsPreviewing(true)
        setPreviewResult(null)
        setPreviewError(null)
        try {
            const result = await extendConfigService.previewSemanticQuery(text)
            setPreviewResult(result)
            await load({ showLoading: false })
            toast({ type: 'success', title: '语义预览完成', description: `返回 ${result.row_count} 行` })
        } catch (error) {
            const detail = (error as Error & { detail?: SemanticPreviewErrorDetail }).detail
            setPreviewError(detail || { message: error instanceof Error ? error.message : '预览失败' })
            toast({
                type: 'error',
                title: '语义预览失败',
                description: detail?.message || (error instanceof Error ? error.message : '操作失败'),
            })
        } finally {
            setIsPreviewing(false)
        }
    }, [load, toast])

    const clearPreview = useCallback(() => {
        setPreviewResult(null)
        setPreviewError(null)
    }, [])

    const runEvaluation = useCallback(async (caseIds?: string[]) => {
        setIsRunningEvaluation(true)
        try {
            const result = await extendConfigService.runSemanticEvaluation(caseIds)
            setEvalRuns(current => {
                const merged = [...result.runs, ...current]
                const seen = new Set<number>()
                return merged.filter(run => {
                    if (seen.has(run.id)) return false
                    seen.add(run.id)
                    return true
                })
            })
            await loadEvaluation()
            toast({
                type: 'success',
                title: caseIds?.length ? '单题评估完成' : '全量评估完成',
                description: `本次生成 ${result.runs.length} 条评估记录`,
            })
            return result.runs
        } catch (error) {
            toast({
                type: 'error',
                title: '评估运行失败',
                description: error instanceof Error ? error.message : '请检查数据库和 LLM 配置',
            })
            return []
        } finally {
            setIsRunningEvaluation(false)
        }
    }, [loadEvaluation, toast])

    const generateBusinessSuggestions = useCallback(async (scope: 'datasource' | 'table', tableId?: number | null, force = false) => {
        setIsSaving(true)
        try {
            const result = await extendConfigService.generateBusinessSuggestions({
                scope,
                table_id: tableId,
                force,
                use_llm: true,
            })
            await refreshSemanticData(tableId || activeTableId)
            toast({
                type: 'success',
                title: '语义建议已生成',
                description: `生成 ${result.suggestion_count || 0} 条业务语义建议`,
            })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '生成建议失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [activeTableId, refreshSemanticData, toast])

    const acceptTableBusinessSuggestions = useCallback(async (tableId: number) => {
        setIsSaving(true)
        try {
            const result = await extendConfigService.acceptTableBusinessSuggestions(tableId)
            await refreshSemanticData(tableId)
            toast({ type: 'success', title: '已接受本表语义建议', description: `已应用 ${result.accepted_count || 0} 条建议` })
            return true
        } catch (error) {
            toast({ type: 'error', title: '按表接受建议失败', description: error instanceof Error ? error.message : '操作失败' })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [refreshSemanticData, toast])

    const generateMetricSuggestions = useCallback(async () => {
        setIsSaving(true)
        try {
            const result = await extendConfigService.generateMetricSuggestions()
            await load({ showLoading: false })
            toast({ type: 'success', title: '指标建议已生成', description: `生成 ${result.suggestion_count || 0} 条指标建议` })
            return true
        } catch (error) {
            toast({ type: 'error', title: '生成指标建议失败', description: error instanceof Error ? error.message : '操作失败' })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [load, toast])

    const generateRelationshipSuggestions = useCallback(async () => {
        setIsSaving(true)
        try {
            const result = await extendConfigService.generateRelationshipSuggestions()
            await load({ showLoading: false })
            toast({ type: 'success', title: '关系建议已生成', description: `生成 ${result.suggestion_count || 0} 条关系建议` })
            return true
        } catch (error) {
            toast({ type: 'error', title: '生成关系建议失败', description: error instanceof Error ? error.message : '操作失败' })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [load, toast])

    const updateBusinessSuggestion = useCallback(async (id: number, patch: Record<string, unknown>) => {
        setIsSaving(true)
        try {
            await extendConfigService.updateBusinessSuggestion(id, patch)
            await refreshSemanticData(activeTableId)
            toast({ type: 'success', title: '语义建议已更新' })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '更新建议失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [activeTableId, refreshSemanticData, toast])

    const acceptBusinessSuggestion = useCallback(async (id: number) => {
        setIsSaving(true)
        try {
            await extendConfigService.acceptBusinessSuggestion(id)
            await refreshSemanticData(activeTableId)
            toast({ type: 'success', title: '语义建议已接受' })
        } catch (error) {
            toast({
                type: 'error',
                title: '接受建议失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
        } finally {
            setIsSaving(false)
        }
    }, [activeTableId, refreshSemanticData, toast])

    const confirmBusinessSuggestion = useCallback(async (id: number, patch?: Record<string, unknown>) => {
        setIsSaving(true)
        try {
            if (patch && Object.keys(patch).length > 0) {
                await extendConfigService.updateBusinessSuggestion(id, patch)
            }
            await extendConfigService.acceptBusinessSuggestion(id)
            await refreshSemanticData(activeTableId)
            toast({ type: 'success', title: '语义建议已确认' })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '确认建议失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [activeTableId, refreshSemanticData, toast])

    const batchAcceptBusinessSuggestions = useCallback(async (ids: number[]) => {
        if (!ids.length) return
        setIsSaving(true)
        try {
            await extendConfigService.batchAcceptBusinessSuggestions(ids)
            await refreshSemanticData(activeTableId)
            toast({ type: 'success', title: `已接受 ${ids.length} 条语义建议` })
        } catch (error) {
            toast({
                type: 'error',
                title: '批量接受失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
        } finally {
            setIsSaving(false)
        }
    }, [activeTableId, refreshSemanticData, toast])

    const rejectBusinessSuggestion = useCallback(async (id: number) => {
        setIsSaving(true)
        try {
            await extendConfigService.rejectBusinessSuggestion(id)
            await refreshSemanticData(activeTableId)
            toast({ type: 'success', title: '语义建议已忽略' })
        } catch (error) {
            toast({
                type: 'error',
                title: '忽略建议失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
        } finally {
            setIsSaving(false)
        }
    }, [activeTableId, refreshSemanticData, toast])

    const confirmTable = useCallback((table: SemanticTable) => {
        updateModel('tables', table.id, { status: 'confirmed', is_queryable: true })
    }, [updateModel])

    const confirmColumn = useCallback((column: SemanticColumn) => {
        updateModel('columns', column.id, { status: 'confirmed', is_queryable: true })
    }, [updateModel])

    return {
        data,
        tableBusinessSuggestions,
        modelCounts,
        hasLoadedModels,
        accessOptions,
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
        governanceRuns,
        governanceCandidates,
        governancePolicy,
        governanceEvidence,
        loadTrustFoundation,
        load,
        loadEvaluation,
        loadAccessOptions,
        scan,
        applyScan,
        setRuntimeMode,
        checkQuestionReadiness,
        cancelGovernanceRun,
        retryGovernanceRun,
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
        confirmTable,
        confirmColumn,
        createMetric,
        createRelationship,
        generateBusinessSuggestions,
        acceptTableBusinessSuggestions,
        generateMetricSuggestions,
        generateRelationshipSuggestions,
        updateBusinessSuggestion,
        acceptBusinessSuggestion,
        confirmBusinessSuggestion,
        batchAcceptBusinessSuggestions,
        rejectBusinessSuggestion,
    }
}
