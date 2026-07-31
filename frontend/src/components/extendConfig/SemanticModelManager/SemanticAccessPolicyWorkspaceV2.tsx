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
  Save,
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
  type SemanticAccessVersion,
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
  hasSemanticAccessPermissionChanges,
} from './semanticAccessPolicyDraftUtils'
import {
  accessLevelLabel,
  evidenceAccessLevel,
  evidenceBaselineAccess,
  evidenceFieldDecision,
  evidenceRequiresIndividualReview,
  isBulkReviewableFieldGrant,
  isBulkReviewableTableGrant,
  isLowEvidenceConfidence,
  isManuallyApproved,
  summarizeEvidenceConfirmations,
  type EvidenceConfirmationSummary,
  type EvidenceAccessLevel,
  type EvidenceFieldDecision,
} from './semanticAccessEvidenceV2Utils'

type TargetTab = 'org_unit' | 'position' | 'user'
type Area = 'baseline' | 'organization' | 'unassigned'
type Mapping = SemanticOwnershipMapping

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
  const [includeDescendants, setIncludeDescendants] = useState(true)
  const [activeIncludeDescendants, setActiveIncludeDescendants] = useState(true)
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
  const [evidence, setEvidence] = useState<EvidencePayload>({ set: null })
  const [evidenceReadiness, setEvidenceReadiness] = useState<EvidenceReadiness | null>(null)
  const [bootstrapRun, setBootstrapRun] = useState<SemanticAccessBootstrapRun | null>(null)
  const [bootstrapSuggestions, setBootstrapSuggestions] = useState<SemanticAccessBootstrapSuggestions | null>(null)
  const [draftTarget, setDraftTarget] = useState<SemanticAccessBootstrapTarget | null>(null)
  const [evidenceFilter, setEvidenceFilter] = useState<'all' | 'pending' | 'restricted' | 'sensitive' | 'mapping'>('all')
  const [publishDialogOpen, setPublishDialogOpen] = useState(false)
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
    () => hasSemanticAccessPermissionChanges(rules, activeRules)
      || (selectedTarget?.target_type === 'org_unit'
        && includeDescendants !== activeIncludeDescendants),
    [activeIncludeDescendants, activeRules, includeDescendants, rules, selectedTarget?.target_type],
  )
  const initialReviewMode = Boolean(
    evidenceReadiness?.access_bootstrap_required
    && bootstrapRun?.status === 'review_ready',
  )
  const bootstrapBlockers = useMemo(
    () => (bootstrapSuggestions?.targets || []).flatMap(target => (
      target.validation?.blockers || []
    )),
    [bootstrapSuggestions],
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
  const evidenceConfirmation = useMemo(() => (
    evidenceConfirmationTarget
      ? summarizeEvidenceConfirmations(
          evidence.assets || [],
          evidence.relations || [],
          evidenceConfirmationTarget.type,
          evidenceConfirmationTarget.id,
          new Set(enabledColumns.filter(column => column.is_sensitive).map(column => column.id)),
          currentTable?.id,
        )
      : null
  ), [
    currentTable?.id,
    enabledColumns,
    evidence.assets,
    evidence.relations,
    evidenceConfirmationTarget,
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
    const [readiness, current] = await Promise.all([
      semanticAccessRequest<EvidenceReadiness>(
        apiPath(`/access-evidence/readiness?datasource_id=${datasourceId}`),
      ),
      semanticAccessRequest<EvidencePayload>(
        apiPath(`/access-evidence/sets/current?datasource_id=${datasourceId}`),
      ),
    ])
    setEvidenceReadiness(readiness)
    setEvidence(current)
    if (
      canManage
      && readiness.access_bootstrap_required
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
    } else if (!readiness.access_bootstrap_required) {
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
    if (evidence.set && !['stale', 'generating'].includes(evidence.set.status)) return
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

  const openTarget = useCallback(async (target: SemanticAccessTargetSummary) => {
    setSelectedTarget(target)
    setTargetLoading(true)
    setError('')
    setNotice('')
    setSuggestion(null)
    setSourceText('')
    try {
      if (bootstrapRun?.status === 'review_ready') {
        const draft = await semanticAccessRequest<BootstrapTargetResponse>(
          apiPath(
            `/access-bootstrap/runs/${bootstrapRun.run_id}/targets/${target.target_type}/${encodeURIComponent(target.target_id)}`,
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
        setIncludeDescendants(draft.target.include_descendants)
        setActiveIncludeDescendants(draft.target.include_descendants)
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
      setIncludeDescendants(detail.include_descendants)
      setActiveIncludeDescendants(detail.include_descendants)
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
      setVersions([])
      setError(semanticAccessErrorMessage(cause, '策略加载失败'))
    } finally {
      setTargetLoading(false)
    }
  }, [apiPath, bootstrapRun, datasourceId])

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
    const next: SemanticAccessTableRule = {
      table_id: currentTable.id,
      hidden_column_ids: [],
      hidden_metric_ids: [],
      ...currentRule,
      ...patch,
    }
    if ('decision' in patch) {
      if (next.decision !== 'visible') {
        delete next.row_scope
      } else if (!next.row_scope) {
        const suggestedScope = draftTarget?.candidates.find(
          candidate => candidate.table_id === currentTable.id,
        )?.row_scope
        next.row_scope = suggestedScope === 'target_org_tree'
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

  const saveDraftTarget = useCallback(async (): Promise<boolean> => {
    if (!selectedTarget || !bootstrapRun || !canManage) return false
    setBusy('draft-save')
    setError('')
    setNotice('')
    try {
      const result = await semanticAccessRequest<BootstrapTargetResponse>(
        apiPath(
          `/access-bootstrap/runs/${bootstrapRun.run_id}/targets/${selectedTarget.target_type}/${encodeURIComponent(selectedTarget.target_id)}`,
        ),
        {
          method: 'PUT',
          body: JSON.stringify({
            expected_revision: bootstrapRun.revision,
            definition: {
              name: selectedTarget.label,
              tables: savableRules,
            },
            include_descendants: selectedTarget.target_type === 'org_unit'
              ? includeDescendants
              : false,
            reason: sourceText.trim() || null,
          }),
        },
      )
      setBootstrapRun(result.run)
      setDraftTarget(result.target)
      setRules(result.target.definition.tables || [])
      setActiveRules(result.target.definition.tables || [])
      setActiveIncludeDescendants(result.target.include_descendants)
      setBootstrapSuggestions(await semanticAccessRequest<SemanticAccessBootstrapSuggestions>(
        apiPath(`/access-bootstrap/runs/${result.run.run_id}/suggestions`),
      ))
      setNotice('当前授权对象已保存到首次权限草稿，尚未发布生效。')
      return true
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '首次权限草稿保存失败'))
      return false
    } finally {
      setBusy('')
    }
  }, [
    apiPath,
    bootstrapRun,
    canManage,
    includeDescendants,
    savableRules,
    selectedTarget,
    sourceText,
  ])

  const save = useCallback(async (confirmWarnings = false): Promise<boolean> => {
    if (!selectedTarget || !binding || !canManage) return false
    setBusy('save')
    setError('')
    setNotice('')
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
          include_descendants: selectedTarget.target_type === 'org_unit'
            ? includeDescendants
            : false,
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
      setActiveIncludeDescendants(includeDescendants)
      setNotice(`版本 v${result.active_version?.version || result.revision} 已生效 · 影响 ${result.affected_user_count ?? 0} 个账号${result.audit_id ? ` · 审计 #${result.audit_id}` : ''}`)
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
    binding,
    canManage,
    datasourceId,
    includeDescendants,
    loadTargets,
    savableRules,
    selectedTarget,
    sourceText,
  ])

  const saveCurrent = useCallback(
    () => initialReviewMode ? saveDraftTarget() : save(),
    [initialReviewMode, save, saveDraftTarget],
  )

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
    if (!evidence.set) return
    const targetType = area === 'baseline' ? 'baseline' : 'org_unit'
    const targetId = area === 'baseline' ? '*' : String(
      selectedOrg?.level === 0
        ? selectedOrg.id
        : Number(selectedOrg?.ancestors.split('/').filter(Boolean)[0] || selectedOrg?.id),
    )
    if (!targetId || targetId === 'undefined') return
    setBusy(mode === 'table' ? `evidence-table:${tableId}` : 'evidence-target')
    setError('')
    try {
      const next = await semanticAccessRequest<EvidencePayload & {
        confirmation?: { assets: number; relations: number }
      }>(apiPath(
        `/access-evidence/sets/${evidence.set.id}/targets/${targetType}/${encodeURIComponent(targetId)}/confirm`,
      ), {
        method: 'POST',
        body: JSON.stringify({
          expected_revision: evidence.set.revision,
          mode,
          table_id: tableId || null,
        }),
      })
      setEvidence(next)
      setNotice(mode === 'table'
        ? '本表的表权限与字段配置已确认。'
        : mode === 'target'
          ? targetType === 'baseline'
            ? '全员基线的全部配置已确认。'
            : '本部门的全部配置已确认，包括敏感与高风险配置。'
          : '当前授权对象的普通授权已确认。')
      if (mode === 'target') setDepartmentConfirmOpen(false)
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '授权确认失败'))
    } finally {
      setBusy('')
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

  const confirmEvidence = async () => {
    if (!bootstrapRun) return
    setBusy('evidence-confirm')
    setError('')
    try {
      await semanticAccessRequest(
        apiPath(`/access-bootstrap/runs/${bootstrapRun.run_id}/apply`),
        {
          method: 'POST',
          body: JSON.stringify({
            expected_revision: bootstrapRun.revision,
            confirm_warnings: true,
          }),
        },
      )
      setPublishDialogOpen(false)
      setNotice('首次权限草稿已确定性编译并原子发布。')
      await loadBase()
    } catch (cause) {
      setError(semanticAccessErrorMessage(cause, '首次权限发布失败，原有权限保持不变'))
      await loadEvidence().catch(() => undefined)
    } finally {
      setBusy('')
    }
  }

  const openPublishDialog = async () => {
    if (!bootstrapRun) return
    if (isDirty && !await saveDraftTarget()) return
    const next = await semanticAccessRequest<SemanticAccessBootstrapSuggestions>(
      apiPath(`/access-bootstrap/runs/${bootstrapRun.run_id}/suggestions`),
    )
    setBootstrapRun(next.run)
    setBootstrapSuggestions(next)
    setPublishDialogOpen(true)
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
          {canManage && (
            <Button
              onClick={() => initialReviewMode
                ? void openPublishDialog()
                : void save()}
              disabled={
                initialReviewMode
                  ? !bootstrapSuggestions
                    || bootstrapBlockers.length > 0
                    || Boolean(busy)
                  : Boolean(evidenceReadiness?.access_bootstrap_required)
                    || !isDirty || busy === 'save' || !selectedTarget
              }
              title={
                evidenceReadiness?.access_bootstrap_required && !initialReviewMode
                  ? '请先生成首次权限草稿'
                  : initialReviewMode && bootstrapBlockers.length > 0
                    ? `还有 ${bootstrapBlockers.length} 个阻断项`
                    : undefined
              }
            >
              {['save', 'draft-save', 'evidence-confirm'].includes(busy)
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Save className="h-4 w-4" />}
              <span className="hidden sm:inline">保存并生效</span>
            </Button>
          )}
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
          {error ? <CircleAlert className="h-4 w-4" /> : <Check className="h-4 w-4" />}
          <span className="min-w-0 flex-1 truncate">{error || notice}</span>
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
                    selectedTarget={selectedTarget}
                    includeDescendants={includeDescendants}
                    canManage={canManage}
                    draftMode={initialReviewMode}
                    actions={directPolicyActions}
                    onIncludeDescendants={setIncludeDescendants}
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
                                configured={Boolean(binding.active_version_id)}
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
                            rules={rules}
                            candidates={draftTarget?.candidates || []}
                            draftMode={initialReviewMode}
                            canManage={canManage}
                            canManageWorkspace={canManageWorkspace}
                            highRiskTableIds={highRiskTableIds}
                            sensitiveTableIds={sensitiveTableIds}
                            confirmation={evidenceConfirmation || undefined}
                            confirmBusy={busy === `evidence-table:${currentTable?.id}`}
                            onSelectTable={setSelectedTableId}
                            onUpdateRule={updateRule}
                            onConfirmTable={() => currentTable
                              && void confirmEvidenceTarget('table', currentTable.id)}
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

      <Dialog open={publishDialogOpen} onOpenChange={setPublishDialogOpen}>
        <DialogContent className="max-w-2xl border-manus-border bg-manus-secondary">
          <DialogHeader>
            <DialogTitle>保存并生效首次权限</DialogTitle>
            <DialogDescription>系统会一次发布当前全部首次权限草稿；失败时现有权限保持不变。</DialogDescription>
          </DialogHeader>
          <div className="min-h-44">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <SummaryCountCard
                  title="草稿策略"
                  value={bootstrapSuggestions?.targets.length || 0}
                  complete={Boolean(bootstrapSuggestions?.targets.length)}
                />
                <SummaryCountCard
                  title="低置信度"
                  value={
                    (bootstrapRun?.summary.low_confidence_table_count || 0)
                    + (bootstrapRun?.summary.low_confidence_field_count || 0)
                  }
                  complete={
                    (bootstrapRun?.summary.low_confidence_table_count || 0)
                    + (bootstrapRun?.summary.low_confidence_field_count || 0) === 0
                  }
                />
                <SummaryCountCard
                  title="敏感资产"
                  value={
                    (bootstrapRun?.summary.sensitive_table_count || 0)
                    + (bootstrapRun?.summary.sensitive_field_count || 0)
                  }
                  complete
                />
                <SummaryCountCard
                  title="阻断项"
                  value={bootstrapBlockers.length}
                  complete={bootstrapBlockers.length === 0}
                />
            </div>
            {bootstrapBlockers.length > 0 && (
              <div className="mt-3 rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {bootstrapBlockers.map(item => item.message).join('；')}
              </div>
            )}
          </div>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setPublishDialogOpen(false)}>返回继续配置</Button>
            <Button
              onClick={() => void confirmEvidence()}
              disabled={
                !bootstrapSuggestions
                || busy === 'evidence-confirm'
                || bootstrapBlockers.length > 0
              }
            >
              {busy === 'evidence-confirm' && <Loader2 className="h-4 w-4 animate-spin" />}
              确认并原子发布
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

function TargetHeader({ area, selectedOrg, targetTab, selectedTarget, includeDescendants, canManage, draftMode, actions, onIncludeDescendants, onSelectTab }: {
  area: Area
  selectedOrg: OrgUnit | null
  targetTab: TargetTab
  selectedTarget: SemanticAccessTargetSummary | null
  includeDescendants: boolean
  canManage: boolean
  draftMode: boolean
  actions?: ReactNode
  onIncludeDescendants: (checked: boolean) => void
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
        {(actions || (
          area === 'organization'
          && targetTab === 'org_unit'
          && selectedTarget?.target_type === 'org_unit'
        )) && (
          <div className="flex flex-wrap items-center justify-end gap-2">
            {actions}
            {area === 'organization' && targetTab === 'org_unit' && selectedTarget?.target_type === 'org_unit' && (
              <div className="flex items-center gap-3 rounded-lg border border-manus-border bg-manus-tertiary px-3 py-2">
                <div><div className="text-xs font-medium">包含下级部门</div><div className="text-[11px] text-manus-muted">影响约 {selectedTarget.affected_user_count} 个账号</div></div>
                <Switch checked={includeDescendants} onCheckedChange={onIncludeDescendants} disabled={!canManage} />
              </div>
            )}
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

function PolicyEditor({ tables, columns, metrics, organizations, target, selectedTableId, currentTable, rule, rules, candidates, draftMode, canManage, canManageWorkspace, highRiskTableIds, sensitiveTableIds, confirmation, confirmBusy, onSelectTable, onUpdateRule, onConfirmTable }: {
  tables: SemanticTable[]
  columns: SemanticColumn[]
  organizations: OrgUnit[]
  metrics: SemanticMetric[]
  target: SemanticAccessTargetSummary
  selectedTableId: number
  currentTable?: SemanticTable
  rule?: SemanticAccessTableRule
  rules: SemanticAccessTableRule[]
  candidates: SemanticAccessBootstrapCandidate[]
  draftMode: boolean
  canManage: boolean
  canManageWorkspace: boolean
  highRiskTableIds: Set<number>
  sensitiveTableIds: Set<number>
  confirmation?: EvidenceConfirmationSummary
  confirmBusy: boolean
  onSelectTable: (id: number) => void
  onUpdateRule: (patch: Partial<SemanticAccessTableRule>) => void
  onConfirmTable: () => void
}) {
  const candidateByTable = new Map(candidates.map(item => [item.table_id, item]))
  const currentCandidate = currentTable ? candidateByTable.get(currentTable.id) : undefined
  const currentTableIsHighRisk = Boolean(
    currentTable && highRiskTableIds.has(currentTable.id),
  )
  const currentTableIsSensitive = Boolean(
    currentTable && sensitiveTableIds.has(currentTable.id),
  )
  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[236px_minmax(0,1fr)]">
      <div className="min-h-0 overflow-y-auto border-b border-manus-border p-2 md:border-b-0 md:border-r">
        {tables.map(table => {
          const tableRule = rules.find(item => item.table_id === table.id)
          const tableCandidate = candidateByTable.get(table.id)
          return (
            <button key={table.id} onClick={() => onSelectTable(table.id)} className={cn(
              'mb-1 w-full rounded-lg px-3 py-3 text-left transition-colors',
              selectedTableId === table.id ? 'bg-manus-tertiary' : 'hover:bg-manus-hover',
            )}>
              <div className="truncate text-sm font-medium">{table.business_name}</div>
              <div className="mt-1 truncate font-mono text-[11px] text-manus-muted">{table.physical_name}</div>
              <div className={cn(
                'mt-2 text-[11px]',
                tableRule?.decision === 'visible' ? 'text-emerald-300' : tableRule?.decision === 'hidden' ? 'text-red-300' : 'text-manus-muted',
              )}>{tableRule?.decision === 'visible' ? '直接可见' : tableRule?.decision === 'hidden' ? '明确不可见' : '跟随其他层级'}</div>
              {(highRiskTableIds.has(table.id) || sensitiveTableIds.has(table.id)) && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {highRiskTableIds.has(table.id) && (
                    <span className="inline-flex rounded-full bg-amber-500/14 px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
                      高风险
                    </span>
                  )}
                  {sensitiveTableIds.has(table.id) && (
                    <span className="inline-flex rounded-full bg-rose-500/12 px-2 py-0.5 text-[10px] font-medium text-rose-700 dark:text-rose-300">
                      含敏感字段
                    </span>
                  )}
                </div>
              )}
              {draftMode && isLowEvidenceConfidence(tableCandidate?.confidence) && (
                <span className="mt-2 inline-flex rounded-full bg-amber-500/14 px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
                  低置信度 &lt; 60%
                </span>
              )}
            </button>
          )
        })}
      </div>
      <div className="min-h-0 overflow-y-auto p-4 md:p-5">
        {currentTable ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><h3 className="font-semibold">{currentTable.business_name}</h3><code className="text-xs text-manus-muted">{currentTable.physical_name}</code></div>
              <div className="flex flex-wrap items-center gap-2">
                {draftMode && confirmation && confirmation.tablePendingItems > 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={onConfirmTable}
                    disabled={!canManage || !canManageWorkspace || confirmBusy}
                  >
                    {confirmBusy
                      ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Check className="h-4 w-4" />}
                    确认本表
                  </Button>
                )}
                {draftMode && confirmation?.tableConfirmed && (
                  <Badge
                    variant="outline"
                    className="border-emerald-500/35 bg-emerald-500/10 text-emerald-300"
                  >
                    <Check className="mr-1 h-3.5 w-3.5" />
                    本表已确认
                  </Badge>
                )}
                <DecisionSelector value={rule?.decision} disabled={!canManage} onChange={decision => onUpdateRule({ decision })} />
              </div>
            </div>

            {(currentTableIsHighRisk || currentTableIsSensitive) && (
              <div className="mt-4 flex gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-xs text-amber-900">
                <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
                <div>
                  <div className="font-medium">
                    {currentTableIsHighRisk && currentTableIsSensitive
                      ? '此表属于高风险表并包含敏感字段'
                      : currentTableIsHighRisk
                        ? '此表属于高风险表'
                        : '此表包含敏感字段'}
                  </div>
                  <p className="mt-1 leading-5 text-amber-800">
                    请重点核对表权限与敏感字段的可见状态；“确认本表”会一并接受当前表的相关建议。
                  </p>
                </div>
              </div>
            )}

            {draftMode && currentCandidate && (
              <details className="mt-4 rounded-lg border border-manus-border bg-manus-tertiary px-3 py-2.5 text-xs text-manus-muted">
                <summary className="cursor-pointer font-medium text-manus-text">
                  生成依据 · 置信度 {Math.round((currentCandidate.confidence || 0) * 100)}%
                  {currentCandidate.sensitive ? ' · 敏感表' : ''}
                </summary>
                <p className="mt-2 leading-5">{currentCandidate.reason || '未提供额外依据说明'}</p>
              </details>
            )}

            {rule?.decision === 'visible' && (
              <RowScopeEditor
                value={rule.row_scope}
                target={target}
                organizations={organizations}
                columns={columns}
                canManageWorkspace={canManageWorkspace}
                disabled={!canManage}
                onChange={row_scope => onUpdateRule({ row_scope })}
              />
            )}

            <h4 className="mb-3 mt-6 text-sm font-medium">字段可见性</h4>
            <div className="grid gap-2 sm:grid-cols-2">
              {columns.map(column => {
                const fieldCandidate = currentCandidate?.field_suggestions?.find(
                  item => item.column_id === column.id,
                )
                const followsHiddenTable = rule?.decision === 'hidden'
                const hidden = followsHiddenTable || rule?.hidden_column_ids.includes(column.id)
                return (
                  <button
                    key={column.id}
                    disabled={!canManage || rule?.decision !== 'visible'}
                    onClick={() => onUpdateRule({
                      hidden_column_ids: hidden
                        ? (rule?.hidden_column_ids || []).filter(id => id !== column.id)
                        : [...(rule?.hidden_column_ids || []), column.id],
                    })}
                    className={cn(
                      'flex items-center justify-between rounded-lg border bg-manus/30 p-3 text-left disabled:opacity-45',
                      column.is_sensitive
                        ? 'border-rose-500/35'
                        : 'border-manus-border',
                    )}
                  >
                    <span className="min-w-0"><b className="block truncate text-sm">{column.business_name}</b><code className="text-[11px] text-manus-muted">{column.physical_name}</code></span>
                    <span className="ml-3 flex shrink-0 items-center gap-2">
                      {column.is_sensitive && (
                        <span className={cn(
                          'rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                          hidden
                            ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
                            : 'bg-rose-500/18 text-rose-800 dark:text-rose-200',
                        )}>
                          {hidden ? '敏感字段' : '敏感字段 · 已开放'}
                        </span>
                      )}
                      {draftMode && isLowEvidenceConfidence(fieldCandidate?.confidence) && (
                        <span className="rounded-full bg-amber-500/14 px-1.5 py-0.5 text-[10px] text-amber-700 dark:text-amber-300">
                          低置信度
                        </span>
                      )}
                      {followsHiddenTable && (
                        <span className="text-[10px] text-manus-muted">随表不可见</span>
                      )}
                      {hidden ? <EyeOff className="h-4 w-4 text-red-300" /> : <Eye className="h-4 w-4 text-emerald-300" />}
                    </span>
                  </button>
                )
              })}
            </div>

            {metrics.length > 0 && <>
              <h4 className="mb-3 mt-6 text-sm font-medium">指标可见性</h4>
              <div className="grid gap-2 sm:grid-cols-2">
                {metrics.map(metric => {
                  const hidden = rule?.hidden_metric_ids.includes(metric.id)
                  return (
                    <button key={metric.id} disabled={!canManage || rule?.decision !== 'visible'} onClick={() => onUpdateRule({
                      hidden_metric_ids: hidden
                        ? (rule?.hidden_metric_ids || []).filter(id => id !== metric.id)
                        : [...(rule?.hidden_metric_ids || []), metric.id],
                    })} className="flex items-center justify-between rounded-lg border border-manus-border bg-manus/30 p-3 text-left disabled:opacity-45">
                      <span className="text-sm font-medium">{metric.business_name}</span>
                      {hidden ? <EyeOff className="h-4 w-4 text-red-300" /> : <Eye className="h-4 w-4 text-emerald-300" />}
                    </button>
                  )
                })}
              </div>
            </>}
          </>
        ) : <CenteredState icon={<ShieldCheck />} title="当前数据源没有语义表" />}
      </div>
    </div>
  )
}

function DecisionSelector({ value, disabled, onChange }: { value?: 'visible' | 'hidden'; disabled: boolean; onChange: (value?: 'visible' | 'hidden') => void }) {
  return (
    <div className="flex rounded-lg border border-manus-border bg-manus-tertiary p-1">
      {([
        [undefined, '跟随'],
        ['visible', '可见'],
        ['hidden', '不可见'],
      ] as const).map(([id, label]) => (
        <button key={label} disabled={disabled} onClick={() => onChange(id)} className={cn(
          'rounded-md px-3 py-1.5 text-sm transition-colors disabled:opacity-50',
          value === id && (id === 'visible' ? 'bg-emerald-500/18 text-emerald-300' : id === 'hidden' ? 'bg-red-500/18 text-red-300' : 'bg-manus-secondary'),
        )}>{label}</button>
      ))}
    </div>
  )
}

function RowScopeEditor({ value, target, organizations, columns, canManageWorkspace, disabled, onChange }: {
  value?: SemanticAccessRowScope
  target: SemanticAccessTargetSummary
  organizations: OrgUnit[]
  columns: SemanticColumn[]
  canManageWorkspace: boolean
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
    <div className="mt-5 rounded-xl border border-manus-border bg-manus/25 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h4 className="text-sm font-medium">行数据范围</h4><p className="mt-1 text-xs text-manus-muted">授权对象与可查看的数据组织范围相互独立。</p></div>
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
      <Field label="组织字段取值"><NativeSelect value={mapping.org_value_kind} disabled={!canEdit} onChange={value => onChange({ ...mapping, org_value_kind: value as 'id' | 'code' })} ariaLabel="组织字段取值"><option value="id">部门 ID</option><option value="code">部门编码</option></NativeSelect></Field>
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

function SummaryCountCard({ title, value, complete }: {
  title: string
  value: number
  complete: boolean
}) {
  return <div className="rounded-xl border border-manus-border bg-manus-tertiary p-4"><div className="text-sm font-medium">{title}</div><div className={cn('mt-2 text-2xl font-semibold', complete ? 'text-emerald-300' : 'text-amber-300')}>{value}</div></div>
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
  return <Pill tone="neutral">未配置 · 跟随</Pill>
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
