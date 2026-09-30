import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  ArrowLeft,
  BriefcaseBusiness,
  Building2,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Eye,
  EyeOff,
  History,
  Inbox,
  Loader2,
  Menu,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  UserRound,
  Users,
  Wand2,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { authorizationRequest } from '@/features/authorization/api'
import {
  buildOrgTree,
  type OrgTreeNode,
  type OrgUnit,
} from '@/features/authorization/types'
import {
  SemanticAccessApiError,
  semanticAccessErrorMessage,
  semanticAccessRequest,
} from '@/features/semanticAccess/api'
import {
  semanticTargetKey,
  semanticTargetLabel,
  type SemanticAccessBinding,
  type SemanticAccessBootstrapCandidate,
  type SemanticAccessBootstrapRun,
  type SemanticAccessBootstrapSuggestions,
  type SemanticAccessBootstrapTarget,
  type SemanticAccessTargetPage,
  type SemanticAccessTargetSummary,
  type SemanticAccessTargetType,
  type SemanticEffectiveTableRule,
  type SemanticAccessVersion,
  type SemanticAccessSimilarSuggestionApplyResult,
  type SemanticAccessSimilarSuggestionPreview,
  type SemanticOwnershipMapping,
} from '@/features/semanticAccess/types'
import type {
  ConditionRule,
  SemanticAccessRowScope,
  SemanticAccessTableRule,
  SemanticColumn,
  SemanticMetric,
  SemanticTable,
} from '@/types/extendConfig'
import {
  bootstrapEffectiveRules,
  hasSemanticAccessPermissionChanges,
  persistedSemanticAccessTableRule,
  semanticAccessDecisionOriginLabel,
} from './semanticAccessPolicyDraftUtils'
import {
  accessLevelLabel,
  authorizationNeedsReview,
  evidenceAccessLevel,
  evidenceBaselineAccess,
  evidenceFieldDecision,
  evidenceRequiresIndividualReview,
  isBulkReviewableFieldGrant,
  isBulkReviewableTableGrant,
  isLowEvidenceConfidence,
  isManuallyApproved,
  summarizeEvidenceConfirmations,
  shouldPollEvidenceSet,
  type EvidenceConfirmationSummary,
  type EvidenceAccessLevel,
  type EvidenceFieldDecision,
} from './semanticAccessEvidenceV2Utils'

type TargetTab = 'org_unit' | 'position' | 'user'
type Area = 'baseline' | 'organization' | 'unassigned'
type Mapping = SemanticOwnershipMapping

type SimilarSuggestionContext = {
  datasource_id: number
  org_unit_id: number
  source_table_id: number
  source_before_rule: SemanticAccessTableRule | null
  expected_binding_revision: number
  bootstrap_run_id: number | null
  expected_bootstrap_revision: number | null
}

type Suggestion = {
  draft_patch?: { tables?: SemanticAccessTableRule[] }
  validation?: {
    blockers?: Array<{ code: string; message: string }>
    warnings?: Array<{ code: string; message: string }>
  }
}

type EvidenceReadiness = {
  ready: boolean
  access_bootstrap_required: boolean
  blockers: Array<{ code: string; message?: string; items?: Array<{ id: number; name: string; status?: string }> }>
  business_context: {
    exists: boolean
    revision: number
    quality: {
      level: 'missing' | 'partial' | 'sufficient'
      required_completed: number
      required_total: number
      missing_field_labels: string[]
    }
  }
  department_profiles: { total: number; confirmed: number; missing: unknown[] }
  business_semantics: {
    tables_total: number
    tables_unconfirmed: unknown[]
    columns_total: number
    columns_unconfirmed: unknown[]
  }
}

type EvidenceSetSummary = {
  id: number
  datasource_id: number
  version: number
  model_version: number
  revision: number
  status: string
  review_progress: {
    public_access?: { reviewed: number; total: number }
    table_grants?: { reviewed: number; total: number }
    field_grants?: { reviewed: number; total: number }
    auto_safe_count?: number
  }
  review_summary?: EvidenceSetSummary['review_progress']
  blockers: Array<{ code: string; asset_id?: number; relation_id?: number; table_id?: number }>
  error_message?: string
}

type EvidenceAsset = {
  id: number
  asset_type: 'table' | 'column'
  asset_id: number
  table_id: number
  baseline_access: 'workspace_visible' | 'controlled' | null
  requires_individual_review: boolean
  // Legacy v1 response field retained during rollout.
  access_class: 'workspace_public' | 'department_scoped' | 'restricted'
  is_sensitive: boolean
  review_status: string
  reason?: string
}

type EvidenceRelation = {
  id: number
  asset_type: 'table' | 'column'
  asset_id: number
  table_id: number
  org_unit_id: number
  business_role: 'owner' | 'producer' | 'required_consumer' | 'conditional_consumer' | 'none'
  access_level: 'hidden' | 'visible' | 'partial' | null
  field_decision: 'visible' | 'hidden' | null
  // Legacy v1 response fields retained during rollout.
  relation_role: 'owner' | 'producer' | 'required_consumer' | 'conditional_consumer' | 'none'
  access_decision: 'inherit' | 'visible' | 'hidden'
  row_scope: SemanticAccessRowScope
  reason: string
  confidence?: number
  review_status: string
  override_reason?: string
}

type EvidencePayload = {
  set: EvidenceSetSummary | null
  readiness?: EvidenceReadiness
  assets?: EvidenceAsset[]
  relations?: EvidenceRelation[]
}

type EvidenceGenerationRun = {
  run_id: number
  datasource_id: number
  evidence_set_id: number | null
  status: string
  stage: string
  progress: number
  error_message?: string | null
}

type BootstrapTargetResponse = {
  run: SemanticAccessBootstrapRun
  target: SemanticAccessBootstrapTarget
  active_definition?: { name?: string; tables: SemanticAccessTableRule[] }
  effective_tables?: SemanticEffectiveTableRule[]
}

type BootstrapTablePublishResponse = BootstrapTargetResponse & {
  active_definition: { name?: string; tables: SemanticAccessTableRule[] }
  binding_id: number
  binding_revision: number
  version_id: number
  version: number
  audit_id?: number
}

type AccessMatrixCell = {
  department_id: number
  table_id: number
  decision: 'visible' | 'partial' | 'hidden'
  reason: 'grant' | 'explicit_hidden' | 'default_deny'
  source: 'direct' | 'inherited' | 'baseline' | 'default_deny'
  source_target_id: string | null
  pending_review: boolean
  pending_decision?: 'visible' | 'partial' | 'hidden' | null
  pending_source?: 'direct' | 'inherited' | 'baseline' | null
  confidence?: number | null
}

type AccessMatrixPayload = {
  datasource_id: number
  evidence_set_id: number | null
  tables: Array<{ id: number; business_name: string; physical_name: string }>
  departments: Array<{
    id: number
    name: string
    parent_id: number | null
    level: number
    path: string
  }>
  cells: AccessMatrixCell[]
}

type InheritedEvidenceReview = {
  confirmation: EvidenceConfirmationSummary
  sourceLabel: string
}

type Props = {
  datasourceId: number
  datasourceName?: string
  tables: SemanticTable[]
  columns: SemanticColumn[]
  metrics: SemanticMetric[]
  onClose: () => void
}

const EMPTY_BINDING = (
  datasourceId: number,
  target: SemanticAccessTargetSummary,
): SemanticAccessBinding => ({
  id: null,
  binding_id: null,
  datasource_id: datasourceId,
  target_type: target.target_type,
  target_id: target.target_id,
  include_descendants: target.target_type === 'org_unit',
  active_version_id: null,
  active_version: null,
  revision: 0,
  status: true,
  affected_user_count: target.affected_user_count,
})

export function SemanticAccessPolicyWorkspaceV2({
  datasourceId,
  datasourceName,
  tables,
  columns,
  metrics,
  onClose,
}: Props) {
  const permissions = useAuthStore(state => state.user?.permissions || [])
  const canManage = permissions.includes('*') || permissions.includes('semantic_access:manage')

  const [organizations, setOrganizations] = useState<OrgUnit[]>([])
  const [orgTargets, setOrgTargets] = useState<SemanticAccessTargetSummary[]>([])
  const [baselineTarget, setBaselineTarget] = useState<SemanticAccessTargetSummary | null>(null)
  const [area, setArea] = useState<Area>('baseline')
  const [selectedOrgId, setSelectedOrgId] = useState<number | null>(null)
  const [expandedOrgIds, setExpandedOrgIds] = useState<Set<number>>(new Set())
  const [targetTab, setTargetTab] = useState<TargetTab>('org_unit')
  const [targetOptions, setTargetOptions] = useState<SemanticAccessTargetSummary[]>([])
  const [selectedTarget, setSelectedTarget] = useState<SemanticAccessTargetSummary | null>(null)
  const [targetSearch, setTargetSearch] = useState('')
  const [canManageWorkspace, setCanManageWorkspace] = useState(false)

  const [binding, setBinding] = useState<SemanticAccessBinding | null>(null)
  const [versions, setVersions] = useState<SemanticAccessVersion[]>([])
  const [mappings, setMappings] = useState<Record<number, Mapping>>({})
  const [rules, setRules] = useState<SemanticAccessTableRule[]>([])
  const [activeRules, setActiveRules] = useState<SemanticAccessTableRule[]>([])
  const [effectiveRules, setEffectiveRules] = useState<SemanticEffectiveTableRule[]>([])
  const enabledTables = useMemo(
    () => tables.filter(item => (
      item.status === 'confirmed'
      && item.is_queryable
      && (item.sync_state || 'current') === 'current'
    )),
    [tables],
  )
  const enabledTableIds = useMemo(
    () => new Set(enabledTables.map(item => item.id)),
    [enabledTables],
  )
  const enabledColumns = useMemo(
    () => columns.filter(item => (
      enabledTableIds.has(item.table_id)
      && item.status === 'confirmed'
      && item.is_queryable
      && (item.sync_state || 'current') === 'current'
    )),
    [columns, enabledTableIds],
  )
  const enabledMetrics = useMemo(
    () => metrics.filter(item => (
      enabledTableIds.has(item.table_id)
      && item.status === 'confirmed'
      && item.is_queryable
      && (item.sync_state || 'current') === 'current'
    )),
    [enabledTableIds, metrics],
  )
  const [selectedTableId, setSelectedTableId] = useState(enabledTables[0]?.id || 0)

  const [sourceText, setSourceText] = useState('')
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null)
  const [toolDrawerOpen, setToolDrawerOpen] = useState(false)
  const [mobileTreeOpen, setMobileTreeOpen] = useState(false)
  const [unsavedOpen, setUnsavedOpen] = useState(false)
  const [similarSuggestion, setSimilarSuggestion] = useState<SemanticAccessSimilarSuggestionPreview | null>(null)
  const [similarSuggestionContext, setSimilarSuggestionContext] = useState<SimilarSuggestionContext | null>(null)
  const [selectedSimilarSuggestionIds, setSelectedSimilarSuggestionIds] = useState<string[]>([])
  const [similarSuggestionLoading, setSimilarSuggestionLoading] = useState(false)
  const suggestionRequestToken = useRef(0)
  const [evidence, setEvidence] = useState<EvidencePayload>({ set: null })
  const [evidenceReadiness, setEvidenceReadiness] = useState<EvidenceReadiness | null>(null)
  const [bootstrapRun, setBootstrapRun] = useState<SemanticAccessBootstrapRun | null>(null)
  const [bootstrapSuggestions, setBootstrapSuggestions] = useState<SemanticAccessBootstrapSuggestions | null>(null)
  const [draftTarget, setDraftTarget] = useState<SemanticAccessBootstrapTarget | null>(null)
  const [evidenceFilter, setEvidenceFilter] = useState<'all' | 'pending' | 'restricted' | 'sensitive' | 'mapping'>('all')
  const [departmentConfirmOpen, setDepartmentConfirmOpen] = useState(false)
  const [generationRun, setGenerationRun] = useState<EvidenceGenerationRun | null>(null)
  const [matrixOpen, setMatrixOpen] = useState(false)
  const [matrixLoading, setMatrixLoading] = useState(false)
  const [matrix, setMatrix] = useState<AccessMatrixPayload | null>(null)
  const pendingAction = useRef<(() => void) | null>(null)

  const [loading, setLoading] = useState(true)
  const [targetLoading, setTargetLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const orgTree = useMemo(() => buildOrgTree(organizations), [organizations])
  const selectedOrg = useMemo(
    () => organizations.find(item => item.id === selectedOrgId) || null,
    [organizations, selectedOrgId],
  )
  const orgTargetById = useMemo(
    () => new Map(orgTargets.map(item => [Number(item.target_id), item])),
    [orgTargets],
  )
  const currentTable = enabledTables.find(item => item.id === selectedTableId) || enabledTables[0]
  const currentRule = rules.find(item => item.table_id === currentTable?.id)
  const currentEffectiveRule = effectiveRules.find(item => item.table_id === currentTable?.id)
  const tableColumns = enabledColumns.filter(item => item.table_id === currentTable?.id)
  const tableMetrics = enabledMetrics.filter(item => item.table_id === currentTable?.id)
  const currentMapping = currentTable
    ? mappings[currentTable.id] || {
        table_id: currentTable.id,
        org_column_id: null,
        org_value_kind: 'id' as const,
        user_column_id: null,
        user_value_kind: 'id' as const,
      }
    : null
  const savableRules = useMemo(() => {
    const columnIds = new Set(enabledColumns.map(item => item.id))
    const metricIds = new Set(enabledMetrics.map(item => item.id))
    return rules
      .filter(item => enabledTableIds.has(item.table_id))
      .map(item => {
        const normalized = {
          ...item,
          hidden_column_ids: (item.hidden_column_ids || []).filter(id => columnIds.has(id)),
          hidden_metric_ids: (item.hidden_metric_ids || []).filter(id => metricIds.has(id)),
        }
        if (normalized.decision !== 'visible') delete normalized.row_scope
        return normalized
      })
  }, [enabledColumns, enabledMetrics, enabledTableIds, rules])
  const isDirty = useMemo(
    () => hasSemanticAccessPermissionChanges(rules, activeRules),
    [activeRules, rules],
  )
  const currentTableDirty = useMemo(() => {
    if (!currentTable) return false
    return hasSemanticAccessPermissionChanges(
      rules.filter(item => item.table_id === currentTable.id),
      activeRules.filter(item => item.table_id === currentTable.id),
    )
  }, [
    activeRules,
    currentTable,
    rules,
  ])
  const initialReviewMode = Boolean(
    bootstrapRun?.status === 'review_ready'
    && evidence.set
    && ['review_ready', 'publish_failed'].includes(evidence.set.status),
  )
  const bootstrapDraftOrgIds = useMemo(
    () => new Set((bootstrapSuggestions?.targets || [])
      .filter(target => target.target_type === 'org_unit')
      .map(target => Number(target.target_id))),
    [bootstrapSuggestions],
  )
  const evidenceConfirmationTarget = useMemo(() => {
    if (!initialReviewMode || !evidence.set || !selectedTarget) return null
    if (area === 'baseline' && selectedTarget.target_type === 'baseline') {
      return { type: 'baseline' as const, id: '*' }
    }
    if (
      area === 'organization'
      && selectedTarget.target_type === 'org_unit'
      && selectedOrg?.level === 0
    ) {
      return { type: 'org_unit' as const, id: String(selectedOrg.id) }
    }
    return null
  }, [area, evidence.set, initialReviewMode, selectedOrg, selectedTarget])
  const evidenceConfirmationsByTable = useMemo(() => {
    if (!evidenceConfirmationTarget) return new Map<number, EvidenceConfirmationSummary>()
    const sensitiveColumnIds = new Set(
      enabledColumns.filter(column => column.is_sensitive).map(column => column.id),
    )
    return new Map(enabledTables.map(table => [
      table.id,
      summarizeEvidenceConfirmations(
        evidence.assets || [],
        evidence.relations || [],
        evidenceConfirmationTarget.type,
        evidenceConfirmationTarget.id,
        sensitiveColumnIds,
        table.id,
      ),
    ]))
  }, [
    enabledColumns,
    enabledTables,
    evidence.assets,
    evidence.relations,
    evidenceConfirmationTarget,
  ])
  const evidenceConfirmation = currentTable
    ? evidenceConfirmationsByTable.get(currentTable.id) || null
    : null
  const inheritedEvidenceReviewsByTable = useMemo(() => {
    const result = new Map<number, InheritedEvidenceReview>()
    if (!initialReviewMode || !evidence.set) return result
    const sensitiveColumnIds = new Set(
      enabledColumns.filter(column => column.is_sensitive).map(column => column.id),
    )
    for (const effectiveRule of effectiveRules) {
      if (effectiveRule.is_direct_override) continue
      const source = effectiveRule.sources.find(item => (
        item.target_type === 'org_unit' || item.target_type === 'baseline'
      ))
      if (!source) continue
      result.set(effectiveRule.table_id, {
        confirmation: summarizeEvidenceConfirmations(
          evidence.assets || [],
          evidence.relations || [],
          source.target_type as 'baseline' | 'org_unit',
          source.target_id,
          sensitiveColumnIds,
          effectiveRule.table_id,
        ),
        sourceLabel: source.label,
      })
    }
    return result
  }, [
    effectiveRules,
    enabledColumns,
    evidence.assets,
    evidence.relations,
    evidence.set,
    initialReviewMode,
  ])
  const evidenceTableAssets = useMemo(
    () => (evidence.assets || []).filter(asset => (
      asset.asset_type == null || asset.asset_type === 'table'
    )),
    [evidence.assets],
  )
  const highRiskTableIds = useMemo(() => new Set([
    ...evidenceTableAssets
      .filter(asset => (
        asset.requires_individual_review
        || asset.access_class === 'restricted'
      ))
      .map(asset => asset.table_id),
  ]), [evidenceTableAssets])
  const sensitiveTableIds = useMemo(() => new Set([
    ...enabledTables.filter(table => table.is_sensitive).map(table => table.id),
    ...evidenceTableAssets.filter(asset => asset.is_sensitive).map(asset => asset.table_id),
    ...enabledColumns.filter(column => column.is_sensitive).map(column => column.table_id),
  ]), [enabledColumns, enabledTables, evidenceTableAssets])
  const pendingRiskTables = useMemo(() => (
    (evidenceConfirmation?.pendingRiskTableIds || []).map(tableId => ({
      tableId,
      table: enabledTables.find(item => item.id === tableId),
      highRisk: evidenceConfirmation?.pendingHighRiskTableIds.includes(tableId) || false,
      sensitive: evidenceConfirmation?.pendingSensitiveTableIds.includes(tableId) || false,
    }))
  ), [enabledTables, evidenceConfirmation])

  const apiPath = useCallback((path: string) => `/config/semantic${path}`, [])

  const loadMappings = useCallback(async () => {
    const rows = await semanticAccessRequest<Mapping[]>(
      apiPath(`/ownership-mappings?datasource_id=${datasourceId}`),
    )
    setMappings(Object.fromEntries(rows.map(item => [item.table_id, item])))
  }, [apiPath, datasourceId])

  const loadEvidence = useCallback(async () => {
    const current = await semanticAccessRequest<EvidencePayload>(
      apiPath(`/access-evidence/sets/current?datasource_id=${datasourceId}`),
    )
    const readiness = current.readiness || await semanticAccessRequest<EvidenceReadiness>(
      apiPath(`/access-evidence/readiness?datasource_id=${datasourceId}`),
    )
    setEvidenceReadiness(readiness)
    setEvidence(current)
    if (
      canManage
      && current.set
      && ['review_ready', 'publish_failed'].includes(current.set.status)
    ) {
      const run = await semanticAccessRequest<SemanticAccessBootstrapRun>(
        apiPath(`/access-bootstrap/evidence-sets/${current.set.id}/materialize`),
        { method: 'POST' },
      )
      setBootstrapRun(run)
      const nextSuggestions = await semanticAccessRequest<SemanticAccessBootstrapSuggestions>(
        apiPath(`/access-bootstrap/runs/${run.run_id}/suggestions`),
      )
      setBootstrapSuggestions(nextSuggestions)
    } else if (!current.set || !['review_ready', 'publish_failed'].includes(current.set.status)) {
      setBootstrapRun(null)
      setBootstrapSuggestions(null)
      setDraftTarget(null)
    }
  }, [apiPath, canManage, datasourceId])

  const loadTargets = useCallback(async (
    type: SemanticAccessTargetType,
    orgUnitId?: number | null,
    search = '',
    unassigned = false,
  ) => {
    const params = new URLSearchParams({
      datasource_id: String(datasourceId),
      target_type: type,
      page_size: '500',
    })
    if (orgUnitId != null) params.set('org_unit_id', String(orgUnitId))
    if (search.trim()) params.set('search', search.trim())
    if (unassigned) params.set('unassigned', 'true')
    return semanticAccessRequest<SemanticAccessTargetPage>(
      apiPath(`/access-targets?${params.toString()}`),
    )
  }, [apiPath, datasourceId])

  const loadBase = useCallback(async () => {
    if (!datasourceId) return
    setLoading(true)
    setError('')
    try {
      const [orgRows, orgPage, baselinePage] = await Promise.all([
        authorizationRequest<OrgUnit[]>('/authorization/org-units'),
        loadTargets('org_unit'),
        loadTargets('baseline'),
        loadMappings(),
        loadEvidence(),
      ])
      setOrganizations(orgRows)
      setOrgTargets(orgPage.items)
      setBaselineTarget(baselinePage.items[0] || null)
      setCanManageWorkspace(baselinePage.can_manage_workspace)
      setExpandedOrgIds(current => current.size
        ? current
        : new Set(orgRows.filter(item => item.parent_id == null).map(item => item.id)))
      setSelectedOrgId(current => current && orgRows.some(item => item.id === current)
        ? current
        : orgRows[0]?.id ?? null)
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '问数权限数据加载失败'))
    } finally {
      setLoading(false)
    }
  }, [datasourceId, loadEvidence, loadMappings, loadTargets])

  useEffect(() => { void loadBase() }, [loadBase])

  useEffect(() => {
    if (!enabledTables.length) {
      setSelectedTableId(0)
      return
    }
    if (!enabledTableIds.has(selectedTableId)) {
      setSelectedTableId(enabledTables[0].id)
    }
  }, [enabledTableIds, enabledTables, selectedTableId])

  useEffect(() => {
    if (!shouldPollEvidenceSet(evidence.set?.status)) return
    const timer = window.setInterval(() => {
      void loadEvidence().catch(() => undefined)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [evidence.set, loadEvidence])

  useEffect(() => {
    if (!generationRun || !['queued', 'running'].includes(generationRun.status)) return
    const timer = window.setInterval(() => {
      void semanticAccessRequest<EvidenceGenerationRun>(
        apiPath(`/access-evidence/runs/${generationRun.run_id}`),
      ).then(run => {
        setGenerationRun(run)
        if (!['queued', 'running'].includes(run.status)) {
          void loadEvidence()
        }
      }).catch(() => undefined)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [apiPath, generationRun, loadEvidence])

  const openTarget = useCallback(async (
    target: SemanticAccessTargetSummary,
    bootstrapOverride?: SemanticAccessBootstrapRun | null,
  ) => {
    const activeBootstrapRun = bootstrapOverride === undefined ? bootstrapRun : bootstrapOverride
    setSelectedTarget(target)
    setTargetLoading(true)
    setError('')
    setNotice('')
    setSuggestion(null)
    setSourceText('')
    try {
      if (activeBootstrapRun?.status === 'review_ready') {
        const draft = await semanticAccessRequest<BootstrapTargetResponse>(
          apiPath(
            `/access-bootstrap/runs/${activeBootstrapRun.run_id}/targets/${target.target_type}/${encodeURIComponent(target.target_id)}`,
          ),
        )
        const nextRules = draft.target.definition?.tables || []
        setBootstrapRun(draft.run)
        setDraftTarget(draft.target)
        setBinding({
          ...EMPTY_BINDING(datasourceId, target),
          binding_id: draft.target.base_binding_id,
          id: draft.target.base_binding_id,
          revision: draft.target.base_revision,
          include_descendants: draft.target.include_descendants,
        })
        setRules(nextRules)
        setActiveRules(nextRules)
        setEffectiveRules(bootstrapEffectiveRules(
          draft.effective_tables,
          draft.active_definition?.tables || nextRules,
        ))
        if (draft.target.base_binding_id) {
          setVersions(await semanticAccessRequest<SemanticAccessVersion[]>(
            apiPath(`/access-bindings/${draft.target.base_binding_id}/versions`),
          ))
        } else {
          setVersions([])
        }
        return
      }
      const detail = await semanticAccessRequest<SemanticAccessBinding>(
        apiPath(`/access-policies/${target.target_type}/${encodeURIComponent(target.target_id)}?datasource_id=${datasourceId}`),
      )
      const nextRules = detail.active_version?.definition?.tables || []
      setBinding(detail)
      setRules(nextRules)
      setActiveRules(nextRules)
      setEffectiveRules(detail.effective_tables || [])
      if (detail.binding_id) {
        setVersions(await semanticAccessRequest<SemanticAccessVersion[]>(
          apiPath(`/access-bindings/${detail.binding_id}/versions`),
        ))
      } else {
        setVersions([])
      }
    } catch (cause) {
      setBinding(EMPTY_BINDING(datasourceId, target))
      setRules([])
      setActiveRules([])
      setEffectiveRules([])
      setVersions([])
      setError(semanticAccessErrorMessage(cause, '策略加载失败'))
    } finally {
      setTargetLoading(false)
    }
  }, [apiPath, bootstrapRun, datasourceId])

  const previewSimilarSuggestions = useCallback(async (
    sourceTableId: number,
    sourceBeforeRule: SemanticAccessTableRule | null,
    nextBindingRevision: number,
    nextBootstrapRun: SemanticAccessBootstrapRun | null,
  ) => {
    if (selectedTarget?.target_type !== 'org_unit') return
    const requestToken = ++suggestionRequestToken.current
    setSimilarSuggestionLoading(true)
    const context: SimilarSuggestionContext = {
      datasource_id: datasourceId,
      org_unit_id: Number(selectedTarget.target_id),
      source_table_id: sourceTableId,
      source_before_rule: sourceBeforeRule,
      expected_binding_revision: nextBindingRevision,
      bootstrap_run_id: nextBootstrapRun?.run_id ?? null,
      expected_bootstrap_revision: nextBootstrapRun?.revision ?? null,
    }
    try {
      const result = await semanticAccessRequest<SemanticAccessSimilarSuggestionPreview>(
        apiPath('/access-policy-similar-suggestions/preview'),
        { method: 'POST', body: JSON.stringify(context) },
      )
      if (requestToken !== suggestionRequestToken.current || result.candidates.length === 0) return
      setSimilarSuggestionContext(context)
      setSimilarSuggestion(result)
      setSelectedSimilarSuggestionIds(
        result.candidates.filter(item => item.default_selected).map(item => item.candidate_id),
      )
    } catch {
      // Suggestions are optional and must never turn a successful source save into an error.
    } finally {
      if (requestToken === suggestionRequestToken.current) {
        setSimilarSuggestionLoading(false)
      }
    }
  }, [apiPath, datasourceId, selectedTarget])

  const dismissSimilarSuggestions = useCallback(() => {
    suggestionRequestToken.current += 1
    setSimilarSuggestionLoading(false)
    setSimilarSuggestion(null)
    setSimilarSuggestionContext(null)
    setSelectedSimilarSuggestionIds([])
  }, [])

  const applySimilarSuggestions = useCallback(async () => {
    if (!similarSuggestion || !similarSuggestionContext || selectedSimilarSuggestionIds.length === 0) return
    setBusy('similar-suggestion-apply')
    try {
      const result = await semanticAccessRequest<SemanticAccessSimilarSuggestionApplyResult>(
        apiPath('/access-policy-similar-suggestions/apply'),
        {
          method: 'POST',
          body: JSON.stringify({
            ...similarSuggestionContext,
            batch_fingerprint: similarSuggestion.batch_fingerprint,
            candidate_ids: selectedSimilarSuggestionIds,
          }),
        },
      )
      dismissSimilarSuggestions()
      let nextRun = bootstrapRun
      if (bootstrapRun && result.bootstrap_revision != null) {
        nextRun = { ...bootstrapRun, revision: result.bootstrap_revision }
        setBootstrapRun(nextRun)
      }
      if (selectedTarget) await openTarget(selectedTarget, nextRun)
      const refreshes: Promise<unknown>[] = [loadEvidence()]
      if (nextRun?.status === 'review_ready') {
        refreshes.push(
          semanticAccessRequest<SemanticAccessBootstrapSuggestions>(
            apiPath(`/access-bootstrap/runs/${nextRun.run_id}/suggestions`),
          ).then(setBootstrapSuggestions),
        )
      }
      await Promise.all(refreshes)
      setNotice(
        `${result.direct_table_ids.length} 张表已直接生效，${result.review_table_ids.length} 张表仍需复核`,
      )
    } catch (cause) {
      dismissSimilarSuggestions()
      setError(semanticAccessErrorMessage(cause, '相似修改应用失败，所选表均未调整'))
    } finally {
      setBusy('')
    }
  }, [
    apiPath,
    bootstrapRun,
    dismissSimilarSuggestions,
    loadEvidence,
    openTarget,
    selectedSimilarSuggestionIds,
    selectedTarget,
    similarSuggestion,
    similarSuggestionContext,
  ])

  useEffect(() => {
    dismissSimilarSuggestions()
  }, [area, dismissSimilarSuggestions, selectedTableId, selectedTarget?.target_id, selectedTarget?.target_type])

  useEffect(() => {
    if (!loading && area === 'baseline' && baselineTarget && !selectedTarget) {
      void openTarget(baselineTarget)
    }
  }, [area, baselineTarget, loading, openTarget, selectedTarget])

  useEffect(() => {
    if (!bootstrapRun || bootstrapRun.status !== 'review_ready' || !selectedTarget) return
    void openTarget(selectedTarget)
    // Re-open only when a newly materialized draft becomes available.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootstrapRun?.run_id])

  const guarded = (action: () => void) => {
    if (!isDirty) return action()
    pendingAction.current = action
    setUnsavedOpen(true)
  }

  const executePending = () => {
    const action = pendingAction.current
    pendingAction.current = null
    setUnsavedOpen(false)
    action?.()
  }

  const selectBaseline = () => guarded(() => {
    if (!baselineTarget) return
    setArea('baseline')
    setTargetTab('org_unit')
    setTargetOptions([])
    setTargetSearch('')
    setMobileTreeOpen(false)
    void openTarget(baselineTarget)
  })

  const loadTab = async (
    nextArea: Area,
    orgId: number | null,
    tab: TargetTab,
    search = '',
  ) => {
    setArea(nextArea)
    setSelectedOrgId(orgId)
    setTargetTab(tab)
    setTargetSearch(search)
    setTargetLoading(true)
    setError('')
    try {
      if (nextArea === 'unassigned') {
        const page = await loadTargets('user', null, search, true)
        setTargetOptions(page.items)
        setCanManageWorkspace(page.can_manage_workspace)
        if (page.items[0]) await openTarget(page.items[0])
        else setSelectedTarget(null)
        return
      }
      if (tab === 'org_unit') {
        const target = orgTargetById.get(Number(orgId))
        setTargetOptions(target ? [target] : [])
        if (target) await openTarget(target)
        else setSelectedTarget(null)
        return
      }
      const page = await loadTargets(tab, orgId, search)
      setTargetOptions(page.items)
      setCanManageWorkspace(page.can_manage_workspace)
      if (page.items[0]) await openTarget(page.items[0])
      else setSelectedTarget(null)
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '授权对象加载失败'))
    } finally {
      setTargetLoading(false)
      setMobileTreeOpen(false)
    }
  }

  const selectOrganization = (orgId: number) => guarded(() => {
    void loadTab('organization', orgId, 'org_unit')
  })

  const selectUnassigned = () => guarded(() => {
    void loadTab('unassigned', null, 'user')
  })

  const selectTab = (tab: TargetTab) => guarded(() => {
    if (area !== 'organization') return
    void loadTab('organization', selectedOrgId, tab)
  })

  const searchTargets = () => guarded(() => {
    void loadTab(area, selectedOrgId, area === 'unassigned' ? 'user' : targetTab, targetSearch)
  })

  const updateRule = (patch: Partial<SemanticAccessTableRule>) => {
    if (!currentTable) return
    const seed = currentRule || currentEffectiveRule?.override_seed
    const next: SemanticAccessTableRule = {
      table_id: currentTable.id,
      hidden_column_ids: [],
      hidden_metric_ids: [],
      ...seed,
      ...patch,
    }
    if ('decision' in patch) {
      if (next.decision !== 'visible') {
        delete next.row_scope
      } else if (!next.row_scope) {
        const suggestedScope = draftTarget?.candidates.find(
          candidate => candidate.table_id === currentTable.id,
        )?.row_scope
        next.row_scope = suggestedScope && typeof suggestedScope === 'object'
          ? suggestedScope
          : suggestedScope === 'target_org_tree'
            ? { type: 'target_org_tree' }
            : suggestedScope === 'all' || selectedTarget?.target_type === 'baseline'
              ? { type: 'all' }
              : { type: 'target_org' }
      }
    }
    if (next.decision == null) {
      setRules(previous => previous.filter(item => item.table_id !== currentTable.id))
    } else {
      setRules(previous => [
        ...previous.filter(item => item.table_id !== currentTable.id),
        next,
      ])
    }
  }

  const restoreInheritedRule = () => {
    if (!currentTable) return
    setRules(previous => previous.filter(item => item.table_id !== currentTable.id))
  }

  const publishCurrentTables = useCallback(async (
    tableIds: number[],
    successMessage: string,
  ): Promise<boolean> => {
    if (!selectedTarget || !bootstrapRun || !canManage || !tableIds.length) return false
    setBusy(tableIds.length === 1 ? `evidence-table:${tableIds[0]}` : 'evidence-target')
    setError('')
    setNotice('')
    const sourceBeforeRule = tableIds.length === 1
      ? activeRules.find(item => item.table_id === tableIds[0]) || null
      : null
    try {
      const result = await semanticAccessRequest<BootstrapTablePublishResponse>(
        apiPath(
          `/access-bootstrap/runs/${bootstrapRun.run_id}/targets/${selectedTarget.target_type}/${encodeURIComponent(selectedTarget.target_id)}/publish-tables`,
        ),
        {
          method: 'POST',
          body: JSON.stringify({
            expected_revision: bootstrapRun.revision,
            table_ids: tableIds,
            definition: {
              name: selectedTarget.label,
              tables: savableRules,
            },
            include_descendants: selectedTarget.target_type === 'org_unit',
            reason: sourceText.trim() || null,
            confirm_warnings: true,
          }),
        },
      )
      setBootstrapRun(result.run)
      setDraftTarget(result.target)
      setRules(result.target.definition.tables || [])
      setActiveRules(result.target.definition.tables || [])
      setBinding(current => current && ({
        ...current,
        id: result.binding_id,
        binding_id: result.binding_id,
        revision: result.binding_revision,
        active_version_id: result.version_id,
        include_descendants: result.target.include_descendants,
        active_version: {
          id: result.version_id,
          binding_id: result.binding_id,
          version: result.version,
          definition: result.active_definition,
        },
      }))
      const [nextEvidence, nextSuggestions] = await Promise.all([
        semanticAccessRequest<EvidencePayload>(
          apiPath(`/access-evidence/sets/${evidence.set?.id}`),
        ),
        semanticAccessRequest<SemanticAccessBootstrapSuggestions>(
          apiPath(`/access-bootstrap/runs/${result.run.run_id}/suggestions`),
        ),
      ])
      setEvidence(nextEvidence)
      setBootstrapSuggestions(nextSuggestions)
      await openTarget(selectedTarget, result.run)
      if (tableIds.length === 1) {
        await previewSimilarSuggestions(
          tableIds[0],
          sourceBeforeRule,
          result.binding_revision,
          result.run,
        )
      }
      setNotice(`${successMessage}，权限已立即生效`)
      return true
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '权限保存失败，原有生效版本保持不变'))
      return false
    } finally {
      setBusy('')
    }
  }, [
    apiPath,
    activeRules,
    bootstrapRun,
    canManage,
    evidence.set?.id,
    openTarget,
    previewSimilarSuggestions,
    savableRules,
    selectedTarget,
    sourceText,
  ])

  const save = useCallback(async (confirmWarnings = false): Promise<boolean> => {
    if (!selectedTarget || !binding || !canManage) return false
    setBusy('save')
    setError('')
    setNotice('')
    const sourceTableId = currentTable?.id || 0
    const sourceBeforeRule = sourceTableId
      ? activeRules.find(item => item.table_id === sourceTableId) || null
      : null
    try {
      const result = await semanticAccessRequest<SemanticAccessBinding & {
        audit_id?: number
      }>(apiPath(
        `/access-policies/${selectedTarget.target_type}/${encodeURIComponent(selectedTarget.target_id)}`,
      ), {
        method: 'PUT',
        body: JSON.stringify({
          datasource_id: datasourceId,
          expected_revision: binding.revision,
          include_descendants: selectedTarget.target_type === 'org_unit',
          definition: {
            name: selectedTarget.label,
            tables: savableRules,
          },
          source_text: sourceText.trim() || null,
          confirm_warnings: confirmWarnings,
        }),
      })
      setBinding(result)
      setRules(savableRules)
      setActiveRules(savableRules)
      if (selectedTarget) await openTarget(selectedTarget)
      if (result.binding_id) {
        setVersions(await semanticAccessRequest<SemanticAccessVersion[]>(
          apiPath(`/access-bindings/${result.binding_id}/versions`),
        ))
      }
      const [orgPage, baselinePage] = await Promise.all([
        loadTargets('org_unit'),
        loadTargets('baseline'),
      ])
      setOrgTargets(orgPage.items)
      setBaselineTarget(baselinePage.items[0] || null)
      if (sourceTableId) {
        await previewSimilarSuggestions(
          sourceTableId,
          sourceBeforeRule,
          result.revision,
          null,
        )
      }
      setNotice(`版本 v${result.active_version?.version || result.revision} 已生效 · 影响 ${result.affected_user_count ?? 0} 个账号`)
      return true
    } catch (cause) {
      if (cause instanceof SemanticAccessApiError
        && cause.code === 'warnings_confirmation_required'
        && !confirmWarnings
        && window.confirm('配置存在提醒，确认仍要立即生效吗？')) {
        return save(true)
      }
      setError(semanticAccessErrorMessage(cause, '保存失败，旧版本仍在生效'))
      return false
    } finally {
      setBusy('')
    }
  }, [
    apiPath,
    activeRules,
    binding,
    canManage,
    currentTable?.id,
    datasourceId,
    loadTargets,
    openTarget,
    previewSimilarSuggestions,
    savableRules,
    selectedTarget,
    sourceText,
  ])

  const saveCurrent = useCallback(() => {
    if (!initialReviewMode) return save()
    if (!currentTable) return Promise.resolve(false)
    return publishCurrentTables([currentTable.id], `${currentTable.business_name} 已确认`)
  }, [currentTable, initialReviewMode, publishCurrentTables, save])

  const generateFirstPermissions = async () => {
    setBusy('evidence-generate')
    setError('')
    setNotice('')
    try {
      const run = await semanticAccessRequest<EvidenceGenerationRun>(
        apiPath('/access-evidence/generate'),
        {
          method: 'POST',
          body: JSON.stringify({ datasource_id: datasourceId }),
        },
      )
      setGenerationRun(run)
      setNotice('首次权限正在生成；完成后会自动填入部门、表和字段配置草稿。')
      await loadEvidence()
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '首次权限生成失败'))
    } finally {
      setBusy('')
    }
  }

  const loadMatrix = async () => {
    setMatrixOpen(true)
    setMatrixLoading(true)
    setError('')
    try {
      setMatrix(await semanticAccessRequest<AccessMatrixPayload>(
        apiPath(`/access-matrix?datasource_id=${datasourceId}`),
      ))
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '权限矩阵加载失败'))
      setMatrixOpen(false)
    } finally {
      setMatrixLoading(false)
    }
  }

  const confirmEvidenceTarget = async (
    mode: 'table' | 'ordinary_target' | 'target',
    tableId?: number,
  ) => {
    if (!evidence.set || !selectedTarget) return
    const tableIds = mode === 'table' && tableId
      ? [tableId]
      : mode === 'ordinary_target'
        ? [...evidenceConfirmationsByTable.entries()]
          .filter(([, confirmation]) => (
            confirmation.tablePendingItems > 0
            && confirmation.pendingRiskTableIds.length === 0
          ))
          .map(([pendingTableId]) => pendingTableId)
        : enabledTables.map(table => table.id)
    if (!tableIds.length) return
    const label = mode === 'table'
      ? `${enabledTables.find(table => table.id === tableId)?.business_name || '本表'} 已确认`
      : selectedTarget.target_type === 'baseline'
        ? '全员基线已确认'
        : '本部门权限已确认'
    if (await publishCurrentTables(tableIds, label)) {
      if (mode === 'target') setDepartmentConfirmOpen(false)
    }
  }

  const saveMapping = async () => {
    if (!currentTable || !currentMapping || !canManageWorkspace) return
    setBusy('mapping')
    setError('')
    try {
      const saved = await semanticAccessRequest<Mapping>(
        apiPath(`/ownership-mappings/${currentTable.id}?datasource_id=${datasourceId}`),
        { method: 'PUT', body: JSON.stringify(currentMapping) },
      )
      setMappings(previous => ({ ...previous, [currentTable.id]: saved }))
      setNotice(`${currentTable.business_name} 的数据归属映射已保存。`)
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '归属映射保存失败'))
    } finally {
      setBusy('')
    }
  }

  const generateSuggestion = async () => {
    if (!binding?.binding_id || !sourceText.trim()) return
    setBusy('suggest')
    setError('')
    try {
      setSuggestion(await semanticAccessRequest<Suggestion>(
        apiPath(`/access-bindings/${binding.binding_id}/suggestion`),
        { method: 'POST', body: JSON.stringify({ source_text: sourceText }) },
      ))
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '生成表单建议失败'))
    } finally {
      setBusy('')
    }
  }

  const rollback = async (version: SemanticAccessVersion) => {
    if (!binding?.binding_id || version.active || !canManage) return
    if (!window.confirm(`确认从 v${version.version} 生成一个新的生效版本吗？`)) return
    setBusy(`rollback:${version.id}`)
    setError('')
    try {
      const result = await semanticAccessRequest<SemanticAccessBinding>(
        apiPath(`/access-bindings/${binding.binding_id}/versions/${version.id}/rollback`),
        { method: 'POST', body: JSON.stringify({ expected_revision: binding.revision }) },
      )
      const nextRules = result.active_version?.definition?.tables || []
      setBinding(result)
      setRules(nextRules)
      setActiveRules(nextRules)
      setVersions(await semanticAccessRequest<SemanticAccessVersion[]>(
        apiPath(`/access-bindings/${binding.binding_id}/versions`),
      ))
      if (selectedTarget) await openTarget(selectedTarget)
      setNotice(`已从 v${version.version} 生成新的生效版本。`)
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '回滚失败，旧版本仍在生效'))
    } finally {
      setBusy('')
    }
  }

  const reviewEvidence = async (
    decisions: Array<{
      kind: 'asset' | 'relation'
      asset_id?: number
      relation_id?: number
      action: 'accepted' | 'modified' | 'rejected'
      baseline_access?: 'workspace_visible' | 'controlled'
      requires_individual_review?: boolean
      access_class?: EvidenceAsset['access_class']
      reason?: string
    }>,
  ) => {
    if (!evidence.set) return
    setBusy('evidence-review')
    setError('')
    try {
      const next = await semanticAccessRequest<EvidencePayload>(
        apiPath(`/access-evidence/sets/${evidence.set.id}/review-decisions`),
        {
          method: 'PATCH',
          body: JSON.stringify({
            expected_revision: evidence.set.revision,
            decisions,
          }),
        },
      )
      setEvidence(next)
      setNotice('审核结果已保存，尚未发布权限。')
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '访问依据审核失败'))
    } finally {
      setBusy('')
    }
  }

  const updateEvidenceRelation = async (
    relation: EvidenceRelation,
    patch: Partial<EvidenceRelation>,
  ) => {
    if (!evidence.set) return
    setBusy(`evidence-relation:${relation.id}`)
    setError('')
    try {
      const next = await semanticAccessRequest<EvidencePayload>(
        apiPath(`/access-evidence/sets/${evidence.set.id}/relations/${relation.id}`),
        {
          method: 'PATCH',
          body: JSON.stringify({
            expected_revision: evidence.set.revision,
            access_level: patch.access_level ?? relation.access_level,
            field_decision: patch.field_decision ?? relation.field_decision,
            row_scope: patch.row_scope ?? relation.row_scope,
            reason: patch.reason ?? relation.reason,
            review_status: patch.review_status ?? 'modified',
            override_reason: patch.override_reason ?? relation.override_reason,
          }),
        },
      )
      setEvidence(next)
      setNotice('关系依据已保存。')
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '关系依据保存失败'))
    } finally {
      setBusy('')
    }
  }

  const retryEvidence = async () => {
    if (!evidence.set) return
    setBusy('evidence-retry')
    try {
      await semanticAccessRequest(
        apiPath(`/access-evidence/sets/${evidence.set.id}/retry`),
        { method: 'POST' },
      )
      setNotice('已重新排队生成访问依据。')
      setEvidence(current => current.set
        ? { ...current, set: { ...current.set, status: 'generating' } }
        : current)
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '访问依据重试失败'))
    } finally {
      setBusy('')
    }
  }

  const departmentPanel = (
    <DepartmentTree
      tree={orgTree}
      baselineSelected={area === 'baseline'}
      unassignedSelected={area === 'unassigned'}
      selectedOrgId={selectedOrgId}
      expandedOrgIds={expandedOrgIds}
      statusByOrgId={orgTargetById}
      baselineConfigured={Boolean(baselineTarget?.configured)}
      draftOrgIds={bootstrapDraftOrgIds}
      baselineDraft={Boolean(bootstrapSuggestions?.targets.some(
        target => target.target_type === 'baseline',
      ))}
      onSelectBaseline={selectBaseline}
      onSelectOrganization={selectOrganization}
      onSelectUnassigned={selectUnassigned}
      onToggle={orgId => setExpandedOrgIds(current => {
        const next = new Set(current)
        if (next.has(orgId)) next.delete(orgId)
        else next.add(orgId)
        return next
      })}
    />
  )
  const directPolicyLayout = (
    area === 'baseline'
    || (area === 'organization' && targetTab === 'org_unit')
  )
  const directPolicyActions = (
    directPolicyLayout && selectedTarget && binding
      ? <>
          {evidenceConfirmationTarget
            && evidenceConfirmation
            && evidenceConfirmation.targetPendingItems > 0
            && canManage
            && canManageWorkspace && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (evidenceConfirmation.pendingRiskTableIds.length > 0) {
                  setDepartmentConfirmOpen(true)
                  return
                }
                void confirmEvidenceTarget('target')
              }}
              disabled={Boolean(busy)}
            >
              {busy === 'evidence-target'
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Check className="h-4 w-4" />}
              {evidenceConfirmationTarget.type === 'baseline'
                ? '确认全员基线'
                : '确认本部门'}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => setToolDrawerOpen(true)}>
            <Settings2 className="h-4 w-4" />工具与历史
          </Button>
        </>
      : undefined
  )

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-manus text-manus-text">
      <header className="flex min-h-[88px] shrink-0 items-center justify-between gap-4 border-b border-manus-border bg-manus/95 px-4 backdrop-blur md:px-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-emerald-400" />
            <h1 className="truncate text-xl font-semibold tracking-tight">问数数据权限</h1>
            <Badge variant="outline" className="border-emerald-500/40 bg-emerald-500/10 text-emerald-300">DEFAULT DENY</Badge>
          </div>
          <p className="mt-1 truncate text-sm text-manus-muted">
            {datasourceName || `数据源 #${datasourceId}`} · 按部门、岗位与账号叠加授权，明确拒绝始终优先
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {canManage && canManageWorkspace && evidenceReadiness?.access_bootstrap_required && !initialReviewMode && (
            <Button
              variant="outline"
              className="border-accent/35 bg-accent/5 text-accent hover:bg-accent/10"
              onClick={() => void generateFirstPermissions()}
              disabled={
                !evidenceReadiness.ready
                || busy === 'evidence-generate'
                || Boolean(generationRun && ['queued', 'running'].includes(generationRun.status))
              }
              title={!evidenceReadiness.ready ? '部门画像或启用表字段尚未就绪' : undefined}
            >
              {(busy === 'evidence-generate' || generationRun && ['queued', 'running'].includes(generationRun.status))
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Wand2 className="h-4 w-4" />}
              <span className="hidden md:inline">
                {generationRun && ['queued', 'running'].includes(generationRun.status)
                  ? `生成中 ${generationRun.progress || 0}%`
                  : '生成首次权限'}
              </span>
            </Button>
          )}
          <Button variant="outline" onClick={() => void loadMatrix()}>
            <ShieldCheck className="h-4 w-4" />
            <span className="hidden sm:inline">权限矩阵</span>
          </Button>
          <Button variant="outline" onClick={() => guarded(() => void loadBase())} disabled={loading}>
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
            <span className="hidden sm:inline">刷新</span>
          </Button>
          <Button variant="outline" onClick={() => guarded(onClose)}><ArrowLeft className="h-4 w-4" />返回</Button>
        </div>
      </header>

      {(error || notice) && (
        <div className={cn(
          'flex items-center gap-2 border-b px-6 py-2 text-sm',
          error
            ? 'border-red-500/20 bg-red-500/10 text-red-300'
            : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300',
        )}>
          {error
            ? <CircleAlert className="h-4 w-4" />
            : <Check className="h-4 w-4" />}
          <span className="min-w-0 flex-1 truncate">
            {error || notice}
          </span>
          <button onClick={() => { setError(''); setNotice('') }} className="text-xs underline">关闭</button>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col p-3 md:p-4">
        <div className="mb-3 lg:hidden">
          <Sheet open={mobileTreeOpen} onOpenChange={setMobileTreeOpen}>
            <SheetTrigger asChild>
              <Button variant="outline" className="w-full justify-start">
                <Menu className="h-4 w-4" />选择授权部门
                <span className="ml-auto text-manus-muted">
                  {area === 'baseline' ? '全员基线' : area === 'unassigned' ? '待分配账号' : selectedOrg?.name || '未选择'}
                </span>
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="border-manus-border bg-manus-secondary p-0">
              <SheetHeader className="sr-only"><SheetTitle>授权部门</SheetTitle></SheetHeader>
              {departmentPanel}
            </SheetContent>
          </Sheet>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[286px_minmax(0,1fr)]">
          <Card className="hidden min-h-0 overflow-hidden border-manus-border bg-manus-secondary lg:block">
            {departmentPanel}
          </Card>

          <Card className="flex min-h-0 min-w-0 flex-col overflow-hidden border-manus-border bg-manus-secondary">
            {loading ? (
              <CenteredState icon={<Loader2 className="animate-spin" />} title="正在加载组织权限" />
            ) : import.meta.env.VITE_SEMANTIC_ACCESS_EVIDENCE_WORKSPACE === 'true'
              && evidenceReadiness?.access_bootstrap_required ? (
              <EvidenceWorkspace
                area={area}
                selectedOrg={selectedOrg}
                tables={enabledTables}
                columns={enabledColumns}
                organizations={organizations}
                mappings={mappings}
                evidence={evidence}
                readiness={evidenceReadiness}
                focusTableId={selectedTableId}
                filter={evidenceFilter}
                busy={busy}
                canManage={canManage && canManageWorkspace}
                onFilter={setEvidenceFilter}
                onReview={reviewEvidence}
                onUpdateRelation={updateEvidenceRelation}
                onConfirmTarget={(mode, tableId) => void confirmEvidenceTarget(mode, tableId)}
                onRetry={() => void retryEvidence()}
                onOpenMappings={tableId => {
                  setSelectedTableId(tableId)
                  setToolDrawerOpen(true)
                }}
              />
            ) : (
              <>
                  <TargetHeader
                    area={area}
                    selectedOrg={selectedOrg}
                    targetTab={targetTab}
                    draftMode={initialReviewMode}
                    actions={directPolicyActions}
                    onSelectTab={selectTab}
                  />

                  <div className={cn(
                    'grid min-h-0 flex-1 grid-cols-1',
                    !directPolicyLayout && 'xl:grid-cols-[260px_minmax(0,1fr)]',
                  )}>
                  {!directPolicyLayout && (
                    <TargetList
                      area={area}
                      targetTab={targetTab}
                      targets={targetOptions}
                      selectedTarget={selectedTarget}
                      search={targetSearch}
                      loading={targetLoading}
                      onSearch={setTargetSearch}
                      onSubmitSearch={searchTargets}
                      onSelect={target => guarded(() => void openTarget(target))}
                    />
                  )}

                  <main className={cn(
                    'flex min-h-0 min-w-0 flex-col',
                    !directPolicyLayout
                      && 'border-t border-manus-border xl:border-l xl:border-t-0',
                  )}>
                    {selectedTarget && binding ? (
                      <>
                        {!directPolicyLayout && (
                          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-manus-border px-4 py-3">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <h2 className="truncate font-semibold">{selectedTarget.label}</h2>
                              <TargetStatus
                                target={selectedTarget}
                                configured={rules.length > 0}
                                draft={initialReviewMode && Boolean(draftTarget)}
                              />
                            </div>
                            <p className="mt-1 text-xs text-manus-muted">
                              {semanticTargetLabel[selectedTarget.target_type]}策略 · 影响约 {binding.affected_user_count ?? selectedTarget.affected_user_count} 个账号 · 修订 {binding.revision}
                            </p>
                          </div>
                          <div className="flex items-center gap-2">
                            <Button variant="outline" size="sm" onClick={() => setToolDrawerOpen(true)}>
                              <Settings2 className="h-4 w-4" />工具与历史
                            </Button>
                          </div>
                          </div>
                        )}
                        {canManage && ['position', 'user'].includes(selectedTarget.target_type) && (
                          <div className="border-b border-manus-border px-4 py-3">
                            <Input
                              value={sourceText}
                              onChange={event => setSourceText(event.target.value)}
                              placeholder="若本次扩大岗位或账号访问，请填写变更理由（必填）"
                            />
                          </div>
                        )}

                        {targetLoading ? (
                          <CenteredState icon={<Loader2 className="animate-spin" />} title="正在加载策略" />
                        ) : (
                          <PolicyEditor
                            tables={enabledTables}
                            columns={tableColumns}
                            metrics={tableMetrics}
                            organizations={organizations}
                            target={selectedTarget}
                            selectedTableId={currentTable?.id || 0}
                            currentTable={currentTable}
                            rule={currentRule}
                            activeRules={activeRules}
                            effectiveRules={effectiveRules}
                            candidates={draftTarget?.candidates || []}
                            draftMode={initialReviewMode}
                            canManage={canManage}
                            canManageWorkspace={canManageWorkspace}
                            highRiskTableIds={highRiskTableIds}
                            sensitiveTableIds={sensitiveTableIds}
                            confirmationsByTable={evidenceConfirmationsByTable}
                            inheritedEvidenceReviewsByTable={inheritedEvidenceReviewsByTable}
                            confirmation={evidenceConfirmation || undefined}
                            dirty={currentTableDirty}
                            confirmBusy={
                              busy === `evidence-table:${currentTable?.id}`
                              || busy === 'save'
                            }
                            similarSuggestionLoading={similarSuggestionLoading}
                            onSelectTable={setSelectedTableId}
                            onUpdateRule={updateRule}
                            onRestoreInherited={restoreInheritedRule}
                            onSaveTable={saveCurrent}
                          />
                        )}
                      </>
                    ) : (
                      <CenteredState
                        icon={<ShieldCheck />}
                        title="请选择授权对象"
                        description="从部门、岗位或账号列表中选择一项，然后配置直接问数权限。"
                      />
                    )}
                  </main>
                </div>
              </>
            )}
          </Card>
        </div>
      </div>

      <Sheet open={toolDrawerOpen} onOpenChange={setToolDrawerOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto border-manus-border bg-manus-secondary sm:max-w-xl">
          <SheetHeader>
            <SheetTitle className="flex items-center gap-2 text-manus-text"><Sparkles className="h-5 w-5 text-accent" />策略工具与版本</SheetTitle>
          </SheetHeader>
          <div className="mt-6 space-y-7">
            <section>
              <div className="flex items-center gap-2"><Wand2 className="h-4 w-4" /><h3 className="font-medium">自然语言填表</h3></div>
              <p className="mt-1 text-xs text-manus-muted">只生成当前对象的表单草稿；检查差异后仍需手动保存。</p>
              <Textarea
                value={sourceText}
                onChange={event => setSourceText(event.target.value)}
                className="mt-3 min-h-28 bg-manus-tertiary"
                placeholder="例如：允许生产部经理查看订单表，但隐藏手机号，只查看生产部及下级数据。"
              />
              <Button
                variant="secondary"
                className="mt-2 w-full"
                onClick={() => void generateSuggestion()}
                disabled={!binding?.binding_id || !sourceText.trim() || busy === 'suggest'}
              >
                {busy === 'suggest' && <Loader2 className="h-4 w-4 animate-spin" />}
                {binding?.binding_id ? '生成表单建议' : '首次保存后可生成建议'}
              </Button>
              {suggestion && (
                <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                  <div className="font-medium text-amber-200">建议包含 {suggestion.draft_patch?.tables?.length || 0} 张表</div>
                  {(suggestion.validation?.blockers || []).map(item => <p key={item.code} className="mt-1 text-xs text-red-300">阻断：{item.message}</p>)}
                  {(suggestion.validation?.warnings || []).map(item => <p key={item.code} className="mt-1 text-xs text-amber-300">提醒：{item.message}</p>)}
                  <Button size="sm" className="mt-3" onClick={() => {
                    setRules(suggestion.draft_patch?.tables || [])
                    setSuggestion(null)
                    setNotice('建议已应用到当前草稿，尚未保存。')
                  }}>应用到草稿</Button>
                </div>
              )}
            </section>

            <section className="border-t border-manus-border pt-6">
              <div className="flex items-center gap-2"><Settings2 className="h-4 w-4" /><h3 className="font-medium">当前表数据归属映射</h3></div>
              {currentTable && currentMapping ? (
                <MappingEditor
                  table={currentTable}
                  columns={tableColumns}
                  mapping={currentMapping}
                  canEdit={canManage && canManageWorkspace}
                  busy={busy === 'mapping'}
                  onChange={mapping => setMappings(previous => ({ ...previous, [currentTable.id]: mapping }))}
                  onSave={() => void saveMapping()}
                />
              ) : <p className="mt-2 text-sm text-manus-muted">请先选择语义表。</p>}
            </section>

            <section className="border-t border-manus-border pt-6">
              <div className="flex items-center gap-2"><History className="h-4 w-4" /><h3 className="font-medium">版本历史</h3></div>
              <div className="mt-3 space-y-2">
                {versions.length === 0 && <p className="text-sm text-manus-muted">当前对象还没有生效版本。</p>}
                {versions.map(version => (
                  <div key={version.id} className="flex items-center justify-between rounded-lg border border-manus-border bg-manus-tertiary px-3 py-3">
                    <div><div className="flex items-center gap-2"><b className="text-sm">v{version.version}</b>{version.source_type === 'evidence_bootstrap' && <Badge variant="outline" className="border-accent/30 text-accent">访问依据</Badge>}</div><div className="text-xs text-manus-muted">{version.created_at ? new Date(version.created_at).toLocaleString() : '-'}</div></div>
                    {version.active ? <Badge>生效中</Badge> : canManage && (
                      <Button size="sm" variant="outline" onClick={() => void rollback(version)} disabled={busy === `rollback:${version.id}`}>回滚</Button>
                    )}
                  </div>
                ))}
              </div>
            </section>
          </div>
        </SheetContent>
      </Sheet>

      <Dialog
        open={departmentConfirmOpen}
        onOpenChange={open => {
          if (busy !== 'evidence-target') setDepartmentConfirmOpen(open)
        }}
      >
        <DialogContent className="max-w-xl border-manus-border bg-manus-secondary">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-manus-text">
              <CircleAlert className="h-5 w-5 text-amber-400" />
              存在未逐表确认的敏感配置
            </DialogTitle>
            <DialogDescription>
              当前{evidenceConfirmationTarget?.type === 'baseline' ? '全员基线' : '部门'}
              仍有 {pendingRiskTables.length} 张高风险或含敏感字段的表尚未按表确认。
              直接确认将接受这些表及其可见敏感字段的系统建议。
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-64 space-y-2 overflow-y-auto">
            {pendingRiskTables.map(item => (
              <div
                key={item.tableId}
                className="flex items-center justify-between gap-3 rounded-lg border border-amber-500/20 bg-amber-500/6 px-3 py-2"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-manus-text">
                    {item.table?.business_name || `表 #${item.tableId}`}
                  </div>
                  {item.table?.physical_name && (
                    <div className="truncate font-mono text-[11px] text-manus-muted">
                      {item.table.physical_name}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 gap-1">
                  {item.highRisk && (
                    <Badge variant="outline" className="border-amber-500/35 text-amber-300">
                      高风险
                    </Badge>
                  )}
                  {item.sensitive && (
                    <Badge variant="outline" className="border-rose-500/35 text-rose-300">
                      敏感配置
                    </Badge>
                  )}
                </div>
              </div>
            ))}
          </div>
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => setDepartmentConfirmOpen(false)}
              disabled={busy === 'evidence-target'}
            >
              返回查看
            </Button>
            <Button
              onClick={() => void confirmEvidenceTarget('target')}
              disabled={busy === 'evidence-target'}
              className="bg-amber-500 text-black hover:bg-amber-400"
            >
              {busy === 'evidence-target' && <Loader2 className="h-4 w-4 animate-spin" />}
              {evidenceConfirmationTarget?.type === 'baseline'
                ? '直接确认全员基线'
                : '直接确认本部门'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={matrixOpen} onOpenChange={setMatrixOpen}>
        <DialogContent className="flex h-[90vh] w-[min(96vw,1540px)] max-w-none flex-col overflow-hidden border-manus-border bg-manus-secondary">
          <DialogHeader>
            <DialogTitle>权限矩阵</DialogTitle>
            <DialogDescription>展示启用表与所有可管理部门的实际权限；点击单元格可返回对应部门和表继续配置。</DialogDescription>
          </DialogHeader>
          {matrixLoading ? (
            <CenteredState icon={<Loader2 className="animate-spin" />} title="正在计算权限矩阵" />
          ) : matrix ? (
            <AccessMatrix
              payload={matrix}
              onSelect={(departmentId, tableId) => {
                setMatrixOpen(false)
                guarded(() => {
                  setSelectedTableId(tableId)
                  void loadTab('organization', departmentId, 'org_unit')
                })
              }}
            />
          ) : (
            <CenteredState icon={<Inbox />} title="暂无矩阵数据" />
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(similarSuggestion)}
        onOpenChange={open => { if (!open) dismissSimilarSuggestions() }}
      >
        <DialogContent className="flex max-h-[82vh] w-[min(94vw,920px)] max-w-none flex-col overflow-hidden border-manus-border bg-manus-secondary">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-manus-text">
              <Sparkles className="h-5 w-5 text-emerald-500" />
              相似修改建议
            </DialogTitle>
            <DialogDescription>
              发现其他表存在相似字段。勾选后，可将本次权限调整同步到所选表。
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 overflow-auto rounded-xl border border-manus-border">
            <div className="grid min-w-[820px] grid-cols-[44px_minmax(140px,1fr)_minmax(140px,1fr)_minmax(220px,1.6fr)_140px] gap-3 border-b border-manus-border bg-manus-tertiary/70 px-4 py-3 text-xs font-medium text-manus-muted">
              <span />
              <span>数据表</span>
              <span>匹配字段</span>
              <span>权限调整</span>
              <span>应用后状态</span>
            </div>
            {(similarSuggestion?.candidates || []).map(candidate => {
              const checked = selectedSimilarSuggestionIds.includes(candidate.candidate_id)
              return (
                <label
                  key={candidate.candidate_id}
                  className="grid min-w-[820px] cursor-pointer grid-cols-[44px_minmax(140px,1fr)_minmax(140px,1fr)_minmax(220px,1.6fr)_140px] items-center gap-3 border-b border-manus-border px-4 py-3 last:border-b-0 hover:bg-manus-hover"
                >
                  <Checkbox
                    checked={checked}
                    disabled={busy === 'similar-suggestion-apply'}
                    onCheckedChange={next => setSelectedSimilarSuggestionIds(current => (
                      next
                        ? [...new Set([...current, candidate.candidate_id])]
                        : current.filter(id => id !== candidate.candidate_id)
                    ))}
                  />
                  <span className="min-w-0">
                    <b className="block truncate text-sm font-medium text-manus-text">{candidate.table_name}</b>
                    {candidate.expands_access && <Pill tone="amber">扩大权限</Pill>}
                  </span>
                  <span className="text-sm text-manus-text">{candidate.matched_fields.join('、')}</span>
                  <span className="space-y-1 text-sm text-manus-text">
                    {candidate.adjustments.map(item => <span key={item} className="block">{item}</span>)}
                  </span>
                  <Pill tone={candidate.apply_status === 'direct' ? 'green' : 'amber'}>
                    {candidate.apply_status === 'direct' ? '直接生效' : '应用后仍需复核'}
                  </Pill>
                </label>
              )
            })}
          </div>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={dismissSimilarSuggestions} disabled={busy === 'similar-suggestion-apply'}>
              暂不处理
            </Button>
            <Button
              onClick={() => void applySimilarSuggestions()}
              disabled={selectedSimilarSuggestionIds.length === 0 || busy === 'similar-suggestion-apply'}
              className="bg-emerald-500 text-white hover:bg-emerald-600"
            >
              {busy === 'similar-suggestion-apply' && <Loader2 className="h-4 w-4 animate-spin" />}
              应用到已选表
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={unsavedOpen} onOpenChange={open => !open && setUnsavedOpen(false)}>
        <DialogContent className="border-manus-border bg-manus-secondary">
          <DialogHeader>
            <DialogTitle className="text-manus-text">当前策略尚未保存</DialogTitle>
            <DialogDescription>切换授权对象前，可以先保存当前修改、放弃修改或取消切换。</DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:justify-between">
            <Button variant="ghost" onClick={() => { pendingAction.current = null; setUnsavedOpen(false) }}>取消</Button>
            <div className="flex gap-2">
              <Button variant="outline" onClick={executePending}>放弃修改</Button>
              <Button onClick={async () => { if (await saveCurrent()) executePending() }} disabled={['save', 'draft-save'].includes(busy)}>保存并继续</Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function DepartmentTree({
  tree,
  baselineSelected,
  unassignedSelected,
  selectedOrgId,
  expandedOrgIds,
  statusByOrgId,
  baselineConfigured,
  draftOrgIds,
  baselineDraft,
  onSelectBaseline,
  onSelectOrganization,
  onSelectUnassigned,
  onToggle,
}: {
  tree: OrgTreeNode[]
  baselineSelected: boolean
  unassignedSelected: boolean
  selectedOrgId: number | null
  expandedOrgIds: Set<number>
  statusByOrgId: Map<number, SemanticAccessTargetSummary>
  baselineConfigured: boolean
  draftOrgIds: Set<number>
  baselineDraft: boolean
  onSelectBaseline: () => void
  onSelectOrganization: (id: number) => void
  onSelectUnassigned: () => void
  onToggle: (id: number) => void
}) {
  return (
    <div className="flex h-full min-h-[560px] flex-col">
      <div className="border-b border-manus-border px-4 py-4">
        <div className="text-xs font-semibold uppercase tracking-[0.16em] text-manus-subtle">授权组织</div>
        <p className="mt-1 text-xs text-manus-muted">选择部门后配置部门、岗位或账号</p>
      </div>
      <div className="p-2">
        <TreeButton
          selected={baselineSelected}
          icon={<Users />}
          label="全员基线"
          configured={baselineConfigured}
          draft={baselineDraft}
          onClick={onSelectBaseline}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto border-y border-manus-border px-2 py-2">
        {tree.map(node => (
          <DepartmentNode
            key={node.id}
            node={node}
            selectedOrgId={selectedOrgId}
            expandedOrgIds={expandedOrgIds}
            statusByOrgId={statusByOrgId}
            draftOrgIds={draftOrgIds}
            onSelect={onSelectOrganization}
            onToggle={onToggle}
          />
        ))}
      </div>
      <div className="p-2">
        <TreeButton selected={unassignedSelected} icon={<Inbox />} label="待分配账号" onClick={onSelectUnassigned} />
      </div>
    </div>
  )
}

function DepartmentNode({ node, selectedOrgId, expandedOrgIds, statusByOrgId, draftOrgIds, onSelect, onToggle }: {
  node: OrgTreeNode
  selectedOrgId: number | null
  expandedOrgIds: Set<number>
  statusByOrgId: Map<number, SemanticAccessTargetSummary>
  draftOrgIds: Set<number>
  onSelect: (id: number) => void
  onToggle: (id: number) => void
}) {
  const expanded = expandedOrgIds.has(node.id)
  const configured = Boolean(statusByOrgId.get(node.id)?.configured)
  return (
    <div>
      <div className={cn(
        'group flex min-h-9 items-center rounded-md transition-colors',
        selectedOrgId === node.id ? 'bg-accent/12 text-manus-text' : 'text-manus-muted hover:bg-manus-hover',
      )} style={{ paddingLeft: `${Math.min(node.level, 6) * 14}px` }}>
        <button
          type="button"
          aria-label={expanded ? `收起${node.name}` : `展开${node.name}`}
          onClick={() => node.children.length && onToggle(node.id)}
          className="flex h-8 w-7 items-center justify-center"
        >
          {node.children.length ? (expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />) : <span className="w-3.5" />}
        </button>
        <button onClick={() => onSelect(node.id)} className="flex min-w-0 flex-1 items-center gap-2 py-2 text-left text-sm">
          <Building2 className="h-4 w-4 shrink-0" />
          <span className="truncate">{node.name}</span>
          <PolicyDot configured={configured} draft={draftOrgIds.has(node.id)} />
        </button>
      </div>
      {expanded && node.children.map(child => (
        <DepartmentNode key={child.id} node={child} selectedOrgId={selectedOrgId} expandedOrgIds={expandedOrgIds} statusByOrgId={statusByOrgId} draftOrgIds={draftOrgIds} onSelect={onSelect} onToggle={onToggle} />
      ))}
    </div>
  )
}

function TreeButton({ selected, icon, label, configured = false, draft = false, onClick }: {
  selected: boolean
  icon: ReactNode
  label: string
  configured?: boolean
  draft?: boolean
  onClick: () => void
}) {
  return (
    <button onClick={onClick} className={cn(
      'flex w-full items-center gap-2 rounded-md px-3 py-2.5 text-left text-sm transition-colors [&>svg]:h-4 [&>svg]:w-4',
      selected ? 'bg-accent/12 text-manus-text' : 'text-manus-muted hover:bg-manus-hover',
    )}>
      {icon}<span className="flex-1">{label}</span><PolicyDot configured={configured} draft={draft} />
    </button>
  )
}

function PolicyDot({ configured, draft = false }: { configured: boolean; draft?: boolean }) {
  const title = draft ? '首次权限草稿' : configured ? '已配置直接策略' : '未配置直接策略'
  return <span title={title} className={cn(
    'h-1.5 w-1.5 shrink-0 rounded-full',
    draft ? 'bg-amber-400' : configured ? 'bg-emerald-400' : 'bg-manus-border',
  )} />
}

function TargetHeader({ area, selectedOrg, targetTab, draftMode, actions, onSelectTab }: {
  area: Area
  selectedOrg: OrgUnit | null
  targetTab: TargetTab
  draftMode: boolean
  actions?: ReactNode
  onSelectTab: (tab: TargetTab) => void
}) {
  const title = area === 'baseline' ? '全员基线' : area === 'unassigned' ? '待分配账号' : selectedOrg?.name || '请选择部门'
  return (
    <div className="shrink-0 border-b border-manus-border px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-lg font-semibold">{title}</h2>
            {draftMode && <Pill tone="amber">首次权限草稿未发布</Pill>}
            {area === 'organization' && (
              <div className="flex w-fit rounded-lg border border-manus-border bg-manus-tertiary p-1">
                {([
                  ['org_unit', '部门权限', Building2],
                  ['position', '岗位', BriefcaseBusiness],
                  ['user', '账号', UserRound],
                ] as const).map(([id, label, Icon]) => (
                  <button key={id} onClick={() => onSelectTab(id)} className={cn(
                    'flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors',
                    targetTab === id ? 'bg-manus-secondary text-manus-text shadow-sm' : 'text-manus-muted hover:text-manus-text',
                  )}><Icon className="h-3.5 w-3.5" />{label}</button>
                ))}
              </div>
            )}
          </div>
          {area === 'unassigned' && (
            <p className="mt-1 text-xs text-manus-muted">
              没有当前有效任职的账号
            </p>
          )}
        </div>
        {actions && (
          <div className="flex flex-wrap items-center justify-end gap-2">
            {actions}
          </div>
        )}
      </div>
    </div>
  )
}

function TargetList({ area, targetTab, targets, selectedTarget, search, loading, onSearch, onSubmitSearch, onSelect }: {
  area: Area
  targetTab: TargetTab
  targets: SemanticAccessTargetSummary[]
  selectedTarget: SemanticAccessTargetSummary | null
  search: string
  loading: boolean
  onSearch: (value: string) => void
  onSubmitSearch: () => void
  onSelect: (target: SemanticAccessTargetSummary) => void
}) {
  const showList = area === 'unassigned' || (area === 'organization' && targetTab !== 'org_unit')
  if (!showList) return null
  return (
    <aside className="min-h-[150px] border-b border-manus-border bg-manus/25 xl:min-h-0 xl:overflow-y-auto xl:border-b-0">
      <>
          <form className="relative border-b border-manus-border p-3" onSubmit={event => { event.preventDefault(); onSubmitSearch() }}>
            <Search className="absolute left-6 top-1/2 h-4 w-4 -translate-y-1/2 text-manus-muted" />
            <Input value={search} onChange={event => onSearch(event.target.value)} placeholder={targetTab === 'position' ? '搜索岗位' : '搜索账号或邮箱'} className="bg-manus-tertiary pl-9" />
          </form>
          <div className="space-y-1 p-2">
            {loading && <div className="flex items-center gap-2 px-3 py-4 text-sm text-manus-muted"><Loader2 className="h-4 w-4 animate-spin" />加载授权对象</div>}
            {!loading && targets.length === 0 && <p className="px-3 py-8 text-center text-sm text-manus-muted">暂无可配置对象</p>}
            {targets.map(target => (
              <button key={semanticTargetKey(target)} onClick={() => onSelect(target)} className={cn(
                'w-full rounded-lg border px-3 py-3 text-left transition-colors',
                selectedTarget && semanticTargetKey(selectedTarget) === semanticTargetKey(target)
                  ? 'border-accent/50 bg-accent/10'
                  : 'border-transparent hover:bg-manus-hover',
              )}>
                <div className="flex items-start gap-2">
                  <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-manus-tertiary text-manus-muted">
                    {target.target_type === 'position' ? <BriefcaseBusiness className="h-3.5 w-3.5" /> : <UserRound className="h-3.5 w-3.5" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{target.label}</div>
                    <div className="mt-1 truncate text-[11px] text-manus-muted">{target.email || `影响 ${target.affected_user_count} 个账号`}</div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      <TargetStatus target={target} configured={target.configured} />
                      {target.target_type === 'user' && <Pill tone="blue">账号全局策略</Pill>}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
      </>
    </aside>
  )
}

function PolicyEditor({ tables, columns, metrics, organizations, target, selectedTableId, currentTable, rule, activeRules, effectiveRules, candidates, draftMode, canManage, canManageWorkspace, highRiskTableIds, sensitiveTableIds, confirmationsByTable, inheritedEvidenceReviewsByTable, confirmation, dirty, confirmBusy, similarSuggestionLoading, onSelectTable, onUpdateRule, onRestoreInherited, onSaveTable }: {
  tables: SemanticTable[]
  columns: SemanticColumn[]
  organizations: OrgUnit[]
  metrics: SemanticMetric[]
  target: SemanticAccessTargetSummary
  selectedTableId: number
  currentTable?: SemanticTable
  rule?: SemanticAccessTableRule
  activeRules: SemanticAccessTableRule[]
  effectiveRules: SemanticEffectiveTableRule[]
  candidates: SemanticAccessBootstrapCandidate[]
  draftMode: boolean
  canManage: boolean
  canManageWorkspace: boolean
  highRiskTableIds: Set<number>
  sensitiveTableIds: Set<number>
  confirmationsByTable: Map<number, EvidenceConfirmationSummary>
  inheritedEvidenceReviewsByTable: Map<number, InheritedEvidenceReview>
  confirmation?: EvidenceConfirmationSummary
  dirty: boolean
  confirmBusy: boolean
  similarSuggestionLoading: boolean
  onSelectTable: (id: number) => void
  onUpdateRule: (patch: Partial<SemanticAccessTableRule>) => void
  onRestoreInherited: () => void
  onSaveTable: () => Promise<boolean>
}) {
  const [reviewOnly, setReviewOnly] = useState(false)
  const [tableDetailOpen, setTableDetailOpen] = useState(false)
  const [fieldDetailsOpen, setFieldDetailsOpen] = useState(true)
  const candidateByTable = new Map(candidates.map(item => [item.table_id, item]))
  const currentCandidate = currentTable ? candidateByTable.get(currentTable.id) : undefined
  const currentTableIsHighRisk = Boolean(
    currentTable && highRiskTableIds.has(currentTable.id),
  )
  const currentTableIsSensitive = Boolean(
    currentTable && sensitiveTableIds.has(currentTable.id),
  )
  const currentEffectiveRule = currentTable
    ? effectiveRules.find(item => item.table_id === currentTable.id)
    : undefined
  const displayedRule = rule || currentEffectiveRule
  const currentNeedsReview = authorizationNeedsReview(displayedRule?.decision, confirmation)
  const currentConfidence = Math.round((currentCandidate?.confidence || 0) * 100)
  const visibleSensitiveFields = columns.filter(column => (
    column.is_sensitive
    && displayedRule?.decision === 'visible'
    && !(displayedRule.hidden_column_ids || []).includes(column.id)
  ))
  const currentReviewReason = currentNeedsReview
    ? currentCandidate?.requires_mapping || (
        currentCandidate?.row_scope_suggestion && (rule?.row_scope?.type || 'all') === 'all'
      )
      ? '行范围尚未明确'
      : visibleSensitiveFields.length > 0
        ? '包含可见敏感字段'
        : isLowEvidenceConfidence(currentCandidate?.confidence)
          ? '授权判断置信度偏低'
          : currentTableIsHighRisk || currentTableIsSensitive
            ? '高风险授权需确认'
            : '授权建议待确认'
    : displayedRule?.decision === 'hidden'
      ? '当前为不可见，无需授权复核'
      : confirmation?.tableConfirmed
        ? '当前授权已经确认'
        : '未发现待处理的授权风险'
  const tableRows = tables.map(table => {
    const {
      rule: tableRule,
      direct,
      effectiveRule,
    } = persistedSemanticAccessTableRule(table.id, activeRules, effectiveRules)
    const candidate = candidateByTable.get(table.id)
    const inheritedReview = direct
      ? undefined
      : inheritedEvidenceReviewsByTable.get(table.id)
    const tableConfirmation = confirmationsByTable.get(table.id)
      || inheritedReview?.confirmation
    return {
      table,
      rule: tableRule,
      direct,
      effectiveRule,
      candidate,
      needsReview: authorizationNeedsReview(tableRule?.decision, tableConfirmation),
      confirmed: Boolean(tableConfirmation?.tableConfirmed),
      inheritedReviewSourceLabel: inheritedReview?.sourceLabel,
    }
  })
  const visibleRows = tableRows.filter(item => (
    item.rule?.decision === 'visible' && (!reviewOnly || item.needsReview)
  ))
  const unavailableRows = tableRows.filter(item => (
    item.rule?.decision !== 'visible' && (!reviewOnly || item.needsReview)
  ))
  const openTableDetail = (tableId: number) => {
    onSelectTable(tableId)
    setFieldDetailsOpen(true)
    setTableDetailOpen(true)
  }
  const saveTableDetail = async () => {
    if (await onSaveTable()) setTableDetailOpen(false)
  }

  const renderTableRows = (items: typeof tableRows) => (
    <div className="overflow-hidden rounded-xl border border-manus-border">
      <div className="hidden grid-cols-[minmax(0,1.25fr)_minmax(130px,.75fr)_120px_minmax(145px,.8fr)_18px] gap-3 border-b border-manus-border bg-manus-tertiary px-4 py-2 text-[11px] font-medium text-manus-muted xl:grid">
        <span>数据资产</span>
        <span>当前部门决策</span>
        <span>风险</span>
        <span>复核状态</span>
        <span />
      </div>
      {items.map(({ table, rule: tableRule, direct, effectiveRule, candidate, needsReview, confirmed, inheritedReviewSourceLabel }) => {
        const confidence = Math.round((candidate?.confidence || 0) * 100)
        const highRisk = highRiskTableIds.has(table.id)
        const sensitive = sensitiveTableIds.has(table.id)
        const reviewReason = needsReview
          ? inheritedReviewSourceLabel
            ? `${inheritedReviewSourceLabel}授权待确认`
            : candidate?.requires_mapping || candidate?.row_scope_suggestion
            ? '行范围尚未明确'
            : isLowEvidenceConfidence(candidate?.confidence)
              ? '置信度偏低'
              : sensitive
                ? '敏感授权待确认'
                : highRisk
                  ? '高风险授权待确认'
                  : '授权建议待确认'
          : confirmed
            ? '已确认'
            : tableRule?.decision === 'hidden'
              ? '无需复核'
              : '无待办'
        return (
          <button
            key={table.id}
            type="button"
            aria-pressed={selectedTableId === table.id}
            onClick={() => openTableDetail(table.id)}
            className={cn(
              'grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-manus-border px-4 py-3 text-left transition-colors last:border-b-0 xl:grid-cols-[minmax(0,1.25fr)_minmax(130px,.75fr)_120px_minmax(145px,.8fr)_18px]',
              selectedTableId === table.id ? 'bg-emerald-500/7' : 'hover:bg-manus-hover/60',
            )}
          >
            <span className="min-w-0">
              <b className="block truncate text-sm font-medium">{table.business_name}</b>
              <span className="mt-0.5 block truncate font-mono text-[11px] text-manus-muted">{table.physical_name}</span>
            </span>
            <span className={cn(
              'justify-self-end text-xs font-medium xl:justify-self-start',
              tableRule?.decision === 'visible'
                ? 'text-emerald-700 dark:text-emerald-300'
                : 'text-manus-muted',
            )}>
              {tableRule?.decision === 'visible'
                ? '可见'
                : '不可见'}
              <span className="ml-1 font-normal text-manus-muted">
                · {semanticAccessDecisionOriginLabel(
                  target.target_type,
                  direct,
                  effectiveRule?.sources,
                )}
              </span>
              {draftMode && candidate ? ` · 置信度${confidence}%` : ''}
            </span>
            <span className="hidden items-center gap-1.5 text-xs xl:flex">
              {(highRisk || sensitive) ? (
                <>
                  <ShieldCheck className="h-3.5 w-3.5 text-amber-500" />
                  <span className="text-amber-700 dark:text-amber-300">
                    {sensitive ? '敏感' : '高风险'}
                  </span>
                </>
              ) : <span className="text-manus-muted">一般</span>}
            </span>
            <span className="hidden items-center gap-2 text-xs xl:flex">
              <span className={cn(
                needsReview
                  ? 'text-orange-700 dark:text-orange-300'
                  : confirmed
                    ? 'text-emerald-700 dark:text-emerald-300'
                    : 'text-manus-muted',
              )}>{reviewReason}</span>
              {needsReview && (
                <span className="rounded-full bg-orange-500/12 px-2 py-0.5 text-[10px] font-medium text-orange-700 dark:text-orange-300">
                  需复核
                </span>
              )}
            </span>
            <ChevronRight className="hidden h-4 w-4 text-manus-muted xl:block" />
          </button>
        )
      })}
      {items.length === 0 && (
        <div className="px-4 py-8 text-center text-sm text-manus-muted">当前筛选下没有数据资产</div>
      )}
    </div>
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-4 lg:p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold">{target.label}数据资产</h3>
            <p className="mt-1 text-xs text-manus-muted">优先核对当前部门的访问决定，再查看数据本身的风险。</p>
          </div>
          {draftMode && (
            <label className="flex items-center gap-2 text-xs text-manus-muted">
              <Switch checked={reviewOnly} onCheckedChange={setReviewOnly} />
              仅看需复核
            </label>
          )}
        </div>
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-sm font-medium">{target.label}可见资产</h4>
            <span className="text-xs text-manus-muted">{visibleRows.length} 张</span>
          </div>
          {renderTableRows(visibleRows)}
        </section>
        <section className="mt-5">
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-sm font-medium">{target.label}不可见资产</h4>
            <span className="text-xs text-manus-muted">{unavailableRows.length} 张</span>
          </div>
          {renderTableRows(unavailableRows)}
        </section>
      </div>
      <Dialog
        open={tableDetailOpen && Boolean(currentTable)}
        onOpenChange={open => {
          if (!open && (confirmBusy || similarSuggestionLoading)) return
          setTableDetailOpen(open)
        }}
      >
        <DialogContent className="flex h-[min(92vh,900px)] w-[min(96vw,1200px)] max-w-none flex-col gap-0 overflow-hidden border-manus-border bg-manus-secondary p-0 shadow-2xl">
          <DialogHeader className="shrink-0 border-b border-manus-border bg-manus-elevated px-6 py-4 pr-14">
            <div className="flex flex-wrap items-center gap-3">
              <DialogTitle className="text-xl text-manus-text">{currentTable?.business_name || '表权限详情'}</DialogTitle>
              <Pill tone={displayedRule?.decision === 'visible' ? 'green' : 'neutral'}>
                {displayedRule?.decision === 'visible' ? '可见' : '不可见'}
              </Pill>
              {confirmation?.tableConfirmed && (
                <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 dark:text-emerald-300"><Check className="h-3.5 w-3.5" />已确认</span>
              )}
            </div>
            <DialogDescription className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-mono">{currentTable?.physical_name}</span>
              <span aria-hidden="true">·</span>
              <span>{target.label}的访问设置</span>
            </DialogDescription>
          </DialogHeader>
          <div className="grid min-h-0 flex-1 overflow-hidden lg:grid-cols-[330px_minmax(0,1fr)]">
        {currentTable ? (
          <>
            <aside className="min-h-0 overflow-y-auto border-b border-manus-border bg-manus/20 p-5 lg:border-b-0 lg:border-r">
            <div className="rounded-xl border border-manus-border bg-manus-elevated p-4">
              <div className="flex flex-wrap items-center gap-2">
                {draftMode && currentCandidate && (
                  <div className="flex w-full items-center justify-between gap-3">
                    <span className="text-xs text-manus-muted">系统建议置信度</span>
                    <b className="text-lg tabular-nums">{currentConfidence}%</b>
                  </div>
                )}
                {currentNeedsReview && (
                  <span className="rounded-full bg-orange-500/12 px-2 py-0.5 text-xs font-medium text-orange-700 dark:text-orange-300">需复核</span>
                )}
              </div>
              <div className={cn('text-xs text-manus-muted', draftMode && currentCandidate ? 'mt-4' : '')}>授权判断</div>
              <div className="mt-1 text-sm font-medium">{currentReviewReason}</div>
              {draftMode && currentCandidate && (
                <p className="mt-2 text-xs leading-5 text-manus-muted">{currentCandidate.reason || '未提供额外依据说明'}</p>
              )}
            </div>

            <div className={cn(
              'mt-3 rounded-xl border px-3 py-3 text-xs',
              currentTableIsHighRisk || currentTableIsSensitive
                ? 'border-amber-500/30 bg-amber-500/7'
                : 'border-manus-border bg-manus-tertiary',
            )}>
              <div className="flex items-center gap-2 font-medium">
                <ShieldCheck className={cn(
                  'h-4 w-4',
                  currentTableIsHighRisk || currentTableIsSensitive ? 'text-amber-600' : 'text-manus-muted',
                )} />
                风险：{currentTableIsSensitive ? '敏感' : currentTableIsHighRisk ? '高' : '一般'}
              </div>
              <p className="mt-1.5 leading-5 text-manus-muted">
                {currentTableIsSensitive
                  ? '包含敏感字段，授权错误可能扩大数据暴露范围。'
                  : currentTableIsHighRisk
                    ? '核心业务数据，授权错误影响较大。'
                    : '未发现需要单独提示的高风险特征。'}
              </p>
            </div>
            <div className="mt-4 rounded-xl border border-manus-border bg-manus-elevated p-4 text-xs leading-5 text-manus-muted">
              <div className="mb-1 font-medium text-manus-text">配置提示</div>
              先确定整表访问，再限制行范围与字段。明确拒绝始终优先于其他授权。
            </div>
            </aside>

            <section className="min-h-0 overflow-y-auto p-5 lg:p-6">
            <div className="rounded-xl border border-manus-border bg-manus-elevated p-4">
              <div className="grid gap-4 xl:grid-cols-[250px_minmax(0,1fr)]">
              <div>
                <div className="mb-2 text-xs font-medium text-manus-muted">访问决定</div>
                <DecisionSelector
                  value={target.target_type === 'baseline'
                    ? rule?.decision || displayedRule?.decision || 'hidden'
                    : rule?.decision || 'follow'}
                  allowFollow={target.target_type !== 'baseline'}
                  disabled={!canManage}
                  onChange={decision => {
                    if (decision === 'follow') onRestoreInherited()
                    else onUpdateRule({ decision })
                  }}
                />
                {dirty && (
                  <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
                    当前调整尚未生效，请点击“保存修改”。
                  </p>
                )}
              </div>

              {displayedRule?.decision === 'visible' && (
                <RowScopeEditor
                  compact
                  value={displayedRule.row_scope}
                  target={target}
                  organizations={organizations}
                  columns={columns}
                  canManageWorkspace={canManageWorkspace}
                  disabled={!canManage || !rule}
                  onChange={row_scope => onUpdateRule({ row_scope })}
                />
              )}
              </div>

              <details
                open={fieldDetailsOpen}
                onToggle={event => setFieldDetailsOpen(event.currentTarget.open)}
                className="group mt-4 border-t border-manus-border pt-4"
              >
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-xs">
                  <span className="font-medium text-manus-text">可见字段</span>
                  <span className="flex items-center gap-1 text-manus-muted">
                    {displayedRule?.decision === 'visible'
                      ? columns.length - (displayedRule.hidden_column_ids || []).length
                      : 0}/{columns.length}
                    <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
                  </span>
                </summary>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {columns.map(column => {
                    const fieldCandidate = currentCandidate?.field_suggestions?.find(
                      item => item.column_id === column.id,
                    )
                    const followsHiddenTable = displayedRule?.decision !== 'visible'
                    const hidden = followsHiddenTable || (displayedRule?.hidden_column_ids || []).includes(column.id)
                    return (
                      <button
                        key={column.id}
                        type="button"
                        disabled={!canManage || !rule || rule.decision !== 'visible'}
                        onClick={() => onUpdateRule({
                          hidden_column_ids: hidden
                            ? (rule?.hidden_column_ids || []).filter(id => id !== column.id)
                            : [...(rule?.hidden_column_ids || []), column.id],
                        })}
                        className={cn(
                          'flex min-h-12 w-full items-center justify-between rounded-lg border px-3 py-2 text-left transition-colors disabled:opacity-45',
                          hidden
                            ? 'border-manus-border bg-manus-tertiary/55 hover:bg-manus-hover'
                            : 'border-emerald-500/20 bg-emerald-500/5 hover:bg-emerald-500/10',
                        )}
                      >
                        <span className="min-w-0">
                          <b className="block truncate text-xs font-medium">{column.business_name}</b>
                          <code className="text-[10px] text-manus-muted">{column.physical_name}</code>
                        </span>
                        <span className="ml-2 flex shrink-0 items-center gap-1.5">
                          {column.is_sensitive && <Pill tone="red">敏感</Pill>}
                          {isLowEvidenceConfidence(fieldCandidate?.confidence) && <Pill tone="amber">低置信度</Pill>}
                          {hidden ? <EyeOff className="h-3.5 w-3.5 text-manus-muted" /> : <Eye className="h-3.5 w-3.5 text-emerald-500" />}
                        </span>
                      </button>
                    )
                  })}
                </div>
              </details>
              {metrics.length > 0 && (
                <details className="group mt-4 border-t border-manus-border pt-4">
                  <summary className="flex cursor-pointer list-none items-center justify-between text-xs">
                    <span className="font-medium">可见指标</span>
                    <span className="flex items-center gap-1 text-manus-muted">
                    {displayedRule?.decision === 'visible'
                        ? metrics.length - (displayedRule.hidden_metric_ids || []).length
                        : 0}/{metrics.length}
                      <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
                    </span>
                  </summary>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {metrics.map(metric => {
                      const hidden = displayedRule?.decision !== 'visible' || (displayedRule?.hidden_metric_ids || []).includes(metric.id)
                      return (
                        <button key={metric.id} type="button" disabled={!canManage || rule?.decision !== 'visible'} onClick={() => onUpdateRule({
                          hidden_metric_ids: hidden
                            ? (rule?.hidden_metric_ids || []).filter(id => id !== metric.id)
                            : [...(rule?.hidden_metric_ids || []), metric.id],
                        })} className="flex min-h-11 w-full items-center justify-between rounded-lg border border-manus-border bg-manus-tertiary/55 px-3 py-2 text-left text-xs hover:bg-manus-hover disabled:opacity-45">
                          <span className="font-medium">{metric.business_name}</span>
                          {hidden ? <EyeOff className="h-3.5 w-3.5 text-manus-muted" /> : <Eye className="h-3.5 w-3.5 text-emerald-500" />}
                        </button>
                      )
                    })}
                  </div>
                </details>
              )}
            </div>

            {canManage && (
              (draftMode && Boolean(confirmation?.tablePendingItems))
              || dirty
              || confirmBusy
              || similarSuggestionLoading
            ) && (
              <Button
                className="sticky bottom-0 mt-4 w-full bg-emerald-500 text-white shadow-[0_-8px_24px_rgba(0,0,0,0.08)] hover:bg-emerald-600"
                onClick={() => void saveTableDetail()}
                disabled={!canManage || (draftMode && !canManageWorkspace) || confirmBusy || similarSuggestionLoading}
              >
                {(confirmBusy || similarSuggestionLoading)
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Check className="h-4 w-4" />}
                {similarSuggestionLoading
                  ? '正在查找相似修改…'
                  : confirmBusy
                    ? '保存中'
                    : draftMode && (confirmation?.tablePendingItems || 0) > 0
                      ? '确认并立即生效'
                      : '保存修改'}
              </Button>
            )}
            </section>
          </>
        ) : <CenteredState icon={<ShieldCheck />} title="当前数据源没有语义表" />}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function DecisionSelector({ value, allowFollow, disabled, onChange }: {
  value: 'visible' | 'hidden' | 'follow'
  allowFollow: boolean
  disabled: boolean
  onChange: (value: 'visible' | 'hidden' | 'follow') => void
}) {
  return (
    <div className="flex rounded-lg border border-manus-border bg-manus-tertiary p-1">
      {([
        ['visible', '可见'],
        ['hidden', '不可见'],
        ...(allowFollow ? [['follow', '跟随'] as const] : []),
      ] as const).map(([id, label]) => (
        <button key={label} disabled={disabled} onClick={() => onChange(id)} className={cn(
          'rounded-md px-3 py-1.5 text-sm transition-colors disabled:opacity-50',
          value === id && (id === 'visible'
            ? 'bg-emerald-500/18 text-emerald-300'
            : id === 'hidden'
              ? 'bg-red-500/18 text-red-300'
              : 'bg-manus-secondary text-manus-text'),
        )}>{label}</button>
      ))}
    </div>
  )
}

function RowScopeEditor({ value, target, organizations, columns, canManageWorkspace, compact = false, disabled, onChange }: {
  value?: SemanticAccessRowScope
  target: SemanticAccessTargetSummary
  organizations: OrgUnit[]
  columns: SemanticColumn[]
  canManageWorkspace: boolean
  compact?: boolean
  disabled: boolean
  onChange: (scope: SemanticAccessRowScope) => void
}) {
  const defaultType = target.target_type === 'baseline' ? 'all' : target.target_type === 'user' ? 'self' : 'target_org'
  const scope = value || ({ type: defaultType } as SemanticAccessRowScope)
  const options: Array<[SemanticAccessRowScope['type'], string]> = [
    ['self', '本人数据'],
  ]
  if (target.target_type !== 'baseline') {
    options.push(['target_org', target.target_type === 'user' ? '主任职部门' : '目标部门'])
    options.push(['target_org_tree', '目标部门及下级'])
  }
  if (target.target_type === 'user') options.push(['all_assignments', '全部有效任职部门'])
  options.push(['custom_org', '自定义部门'])
  options.push(['custom', '自定义条件'])
  if (canManageWorkspace) options.unshift(['all', '全部数据'])

  const custom = scope.type === 'custom'
    ? scope.condition as ConditionRule
    : { column_id: columns[0]?.id || 0, operator: '=', value: '' }
  const customOrgIds = scope.type === 'custom_org' ? scope.org_unit_ids : []

  return (
    <div className={cn(
      compact
        ? 'min-w-0'
        : 'mt-5 rounded-xl border border-manus-border bg-manus/25 p-4',
    )}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h4 className={cn('font-medium', compact ? 'text-xs' : 'text-sm')}>行数据范围</h4>{!compact && <p className="mt-1 text-xs text-manus-muted">授权对象与可查看的数据组织范围相互独立。</p>}</div>
        <NativeSelect value={scope.type} disabled={disabled} onChange={type => {
          if (type === 'custom_org') onChange({ type: 'custom_org', org_unit_ids: [], include_descendants: false })
          else if (type === 'custom') onChange({ type: 'custom', condition: custom })
          else onChange({ type } as SemanticAccessRowScope)
        }} ariaLabel="行数据范围">
          {options.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </NativeSelect>
      </div>
      {scope.type === 'custom_org' && (
        <div className="mt-4 space-y-3">
          <select multiple value={customOrgIds.map(String)} disabled={disabled} onChange={event => onChange({
            ...scope,
            org_unit_ids: Array.from(event.target.selectedOptions).map(option => Number(option.value)),
          })} className="min-h-32 w-full rounded-md border border-manus-border bg-manus-tertiary p-2 text-sm outline-none">
            {organizations.map(org => <option key={org.id} value={org.id}>{'　'.repeat(Math.min(org.level, 5))}{org.name}</option>)}
          </select>
          <label className="flex items-center justify-between rounded-md border border-manus-border px-3 py-2 text-sm"><span>包含所选部门的下级部门</span><Switch checked={scope.include_descendants} disabled={disabled} onCheckedChange={include_descendants => onChange({ ...scope, include_descendants })} /></label>
        </div>
      )}
      {scope.type === 'custom' && (
        <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_110px_1fr]">
          <NativeSelect value={String(custom.column_id)} disabled={disabled} onChange={columnId => onChange({ type: 'custom', condition: { ...custom, column_id: Number(columnId) } })} ariaLabel="条件字段">
            {columns.map(column => <option key={column.id} value={column.id}>{column.business_name}</option>)}
          </NativeSelect>
          <NativeSelect value={custom.operator} disabled={disabled} onChange={operator => onChange({ type: 'custom', condition: { ...custom, operator } })} ariaLabel="条件运算符">
            {['=', '!=', 'in', 'not in', '>', '>=', '<', '<='].map(operator => <option key={operator}>{operator}</option>)}
          </NativeSelect>
          <Input disabled={disabled} value={Array.isArray(custom.value) ? custom.value.join(',') : String(custom.value ?? '')} onChange={event => onChange({ type: 'custom', condition: { ...custom, value: ['in', 'not in'].includes(custom.operator) ? event.target.value.split(',').map(item => item.trim()).filter(Boolean) : event.target.value } })} placeholder="条件值" />
        </div>
      )}
    </div>
  )
}

function MappingEditor({ table, columns, mapping, canEdit, busy, onChange, onSave }: {
  table: SemanticTable
  columns: SemanticColumn[]
  mapping: Mapping
  canEdit: boolean
  busy: boolean
  onChange: (mapping: Mapping) => void
  onSave: () => void
}) {
  return (
    <div className="mt-3 space-y-3 rounded-lg border border-manus-border bg-manus-tertiary p-3">
      <div><b className="text-sm">{table.business_name}</b><p className="text-xs text-manus-muted">组织和本人范围保存前必须映射到真实字段。</p></div>
      <Field label="组织归属字段"><NativeSelect value={mapping.org_column_id == null ? '' : String(mapping.org_column_id)} disabled={!canEdit} onChange={value => onChange({ ...mapping, org_column_id: value ? Number(value) : null })} ariaLabel="组织归属字段"><option value="">未配置</option>{columns.map(column => <option key={column.id} value={column.id}>{column.business_name}</option>)}</NativeSelect></Field>
      <Field label="组织字段取值"><NativeSelect value={mapping.org_value_kind} disabled={!canEdit} onChange={value => onChange({ ...mapping, org_value_kind: value as 'id' | 'code' | 'external' })} ariaLabel="组织字段取值"><option value="id">部门 ID</option><option value="code">部门编码</option><option value="external" disabled>外部组织值映射（首次配置审核维护）</option></NativeSelect></Field>
      <Field label="人员归属字段"><NativeSelect value={mapping.user_column_id == null ? '' : String(mapping.user_column_id)} disabled={!canEdit} onChange={value => onChange({ ...mapping, user_column_id: value ? Number(value) : null })} ariaLabel="人员归属字段"><option value="">未配置</option>{columns.map(column => <option key={column.id} value={column.id}>{column.business_name}</option>)}</NativeSelect></Field>
      <Field label="人员字段取值"><NativeSelect value={mapping.user_value_kind} disabled={!canEdit} onChange={value => onChange({ ...mapping, user_value_kind: value as 'id' | 'username' })} ariaLabel="人员字段取值"><option value="id">账号 ID</option><option value="username">登录名</option></NativeSelect></Field>
      {canEdit ? <Button size="sm" variant="outline" className="w-full" onClick={onSave} disabled={busy}>{busy && <Loader2 className="h-4 w-4 animate-spin" />}保存归属映射</Button> : <p className="text-xs text-amber-300">需要工作区级问数权限管理能力。</p>}
    </div>
  )
}

function EvidenceWorkspace({
  area, selectedOrg, tables, columns, organizations, mappings, evidence, readiness, filter, busy,
  focusTableId, canManage, onFilter, onReview, onUpdateRelation, onConfirmTarget, onOpenMappings,
  onRetry,
}: {
  area: Area
  selectedOrg: OrgUnit | null
  tables: SemanticTable[]
  columns: SemanticColumn[]
  organizations: OrgUnit[]
  mappings: Record<number, Mapping>
  evidence: EvidencePayload
  readiness: EvidenceReadiness | null
  focusTableId: number
  filter: 'all' | 'pending' | 'restricted' | 'sensitive' | 'mapping'
  busy: string
  canManage: boolean
  onFilter: (value: 'all' | 'pending' | 'restricted' | 'sensitive' | 'mapping') => void
  onReview: (decisions: Array<{
    kind: 'asset' | 'relation'
    asset_id?: number
    relation_id?: number
    action: 'accepted' | 'modified' | 'rejected'
    baseline_access?: 'workspace_visible' | 'controlled'
    requires_individual_review?: boolean
    access_class?: EvidenceAsset['access_class']
    reason?: string
  }>) => Promise<void>
  onUpdateRelation: (relation: EvidenceRelation, patch: Partial<EvidenceRelation>) => Promise<void>
  onConfirmTarget: (mode: 'table' | 'ordinary_target', tableId?: number) => void
  onOpenMappings: (tableId: number) => void
  onRetry: () => void
}) {
  const tableById = new Map(tables.map(item => [item.id, item]))
  const columnById = new Map(columns.map(item => [item.id, item]))
  const topOrgId = selectedOrg
    ? selectedOrg.level === 0
      ? selectedOrg.id
      : Number(selectedOrg.ancestors.split('/').filter(Boolean)[0])
    : null
  const assets = evidence.assets || []
  const relations = evidence.relations || []
  const tableAssets = assets.filter(item => item.asset_type === 'table')
  const topOrganizations = organizations.filter(item => item.parent_id == null && item.status)
  const missingMapping = (relation: EvidenceRelation) => {
    const type = relation.row_scope?.type || 'all'
    if (['target_org', 'target_org_tree', 'primary_assignment', 'all_assignments', 'custom_org'].includes(type)) {
      return !mappings[relation.table_id]?.org_column_id
    }
    if (type === 'self') return !mappings[relation.table_id]?.user_column_id
    return false
  }
  const relevantRelations = relations.filter(item => (
    area === 'baseline'
      ? false
      : topOrgId != null && item.org_unit_id === topOrgId
  ))
  const visibleTableAssets = tableAssets.filter(asset => {
    if (!tableById.has(asset.table_id)) return false
    const tableRelations = relevantRelations.filter(item => item.table_id === asset.table_id)
    if (area !== 'baseline' && !tableRelations.length) return false
    if (filter === 'pending') {
      const publicPending = evidenceBaselineAccess(asset) === 'workspace_visible'
        && !isManuallyApproved(asset.review_status)
      const grantPending = tableRelations.some(item => (
        item.asset_type === 'table'
          ? evidenceAccessLevel(item) !== 'hidden' && !isManuallyApproved(item.review_status)
          : evidenceFieldDecision(item) === 'visible' && !isManuallyApproved(item.review_status)
      ))
      return area === 'baseline' ? publicPending : grantPending
    }
    if (filter === 'restricted') return evidenceRequiresIndividualReview(asset)
    if (filter === 'sensitive') return asset.is_sensitive || tableRelations.some(item => (
      item.asset_type === 'column' && columnById.get(item.asset_id)?.is_sensitive
    ))
    if (filter === 'mapping') return tableRelations.some(missingMapping)
    return true
  })
  const ordinaryPendingCount = area === 'baseline'
    ? tableAssets.filter(asset => (
        evidenceBaselineAccess(asset) === 'workspace_visible'
        && asset.review_status === 'pending'
        && !evidenceRequiresIndividualReview(asset)
      )).length
    : relevantRelations.filter(item => {
        const asset = tableAssets.find(candidate => candidate.table_id === item.table_id)
        if (item.asset_type === 'table') return isBulkReviewableTableGrant(asset, item)
        return isBulkReviewableFieldGrant(
          asset,
          item,
          Boolean(columnById.get(item.asset_id)?.is_sensitive),
        )
      }).length
  const reviewSummary = evidence.set?.review_summary || evidence.set?.review_progress
  const progressEntries: Array<[string, { reviewed: number; total: number }]> = [
    ...(reviewSummary?.public_access ? [['public_access', reviewSummary.public_access] as [string, { reviewed: number; total: number }]] : []),
    ...(reviewSummary?.table_grants ? [['table_grants', reviewSummary.table_grants] as [string, { reviewed: number; total: number }]] : []),
    ...(reviewSummary?.field_grants ? [['field_grants', reviewSummary.field_grants] as [string, { reviewed: number; total: number }]] : []),
  ]
  const autoSafeCount = reviewSummary?.auto_safe_count || 0

  useEffect(() => {
    if (!focusTableId || !evidence.set) return
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(`evidence-table-${focusTableId}`)?.scrollIntoView({
        block: 'center',
        behavior: 'smooth',
      })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [area, evidence.set, focusTableId])

  if (!evidence.set) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <div className="mx-auto max-w-3xl space-y-4">
          <div className="rounded-xl border border-manus-border bg-manus-tertiary p-5">
            <div className="flex items-center gap-2"><Sparkles className="h-5 w-5 text-accent" /><h2 className="font-semibold">访问依据尚未生成</h2></div>
            <p className="mt-2 text-sm text-manus-muted">系统会在所有一级部门画像和可问数表字段语义确认后自动生成。</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-manus-border bg-manus-tertiary p-4">
              <div className="text-sm font-medium text-manus-text">企业业务事实</div>
              <div className="mt-3 flex items-end justify-between gap-2">
                <span className="text-2xl font-semibold text-manus-text">
                  {readiness?.business_context?.quality.required_completed || 0}/
                  {readiness?.business_context?.quality.required_total || 3}
                </span>
                <Pill tone={readiness?.business_context?.quality.level === 'sufficient' ? 'green' : 'neutral'}>
                  {readiness?.business_context?.quality.level === 'sufficient' ? '核心事实完整' : '非阻断项'}
                </Pill>
              </div>
              <p className="mt-2 text-xs text-manus-muted">
                {readiness?.business_context?.quality.level === 'sufficient'
                  ? '画像已获得三项建议业务事实'
                  : '画像可继续生成，但需重点审核AI假设'}
              </p>
            </div>
            <ProgressCard title="部门画像" reviewed={readiness?.department_profiles.confirmed || 0} total={readiness?.department_profiles.total || 0} />
            <ProgressCard
              title="表字段语义"
              reviewed={(readiness?.business_semantics.tables_total || 0) + (readiness?.business_semantics.columns_total || 0)
                - (readiness?.business_semantics.tables_unconfirmed.length || 0)
                - (readiness?.business_semantics.columns_unconfirmed.length || 0)}
              total={(readiness?.business_semantics.tables_total || 0) + (readiness?.business_semantics.columns_total || 0)}
            />
          </div>
          {(readiness?.blockers || []).map((item, index) => (
            <div key={`${item.code}:${index}`} className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              {evidenceBlockerLabel(item.code)}{item.items?.length ? `（${item.items.length} 项）` : ''}
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-manus-border px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-semibold">访问依据集 v{evidence.set.version}</h2>
              <Pill tone={evidence.set.status === 'published' ? 'green' : ['publish_failed', 'failed'].includes(evidence.set.status) ? 'red' : 'blue'}>
                {evidenceSetStatusLabel(evidence.set.status)}
              </Pill>
            </div>
            <p className="mt-1 text-xs text-manus-muted">
              {area === 'baseline' ? '设置全员可问范围与高风险逐项审核标记' : `审核 ${selectedOrg?.name || '当前部门'} 所属一级部门的逐表访问级别`}
            </p>
          </div>
          {canManage && evidence.set.status === 'failed' && (
            <Button variant="outline" disabled={busy === 'evidence-retry'} onClick={onRetry}>
              {busy === 'evidence-retry' && <Loader2 className="h-4 w-4 animate-spin" />}重试生成
            </Button>
          )}
          <div className="flex flex-wrap gap-2">
            {progressEntries.map(([key, value]) => (
              <span key={key} className="rounded-full bg-manus-tertiary px-2.5 py-1 text-xs text-manus-muted">
                {progressLabel(key)} {value.reviewed}/{value.total}
              </span>
            ))}
            <span className="rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-700 dark:text-emerald-300">
              已自动保持不可见 {autoSafeCount} 项
            </span>
          </div>
        </div>
        {evidence.set.error_message && <div className="mt-3 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-300">{evidence.set.error_message}</div>}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-manus-border px-4 py-3">
        {([
          ['all', '全部'], ['pending', '待审核'], ['restricted', '受限'],
          ['sensitive', '敏感'], ['mapping', '缺少行映射'],
        ] as const).map(([value, label]) => (
          <Button key={value} size="sm" variant={filter === value ? 'secondary' : 'ghost'} onClick={() => onFilter(value)}>{label}</Button>
        ))}
        {canManage && ordinaryPendingCount > 0 && (
          <Button size="sm" variant="outline" className="ml-auto" disabled={busy === 'evidence-target'} onClick={() => onConfirmTarget('ordinary_target')}>
            {busy === 'evidence-target' && <Loader2 className="h-4 w-4 animate-spin" />}
            一键确认当前{area === 'baseline' ? '基线' : '部门'}普通授权（{ordinaryPendingCount}）
          </Button>
        )}
      </div>
      <details className="shrink-0 border-b border-manus-border bg-manus/30">
        <summary className="cursor-pointer px-4 py-2.5 text-xs font-medium text-manus-muted">
          查看跨部门关系矩阵
        </summary>
        <div className="max-h-56 overflow-auto border-t border-manus-border">
          <table className="min-w-full text-xs">
            <thead className="sticky top-0 bg-manus-secondary">
              <tr>
                <th className="px-3 py-2 text-left font-medium">表</th>
                {topOrganizations.map(org => <th key={org.id} className="min-w-28 px-3 py-2 text-left font-medium">{org.name}</th>)}
              </tr>
            </thead>
            <tbody>
              {tableAssets.map(asset => (
                <tr key={asset.id} className="border-t border-manus-border">
                  <td className="px-3 py-2 font-medium">{tableById.get(asset.table_id)?.business_name || asset.table_id}</td>
                  {topOrganizations.map(org => {
                    const relation = relations.find(item => (
                      item.asset_type === 'table'
                      && item.table_id === asset.table_id
                      && item.org_unit_id === org.id
                    ))
                    const level = relation ? evidenceAccessLevel(relation) : 'hidden'
                    return (
                      <td key={org.id} className="px-3 py-2">
                        <span
                          className={relation?.review_status === 'pending' && level !== 'hidden' ? 'text-amber-700 dark:text-amber-300' : 'text-manus-muted'}
                          title={relation ? `建议依据：${relationRoleLabel(relation.business_role)}` : undefined}
                        >
                          {accessLevelLabel(level)}
                          {relation?.review_status === 'pending' && level !== 'hidden' ? ' · 待审' : ''}
                        </span>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {visibleTableAssets.length === 0 ? (
          <CenteredState icon={<Inbox />} title="当前筛选下没有访问依据" compact />
        ) : (
          <div className="space-y-3">
            {visibleTableAssets.map(asset => {
              const table = tableById.get(asset.table_id)
              const tableRelations = relevantRelations.filter(item => item.table_id === asset.table_id && item.asset_type === 'table')
              const fieldRelations = relevantRelations.filter(item => item.table_id === asset.table_id && item.asset_type === 'column')
              const pendingTableRelation = tableRelations.find(item => (
                evidenceAccessLevel(item) !== 'hidden'
                && !isManuallyApproved(item.review_status)
              ))
              const lowConfidence = tableRelations.some(item => (
                isLowEvidenceConfidence(item.confidence)
              ))
              return (
                <div
                  id={`evidence-table-${asset.table_id}`}
                  key={asset.id}
                  className={cn(
                    'rounded-xl border border-manus-border bg-manus-tertiary p-4',
                    focusTableId === asset.table_id && 'ring-1 ring-accent/50',
                  )}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="font-medium">{table?.business_name || `表 #${asset.table_id}`}</h3>
                        <Pill tone={evidenceBaselineAccess(asset) === 'workspace_visible' ? 'green' : 'neutral'}>
                          {evidenceBaselineAccess(asset) === 'workspace_visible' ? '全员可问' : '按授权开放'}
                        </Pill>
                        {evidenceRequiresIndividualReview(asset) && <Pill tone="red">高风险 · 逐项审核</Pill>}
                        {asset.is_sensitive && <Pill tone="red">敏感表</Pill>}
                        {lowConfidence && <Pill tone="amber">低置信度 &lt; 60%</Pill>}
                      </div>
                      <p className="mt-1 text-xs text-manus-muted">{asset.reason || table?.description || '未填写依据说明'}</p>
                    </div>
                    {canManage && area === 'baseline' && (
                      <EvidenceAssetActions
                        key={`${asset.id}:${asset.review_status}:${asset.baseline_access}:${asset.requires_individual_review}`}
                        asset={asset}
                        busy={busy === 'evidence-review'}
                        onReview={decision => void onReview([decision])}
                      />
                    )}
                    {canManage && area !== 'baseline' && pendingTableRelation && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy === `evidence-table:${asset.table_id}`}
                        onClick={() => onConfirmTarget('table', asset.table_id)}
                      >
                        {busy === `evidence-table:${asset.table_id}` && <Loader2 className="h-4 w-4 animate-spin" />}
                        确认本表
                      </Button>
                    )}
                  </div>
                  {area !== 'baseline' && tableRelations.map(relation => (
                    <EvidenceRelationRow
                      key={`${relation.id}:${relation.review_status}:${relation.access_level}:${relation.row_scope?.type}:${relation.override_reason || ''}`}
                      relation={relation}
                      highRisk={evidenceRequiresIndividualReview(asset)}
                      mapping={mappings[relation.table_id]}
                      busy={busy === `evidence-relation:${relation.id}` || busy === 'evidence-review'}
                      canManage={canManage}
                      onSave={patch => void onUpdateRelation(relation, patch)}
                      onOpenMappings={() => onOpenMappings(relation.table_id)}
                    />
                  ))}
                  {columns.some(column => column.table_id === asset.table_id) && (
                    <div className="mt-3 border-t border-manus-border pt-3">
                      <div className="mb-2 text-xs font-medium text-manus-muted">字段权限（未单独建议的字段随表权限）</div>
                      <div className="grid gap-2 lg:grid-cols-2">
                        {columns.filter(column => column.table_id === asset.table_id).map(column => {
                          const relation = fieldRelations.find(item => item.asset_id === column.id)
                          return relation ? (
                            <EvidenceFieldExceptionRow
                              key={`${relation.id}:${relation.review_status}:${relation.field_decision}:${relation.override_reason || ''}`}
                              label={column.business_name}
                              relation={relation}
                              sensitive={Boolean(column.is_sensitive)}
                              canManage={canManage}
                              busy={busy === `evidence-relation:${relation.id}` || busy === 'evidence-review'}
                              onReview={decision => void onReview([decision])}
                              onSave={patch => void onUpdateRelation(relation, patch)}
                            />
                          ) : (
                            <div key={column.id} className="flex items-center justify-between rounded-lg border border-manus-border/70 bg-manus px-3 py-3 text-xs">
                              <span className="min-w-0">
                                <b className="block truncate">{column.business_name}</b>
                                <code className="text-[10px] text-manus-muted">{column.physical_name}</code>
                              </span>
                              <span className="ml-3 shrink-0 text-manus-muted">随表权限</span>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function EvidenceFieldExceptionRow({ label, relation, sensitive, canManage, busy, onReview, onSave }: {
  label: string
  relation: EvidenceRelation
  sensitive: boolean
  canManage: boolean
  busy: boolean
  onReview: (decision: {
    kind: 'relation'
    relation_id: number
    action: 'accepted' | 'modified' | 'rejected'
    reason?: string
  }) => void
  onSave: (patch: Partial<EvidenceRelation>) => void
}) {
  const originalDecision = evidenceFieldDecision(relation)
  const [decision, setDecision] = useState<EvidenceFieldDecision>(originalDecision)
  const [overrideReason, setOverrideReason] = useState(relation.override_reason || '')
  const changed = decision !== originalDecision
  const requiresReason = changed && decision === 'visible' && originalDecision === 'hidden'
  return (
    <div className="rounded-lg border border-manus-border/70 bg-manus px-3 py-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{label}</span>
        {sensitive && <Pill tone="red">敏感</Pill>}
        <select
          value={decision}
          disabled={!canManage}
          onChange={event => setDecision(event.target.value as EvidenceFieldDecision)}
          className="rounded border border-manus-border bg-manus-tertiary px-2 py-1"
          aria-label={`${label}字段权限`}
        >
          <option value="hidden">隐藏</option>
          <option value="visible">可见</option>
        </select>
        <span className="text-manus-muted">
          {relation.review_status === 'auto_safe'
            ? '安全默认'
            : relation.review_status === 'pending'
              ? '待确认授权'
              : '已处理'}
        </span>
      </div>
      <details className="mt-2 text-manus-muted">
        <summary className="cursor-pointer font-medium">
          建议依据 · {relationRoleLabel(relation.business_role)} · 置信度 {Math.round((relation.confidence || 0) * 100)}%
        </summary>
        <p className="mt-1 leading-5">{relation.reason || '未提供业务依据'}</p>
      </details>
      {canManage && requiresReason && (
        <Input
          value={overrideReason}
          onChange={event => setOverrideReason(event.target.value)}
          placeholder="放开字段必须填写业务理由"
          className="mt-2"
        />
      )}
      {canManage && <div className="mt-2 flex justify-end gap-2">
        {!changed && relation.review_status === 'pending' && decision === 'visible' && (
          <Button
            size="sm"
            disabled={busy || (requiresReason && !overrideReason.trim())}
            onClick={() => onReview({
              kind: 'relation',
              relation_id: relation.id,
              action: 'accepted',
              reason: overrideReason,
            })}
          >
            确认字段授权
          </Button>
        )}
        {changed && (
          <Button
            size="sm"
            disabled={busy || (requiresReason && !overrideReason.trim())}
            onClick={() => onSave({
              field_decision: decision,
              override_reason: overrideReason,
              review_status: 'modified',
            })}
          >
            保存字段授权
          </Button>
        )}
      </div>}
    </div>
  )
}

function EvidenceAssetActions({ asset, busy, onReview }: {
  asset: EvidenceAsset
  busy: boolean
  onReview: (decision: {
    kind: 'asset'
    asset_id: number
    action: 'accepted' | 'modified' | 'rejected'
    baseline_access?: 'workspace_visible' | 'controlled'
    requires_individual_review?: boolean
    reason?: string
  }) => void
}) {
  const originalBaseline = evidenceBaselineAccess(asset)
  const originalRisk = evidenceRequiresIndividualReview(asset)
  const [baselineAccess, setBaselineAccess] = useState(originalBaseline)
  const [individualReview, setIndividualReview] = useState(originalRisk)
  const [reason, setReason] = useState('')
  const changed = baselineAccess !== originalBaseline || individualReview !== originalRisk
  const expands = (
    originalBaseline !== 'workspace_visible' && baselineAccess === 'workspace_visible'
    || originalRisk && !individualReview
  )
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Field label="全员访问">
        <NativeSelect
          value={baselineAccess}
          onChange={value => setBaselineAccess(value as 'workspace_visible' | 'controlled')}
          ariaLabel="全员访问"
        >
          <option value="controlled">仅按授权对象开放</option>
          <option value="workspace_visible" disabled={asset.is_sensitive}>全员可问</option>
        </NativeSelect>
      </Field>
      <label className="flex h-10 items-center gap-2 rounded-lg border border-manus-border bg-manus px-3 text-xs">
        <input
          type="checkbox"
          checked={individualReview}
          disabled={asset.is_sensitive}
          onChange={event => setIndividualReview(event.target.checked)}
          className="h-4 w-4 accent-amber-500"
        />
        高风险 · 需逐项审核
      </label>
      {changed && (
        <Input
          value={reason}
          onChange={event => setReason(event.target.value)}
          placeholder={expands ? '扩大访问必须填写理由' : '修改理由（可选）'}
          className="w-56"
        />
      )}
      {(changed || (asset.review_status === 'pending' && baselineAccess === 'workspace_visible')) && (
        <Button
          size="sm"
          disabled={busy || (expands && !reason.trim())}
          onClick={() => onReview({
            kind: 'asset',
            asset_id: asset.id,
            action: changed ? 'modified' : 'accepted',
            baseline_access: baselineAccess,
            requires_individual_review: individualReview,
            reason,
          })}
        >
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
          {changed ? '保存基线设置' : '确认全员授权'}
        </Button>
      )}
    </div>
  )
}

function EvidenceRelationRow({ relation, highRisk, mapping, busy, canManage, onSave, onOpenMappings }: {
  relation: EvidenceRelation
  highRisk: boolean
  mapping?: Mapping
  busy: boolean
  canManage: boolean
  onSave: (patch: Partial<EvidenceRelation>) => void
  onOpenMappings: () => void
}) {
  const originalLevel = evidenceAccessLevel(relation)
  const originalScope = relation.row_scope?.type || 'all'
  const [level, setLevel] = useState<EvidenceAccessLevel>(originalLevel)
  const [scopeType, setScopeType] = useState(
    originalLevel === 'partial' ? originalScope : 'target_org',
  )
  const [overrideReason, setOverrideReason] = useState(relation.override_reason || '')
  const savedScope = level === 'partial' ? scopeType : 'all'
  const changed = level !== originalLevel
    || (level === 'partial' && savedScope !== originalScope)
  const expands = (
    originalLevel === 'hidden' && level !== 'hidden'
    || originalLevel === 'partial' && level === 'visible'
  )
  const requiresReason = changed && expands
  const mappingMissing = level === 'partial' && (
    ['target_org', 'target_org_tree'].includes(scopeType)
      ? !mapping?.org_column_id
      : scopeType === 'self'
        ? !mapping?.user_column_id
        : false
  )
  const advancedScope = !['target_org', 'target_org_tree', 'self'].includes(scopeType)
  return (
    <div className="mt-3 rounded-lg border border-manus-border bg-manus p-3">
      <fieldset>
        <legend className="mb-2 text-xs font-medium text-manus-muted">表访问权限</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {([
            ['hidden', '不可见', '保持默认拒绝'],
            ['visible', '可见', '允许访问全部行'],
            ['partial', '部分可见', '按行范围限制'],
          ] as const).map(([value, label, description]) => (
            <label
              key={value}
              className={cn(
                'flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2.5 transition-colors',
                level === value
                  ? 'border-emerald-500/50 bg-emerald-500/8'
                  : 'border-manus-border bg-manus-tertiary hover:border-manus-muted/50',
                !canManage && 'cursor-default opacity-70',
              )}
            >
              <input
                type="radio"
                name={`evidence-access-${relation.id}`}
                value={value}
                checked={level === value}
                disabled={!canManage}
                onChange={() => {
                  setLevel(value)
                  if (value === 'partial' && originalLevel !== 'partial') setScopeType('target_org')
                }}
                className="mt-0.5 h-4 w-4 accent-emerald-500"
              />
              <span>
                <span className="block text-sm font-medium">{label}</span>
                <span className="mt-0.5 block text-[11px] text-manus-muted">{description}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      {level === 'partial' && (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <Field label="行范围">
            <NativeSelect
              value={scopeType}
              disabled={!canManage}
              onChange={value => setScopeType(value as SemanticAccessRowScope['type'])}
              ariaLabel="部分可见行范围"
            >
              <option value="target_org">本部门</option>
              <option value="target_org_tree">本部门及下级</option>
              <option value="self">仅本人</option>
              {advancedScope && <option value={scopeType}>高级范围（请在直接配置中修改）</option>}
            </NativeSelect>
          </Field>
          {mappingMissing && (
            <Button size="sm" variant="outline" className="text-amber-700 dark:text-amber-300" onClick={onOpenMappings}>
              缺少归属映射 · 去配置
            </Button>
          )}
        </div>
      )}
      <details className="mt-3 rounded-lg bg-manus-tertiary px-3 py-2 text-xs text-manus-muted">
        <summary className="cursor-pointer font-medium">
          建议依据 · {relationRoleLabel(relation.business_role)} · 置信度 {Math.round((relation.confidence || 0) * 100)}%
        </summary>
        <p className="mt-1 leading-5">{relation.reason || '未提供业务依据'}</p>
      </details>
      {canManage && requiresReason && (
        <Input
          value={overrideReason}
          onChange={event => setOverrideReason(event.target.value)}
          placeholder="扩大访问或高风险授权必须填写理由"
          className="mt-3"
        />
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Pill tone={relation.review_status === 'pending' ? 'neutral' : relation.review_status === 'rejected' ? 'red' : 'green'}>
          {relation.review_status === 'auto_safe'
            ? '安全默认'
            : relation.review_status === 'pending'
              ? '待确认授权'
              : relation.review_status === 'rejected'
                ? '明确拒绝'
                : '已处理'}
        </Pill>
        {highRisk && <Pill tone="red">高风险 · 逐项审核</Pill>}
        {canManage && (changed || (relation.review_status === 'pending' && level !== 'hidden')) && (
          <div className="ml-auto flex gap-2">
          <Button size="sm" onClick={() => onSave({
            access_level: level,
            row_scope: { type: savedScope } as SemanticAccessRowScope,
            override_reason: overrideReason,
            review_status: changed ? 'modified' : 'accepted',
          })} disabled={busy || mappingMissing || (requiresReason && !overrideReason.trim())}>
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {changed ? '保存访问权限' : '确认授权'}
          </Button>
          </div>
        )}
      </div>
    </div>
  )
}

function AccessMatrix({ payload, onSelect }: {
  payload: AccessMatrixPayload
  onSelect: (departmentId: number, tableId: number) => void
}) {
  const [tableSearch, setTableSearch] = useState('')
  const [departmentSearch, setDepartmentSearch] = useState('')
  const [status, setStatus] = useState<'all' | 'visible' | 'partial' | 'hidden' | 'pending'>('all')
  const cellByKey = useMemo(
    () => new Map(payload.cells.map(cell => [`${cell.department_id}:${cell.table_id}`, cell])),
    [payload.cells],
  )
  const departments = payload.departments.filter(item => (
    !departmentSearch.trim()
    || item.path.toLowerCase().includes(departmentSearch.trim().toLowerCase())
  ))
  const tables = payload.tables.filter(table => {
    const matchesSearch = !tableSearch.trim()
      || `${table.business_name} ${table.physical_name}`.toLowerCase().includes(tableSearch.trim().toLowerCase())
    if (!matchesSearch || status === 'all') return matchesSearch
    return departments.some(department => {
      const cell = cellByKey.get(`${department.id}:${table.id}`)
      return status === 'pending' ? cell?.pending_review : cell?.decision === status
    })
  })
  const template = `240px repeat(${departments.length}, minmax(150px, 1fr))`
  const width = 240 + Math.max(1, departments.length) * 150

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input value={tableSearch} onChange={event => setTableSearch(event.target.value)} placeholder="搜索表" className="w-48" />
        <Input value={departmentSearch} onChange={event => setDepartmentSearch(event.target.value)} placeholder="搜索部门" className="w-56" />
        <div className="flex rounded-lg border border-manus-border bg-manus-tertiary p-1">
          {([
            ['all', '全部'], ['visible', '可见'], ['partial', '部分可见'],
            ['hidden', '不可见'], ['pending', '待发布草稿'],
          ] as const).map(([value, label]) => (
            <button key={value} onClick={() => setStatus(value)} className={cn(
              'rounded-md px-2.5 py-1 text-xs',
              status === value && 'bg-manus-secondary',
            )}>{label}</button>
          ))}
        </div>
        <div className="ml-auto flex flex-wrap gap-2 text-[11px] text-manus-muted">
          <span className="text-emerald-300">● 可见</span>
          <span className="text-amber-300">● 部分可见</span>
          <span className="text-red-300">● 明确不可见</span>
          <span>● 默认拒绝</span>
          <span className="text-blue-300">● 待发布草稿</span>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-manus-border">
        <div style={{ minWidth: `${width}px` }}>
          <div className="sticky top-0 z-30 grid bg-manus-tertiary text-xs font-medium" style={{ gridTemplateColumns: template }}>
            <div className="sticky left-0 z-40 border-r border-manus-border bg-manus-tertiary px-3 py-3">启用的表</div>
            {departments.map(department => (
              <div key={department.id} title={department.path} className="border-r border-manus-border px-3 py-3">
                <div className="truncate" style={{ paddingLeft: `${Math.min(department.level, 4) * 8}px` }}>{department.name}</div>
                <div className="mt-1 truncate text-[10px] font-normal text-manus-muted">{department.path}</div>
              </div>
            ))}
          </div>
          {tables.map(table => (
            <div key={table.id} className="grid border-t border-manus-border" style={{ gridTemplateColumns: template }}>
              <div className="sticky left-0 z-20 border-r border-manus-border bg-manus-secondary px-3 py-3">
                <div className="truncate text-sm font-medium">{table.business_name}</div>
                <div className="mt-1 truncate font-mono text-[10px] text-manus-muted">{table.physical_name}</div>
              </div>
              {departments.map(department => {
                const cell = cellByKey.get(`${department.id}:${table.id}`)
                if (!cell) return <div key={department.id} className="border-r border-manus-border p-2" />
                const label = cell.decision === 'visible'
                  ? '可见'
                  : cell.decision === 'partial'
                    ? '部分可见'
                    : cell.reason === 'explicit_hidden'
                      ? '明确不可见'
                      : '默认拒绝'
                const tone = cell.decision === 'visible'
                  ? 'text-emerald-300'
                  : cell.decision === 'partial'
                    ? 'text-amber-300'
                    : cell.reason === 'explicit_hidden'
                      ? 'text-red-300'
                      : 'text-manus-muted'
                return (
                  <button
                    key={department.id}
                    onClick={() => onSelect(department.id, table.id)}
                    className="min-h-20 border-r border-manus-border p-2 text-left transition-colors hover:bg-manus-hover"
                  >
                    <span className={cn('block text-xs font-semibold', tone)}>{label}</span>
                    <span className="mt-1 block text-[10px] text-manus-muted">{matrixSourceLabel(cell, payload.departments)}</span>
                    {cell.pending_review && (
                      <span className="mt-2 inline-flex rounded bg-blue-500/12 px-1.5 py-0.5 text-[10px] text-blue-300">
                        草稿：{cell.pending_decision === 'visible'
                          ? '可见'
                          : cell.pending_decision === 'partial'
                            ? '部分可见'
                            : '不可见'}
                        {cell.confidence != null ? ` · ${Math.round(cell.confidence * 100)}%` : ''}
                      </span>
                    )}
                    {cell.pending_review && isLowEvidenceConfidence(cell.confidence) && (
                      <span className="mt-1 block text-[10px] text-amber-300">低置信度</span>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
          {!tables.length && <div className="p-10 text-center text-sm text-manus-muted">当前筛选下没有启用表。</div>}
        </div>
      </div>
    </div>
  )
}

function matrixSourceLabel(
  cell: AccessMatrixCell,
  departments: AccessMatrixPayload['departments'],
) {
  if (cell.source === 'direct') return '当前部门直接配置'
  if (cell.source === 'inherited') {
    const source = departments.find(item => String(item.id) === cell.source_target_id)
    return `继承自 ${source?.name || `部门 #${cell.source_target_id}`}`
  }
  if (cell.source === 'baseline') return '全员基线'
  return '未授权'
}

function ProgressCard({ title, reviewed, total }: { title: string; reviewed: number; total: number }) {
  const complete = total > 0 && reviewed === total
  return <div className="rounded-xl border border-manus-border bg-manus-tertiary p-4"><div className="text-sm font-medium">{title}</div><div className={cn('mt-2 text-2xl font-semibold', complete ? 'text-emerald-300' : 'text-amber-300')}>{reviewed}/{total}</div></div>
}

function evidenceBlockerLabel(code: string) {
  return {
    business_context_missing: '企业业务背景未填写',
    org_profiles_incomplete: '一级部门职责画像未全部确认',
    queryable_tables_missing: '没有当前可问数表',
    table_semantics_unconfirmed: '存在未确认的表业务语义',
    column_semantics_unconfirmed: '存在未确认的字段业务语义',
    public_access_pending: '存在尚未确认的全员访问授权',
    table_grant_pending: '存在尚未确认的部门表授权',
    field_grant_pending: '存在尚未确认的字段放开授权',
    sensitive_table_cannot_be_public: '敏感表不能开放为全员可问',
    sensitive_reason_missing: '敏感数据放开缺少审核理由',
    visible_scope_must_be_all: '“可见”必须使用全部行范围',
    partial_scope_required: '“部分可见”必须选择受限行范围',
    conditional_not_executable: '条件使用建议不能直接编译为全部可见',
    org_mapping_missing: '部分可见缺少组织归属字段映射',
    user_mapping_missing: '仅本人范围缺少用户归属字段映射',
  }[code] || code
}

function evidenceSetStatusLabel(status: string) {
  return {
    review_ready: '待审核',
    published: '已发布',
    publish_failed: '发布失败',
    failed: '生成失败',
    generating: '生成中',
  }[status] || status
}

function relationRoleLabel(value: EvidenceRelation['relation_role']) {
  return {
    owner: '责任',
    producer: '生产',
    required_consumer: '必须使用',
    conditional_consumer: '条件使用',
    none: '无需要',
  }[value]
}

function progressLabel(value: string) {
  return {
    public_access: '全员授权',
    table_grants: '部门授权',
    field_grants: '字段放开',
  }[value] || value
}

function TargetStatus({ target, configured, draft = false }: {
  target: SemanticAccessTargetSummary
  configured: boolean
  draft?: boolean
}) {
  if (draft) return <Pill tone="amber">草稿</Pill>
  if (target.has_explicit_deny) return <Pill tone="red">存在明确拒绝</Pill>
  if (configured) return <Pill tone="green">已配置</Pill>
  return <Pill tone="neutral">继承上级</Pill>
}

function Pill({ children, tone }: { children: ReactNode; tone: 'green' | 'red' | 'blue' | 'amber' | 'neutral' }) {
  const colors = {
    green: 'bg-emerald-500/14 text-emerald-800 dark:text-emerald-300',
    red: 'bg-red-500/14 text-red-800 dark:text-red-300',
    blue: 'bg-blue-500/14 text-blue-800 dark:text-blue-300',
    amber: 'bg-amber-500/14 text-amber-800 dark:text-amber-300',
    neutral: 'bg-manus-tertiary text-manus-muted',
  }
  return <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', colors[tone])}>{children}</span>
}

function NativeSelect({ value, disabled = false, onChange, children, ariaLabel }: { value: string; disabled?: boolean; onChange: (value: string) => void; children: ReactNode; ariaLabel: string }) {
  return <select aria-label={ariaLabel} value={value} disabled={disabled} onChange={event => onChange(event.target.value)} className="mt-1 h-9 w-full rounded-md border border-manus-border bg-manus-tertiary px-3 text-sm text-manus-text outline-none focus:border-accent disabled:opacity-50">{children}</select>
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block text-xs text-manus-muted">{label}{children}</label>
}

function CenteredState({ icon, title, description, compact = false }: { icon: ReactNode; title: string; description?: string; compact?: boolean }) {
  return <div className={cn('flex flex-col items-center justify-center px-6 text-center', compact ? 'py-12' : 'min-h-[360px] py-16')}><div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-manus-border bg-manus-tertiary text-manus-muted [&>svg]:h-5 [&>svg]:w-5">{icon}</div><div className="font-medium">{title}</div>{description && <p className="mt-1 max-w-sm text-sm text-manus-muted">{description}</p>}</div>
}
