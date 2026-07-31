import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import {
  BriefcaseBusiness,
  Building2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CheckCircle2,
  FolderPlus,
  Inbox,
  KeyRound,
  Loader2,
  Menu,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  UserMinus,
  UserPlus,
  UserRoundCog,
  UserX,
  Users,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'
import { cn } from '@/lib/utils'
import {
  AuthorizationApiError,
  apiErrorMessage,
  authorizationRequest,
} from '@/features/authorization/api'
import {
  assignmentStateLabel,
  buildOrgTree,
  descendantOrgIds,
  formatDateTime,
  toLocalInput,
  type Account,
  type AccountAssignment,
  type AssignmentState,
  type DeletionBlockers,
  type OrgTreeNode,
  type OrgUnit,
  type PositionSummary,
} from '@/features/authorization/types'
import {
  collaborationReferencesDepartment,
  restrictCollaborations,
  setCollaborationDepartment,
} from './organizationProfileUtils'

type OrgDialogState = {
  mode: 'root' | 'child' | 'edit'
  item?: OrgUnit
  parentId: number | null
  name: string
  status: boolean
}

type PositionDialogState = {
  item?: PositionSummary
  name: string
  orgUnitId: number
}

type AssignmentDialogState = {
  mode: 'add' | 'edit' | 'assign-unassigned'
  positionId: number
  assignment?: AccountAssignment
  fixedAccount?: Account
  tab: 'existing' | 'new'
  userId: string
  username: string
  email: string
  password: string
  isPrimary: boolean
  startsAt: string
  endsAt: string
  status: boolean
}

type AccountDialogState = {
  mode: 'edit' | 'password'
  account: Account
  username: string
  email: string
  password: string
}

type DeleteState = {
  kind: 'org' | 'position' | 'assignment' | 'account'
  id: number | string
  name: string
  blockers?: DeletionBlockers
}

type BusinessContext = {
  exists: boolean
  content: string
  industry: string
  core_offerings: string[]
  business_objects: string[]
  business_processes: string[]
  customer_types: string[]
  operating_regions: string[]
  special_terms: string[]
  data_governance_constraints: string[]
  quality: {
    level: 'missing' | 'partial' | 'sufficient'
    required_completed: number
    required_total: number
    completeness_percent: number
    missing_fields: string[]
    missing_field_labels: string[]
    uses_general_knowledge: boolean
  }
  revision: number
  updated_by?: string
  updated_at?: string
}

type BusinessContextDraft = Pick<
  BusinessContext,
  | 'content'
  | 'industry'
  | 'core_offerings'
  | 'business_objects'
  | 'business_processes'
  | 'customer_types'
  | 'operating_regions'
  | 'special_terms'
  | 'data_governance_constraints'
>

type ProfileContent = {
  positioning: string
  responsibilities: string[]
  business_objects: string[]
  data_produced: string[]
  data_consumed: string[]
  collaborations: string[]
  boundaries: string[]
  keywords: string[]
  known_facts: string[]
  assumptions: string[]
  missing_information: string[]
  permission_impacts: string[]
}

type ProfileVersion = {
  id: number
  version: number
  status: string
  content: ProfileContent
  evidence?: {
    sources?: string[]
    business_context_quality?: BusinessContext['quality']
    assumption_count?: number
    missing_information_count?: number
  }
  source: string
  reviewed_at?: string
}

type OrgSemanticProfile = {
  org_unit: { id: number; name: string; code?: string }
  status: 'missing' | 'generating' | 'draft' | 'confirmed' | 'stale' | 'failed'
  revision: number
  error_message?: string
  active_version?: ProfileVersion | null
  draft_version?: ProfileVersion | null
}

type ProfileConfirmState = {
  profile: OrgSemanticProfile
}

const emptyBusinessContextDraft = (): BusinessContextDraft => ({
  content: '',
  industry: '',
  core_offerings: [],
  business_objects: [],
  business_processes: [],
  customer_types: [],
  operating_regions: [],
  special_terms: [],
  data_governance_constraints: [],
})

const emptyBusinessContext = (): BusinessContext => ({
  exists: false,
  ...emptyBusinessContextDraft(),
  quality: {
    level: 'missing',
    required_completed: 0,
    required_total: 3,
    completeness_percent: 0,
    missing_fields: ['industry', 'core_offerings', 'business_objects'],
    missing_field_labels: ['所属行业', '核心产品或服务', '核心业务对象'],
    uses_general_knowledge: true,
  },
  revision: 0,
})

const businessContextDraftFrom = (context: BusinessContext): BusinessContextDraft => ({
  content: context.content || '',
  industry: context.industry || '',
  core_offerings: [...(context.core_offerings || [])],
  business_objects: [...(context.business_objects || [])],
  business_processes: [...(context.business_processes || [])],
  customer_types: [...(context.customer_types || [])],
  operating_regions: [...(context.operating_regions || [])],
  special_terms: [...(context.special_terms || [])],
  data_governance_constraints: [...(context.data_governance_constraints || [])],
})

const assignmentStateOptions: Array<{ value: AssignmentState; label: string }> = [
  { value: 'effective', label: '当前有效' },
  { value: 'upcoming', label: '未生效' },
  { value: 'expired', label: '已到期' },
  { value: 'disabled', label: '已停用' },
  { value: 'all', label: '全部任职' },
]

export default function OrganizationAccountsPage() {
  const user = useAuthStore(state => state.user)
  const permissions = useMemo(() => user?.permissions || [], [user?.permissions])
  const hasCapability = useCallback(
    (code: string) => permissions.includes('*') || permissions.includes(code),
    [permissions],
  )
  const canViewOrg = hasCapability('org:view')
  const canManageOrg = hasCapability('org:manage')
  const canViewUsers = hasCapability('user:view')
  const canManageUsers = hasCapability('user:manage')
  const canManageSemanticAccess = hasCapability('semantic_access:manage')

  const [orgs, setOrgs] = useState<OrgUnit[]>([])
  const [positions, setPositions] = useState<PositionSummary[]>([])
  const [assignments, setAssignments] = useState<AccountAssignment[]>([])
  const [unassignedAccounts, setUnassignedAccounts] = useState<Account[]>([])
  const [allAccounts, setAllAccounts] = useState<Account[]>([])
  const [allPositions, setAllPositions] = useState<PositionSummary[]>([])
  const [selectedOrgId, setSelectedOrgId] = useState<number | null>(null)
  const [showUnassigned, setShowUnassigned] = useState(false)
  const [expandedOrgIds, setExpandedOrgIds] = useState<Set<number>>(new Set())
  const [openPositionId, setOpenPositionId] = useState<number | null>(null)
  const [assignmentState, setAssignmentState] = useState<AssignmentState>('effective')
  const [accountSearch, setAccountSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [contentLoading, setContentLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [orgDialog, setOrgDialog] = useState<OrgDialogState | null>(null)
  const [positionDialog, setPositionDialog] = useState<PositionDialogState | null>(null)
  const [assignmentDialog, setAssignmentDialog] = useState<AssignmentDialogState | null>(null)
  const [accountDialog, setAccountDialog] = useState<AccountDialogState | null>(null)
  const [deleteState, setDeleteState] = useState<DeleteState | null>(null)
  const [mobileTreeOpen, setMobileTreeOpen] = useState(false)
  const [businessContext, setBusinessContext] = useState<BusinessContext>(emptyBusinessContext)
  const [profiles, setProfiles] = useState<OrgSemanticProfile[]>([])
  const [contextDialogOpen, setContextDialogOpen] = useState(false)
  const [contextDraft, setContextDraft] = useState<BusinessContextDraft>(emptyBusinessContextDraft)
  const [profileDraft, setProfileDraft] = useState<OrgSemanticProfile | null>(null)
  const [profileConfirm, setProfileConfirm] = useState<ProfileConfirmState | null>(null)
  const [profileViewerOrgId, setProfileViewerOrgId] = useState<number | null>(null)

  const orgTree = useMemo(() => buildOrgTree(orgs), [orgs])
  const selectedOrg = useMemo(() => orgs.find(item => item.id === selectedOrgId) || null, [orgs, selectedOrgId])
  const selectedTopOrgId = useMemo(() => {
    if (!selectedOrg) return null
    let current = selectedOrg
    while (current.parent_id != null) {
      const parent = orgs.find(item => item.id === current.parent_id)
      if (!parent) break
      current = parent
    }
    return current.id
  }, [orgs, selectedOrg])
  const selectedProfile = useMemo(
    () => profiles.find(item => item.org_unit.id === selectedTopOrgId) || null,
    [profiles, selectedTopOrgId],
  )
  const viewedProfile = useMemo(
    () => profiles.find(item => item.org_unit.id === profileViewerOrgId) || null,
    [profiles, profileViewerOrgId],
  )
  const peerTopDepartments = useMemo(
    () => orgs.filter(item => (
      item.parent_id == null
      && item.status
      && item.id !== (profileDraft?.org_unit.id ?? profileViewerOrgId)
    )),
    [orgs, profileDraft?.org_unit.id, profileViewerOrgId],
  )
  const visibleAssignments = useMemo(() => {
    const query = accountSearch.trim().toLowerCase()
    if (!query) return assignments
    return assignments.filter(item => `${item.username} ${item.email || ''}`.toLowerCase().includes(query))
  }, [accountSearch, assignments])
  const visibleUnassigned = useMemo(() => {
    const query = accountSearch.trim().toLowerCase()
    if (!query) return unassignedAccounts
    return unassignedAccounts.filter(item => `${item.username} ${item.email || ''}`.toLowerCase().includes(query))
  }, [accountSearch, unassignedAccounts])

  const loadOrganizations = useCallback(async () => {
    if (!canViewOrg) return
    const data = await authorizationRequest<OrgUnit[]>('/authorization/org-units')
    setOrgs(data)
    setExpandedOrgIds(current => {
      if (current.size) return current
      return new Set(data.filter(item => item.parent_id == null).map(item => item.id))
    })
    setSelectedOrgId(current => current && data.some(item => item.id === current) ? current : data[0]?.id ?? null)
  }, [canViewOrg])

  const loadUnassigned = useCallback(async () => {
    if (!canViewUsers) return
    try {
      const data = await authorizationRequest<Account[]>('/authorization/users?assignment_state=unassigned')
      setUnassignedAccounts(data)
    } catch (exc) {
      if (!(exc instanceof AuthorizationApiError && exc.status === 403)) throw exc
      setUnassignedAccounts([])
    }
  }, [canViewUsers])

  const loadOrganizationSemantics = useCallback(async () => {
    if (!canManageOrg || !canManageSemanticAccess) return
    const [context, profileResult] = await Promise.all([
      authorizationRequest<BusinessContext>('/authorization/business-context'),
      authorizationRequest<{ items: OrgSemanticProfile[] }>('/authorization/org-semantic-profiles'),
    ])
    setBusinessContext(context)
    setContextDraft(businessContextDraftFrom(context))
    setProfiles(profileResult.items)
  }, [canManageOrg, canManageSemanticAccess])

  const refreshBase = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      await Promise.all([loadOrganizations(), loadUnassigned(), loadOrganizationSemantics()])
    } catch (exc) {
      setError(apiErrorMessage(exc, '组织与账号数据加载失败'))
    } finally {
      setLoading(false)
    }
  }, [loadOrganizations, loadOrganizationSemantics, loadUnassigned])

  useEffect(() => { void refreshBase() }, [refreshBase])

  useEffect(() => {
    if (!profiles.some(item => ['generating', 'stale'].includes(item.status))) return
    const timer = window.setInterval(() => {
      void loadOrganizationSemantics().catch(() => undefined)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [loadOrganizationSemantics, profiles])

  useEffect(() => {
    if (!selectedOrgId || showUnassigned) return
    setContentLoading(true)
    setError('')
    authorizationRequest<PositionSummary[]>(`/authorization/positions?org_unit_id=${selectedOrgId}`)
      .then(data => {
        setPositions(data)
        setOpenPositionId(current => current && data.some(item => item.id === current) ? current : null)
      })
      .catch(exc => setError(apiErrorMessage(exc, '岗位加载失败')))
      .finally(() => setContentLoading(false))
  }, [selectedOrgId, showUnassigned])

  const loadAssignments = useCallback(async (positionId: number, state = assignmentState) => {
    if (!canViewUsers) return
    setContentLoading(true)
    setError('')
    try {
      const data = await authorizationRequest<AccountAssignment[]>(
        `/authorization/assignments?position_id=${positionId}&state=${state}`,
      )
      setAssignments(data)
    } catch (exc) {
      setError(apiErrorMessage(exc, '账号任职加载失败'))
    } finally {
      setContentLoading(false)
    }
  }, [assignmentState, canViewUsers])

  useEffect(() => {
    if (openPositionId && !showUnassigned) void loadAssignments(openPositionId)
  }, [assignmentState, loadAssignments, openPositionId, showUnassigned])

  if (!canViewOrg) return <Navigate to="/unauthorized" replace />

  const selectOrg = (orgId: number) => {
    setSelectedOrgId(orgId)
    setShowUnassigned(false)
    setOpenPositionId(null)
    setAssignments([])
    setAccountSearch('')
    setMobileTreeOpen(false)
  }

  const selectUnassigned = () => {
    setShowUnassigned(true)
    setOpenPositionId(null)
    setAssignments([])
    setAccountSearch('')
    setMobileTreeOpen(false)
    void loadUnassigned().catch(exc => setError(apiErrorMessage(exc, '待分配账号加载失败')))
  }

  const togglePosition = (positionId: number) => {
    if (openPositionId === positionId) {
      setOpenPositionId(null)
      setAssignments([])
      return
    }
    setOpenPositionId(positionId)
    setAssignmentState('effective')
    setAccountSearch('')
    void loadAssignments(positionId, 'effective')
  }

  const submitOrg = async () => {
    if (!orgDialog?.name.trim()) return
    setSubmitting(true)
    setError('')
    try {
      const item = orgDialog.item
      const body = {
        name: orgDialog.name.trim(),
        code: item?.code || null,
        parent_id: orgDialog.parentId,
        leader_id: item?.leader_id || null,
        order_num: item?.order_num || 0,
        status: orgDialog.status,
      }
      await authorizationRequest(item ? `/authorization/org-units/${item.id}` : '/authorization/org-units', {
        method: item ? 'PATCH' : 'POST',
        body: JSON.stringify(body),
      })
      setOrgDialog(null)
      await loadOrganizations()
    } catch (exc) {
      setError(apiErrorMessage(exc, '部门保存失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const submitPosition = async () => {
    if (!positionDialog?.name.trim()) return
    setSubmitting(true)
    setError('')
    try {
      const item = positionDialog.item
      await authorizationRequest(item ? `/authorization/positions/${item.id}` : '/authorization/positions', {
        method: item ? 'PATCH' : 'POST',
        body: JSON.stringify({
          name: positionDialog.name.trim(),
          org_unit_id: positionDialog.orgUnitId,
          code: item?.code || null,
          status: item?.status ?? true,
        }),
      })
      setPositionDialog(null)
      if (selectedOrgId) {
        setPositions(await authorizationRequest<PositionSummary[]>(`/authorization/positions?org_unit_id=${selectedOrgId}`))
      }
    } catch (exc) {
      setError(apiErrorMessage(exc, '岗位保存失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const submitBusinessContext = async () => {
    setSubmitting(true)
    setError('')
    try {
      const updated = await authorizationRequest<BusinessContext>('/authorization/business-context', {
        method: 'PUT',
        body: JSON.stringify({
          ...contextDraft,
          content: contextDraft.content.trim(),
          industry: contextDraft.industry.trim(),
          expected_revision: businessContext.revision,
        }),
      })
      setBusinessContext(updated)
      setContextDialogOpen(false)
      await loadOrganizationSemantics()
    } catch (exc) {
      setError(apiErrorMessage(exc, '企业业务背景保存失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const openProfileEditor = (profile: OrgSemanticProfile) => {
    const editable = structuredClone(profile)
    const source = editable.draft_version || editable.active_version
    if (!source) return
    const peerNames = orgs
      .filter(item => item.parent_id == null && item.status && item.id !== profile.org_unit.id)
      .map(item => item.name)
    const content = {
      ...source.content,
      collaborations: restrictCollaborations(
        source.content.collaborations || [],
        peerNames,
      ),
    }
    editable.draft_version = editable.draft_version
      ? { ...editable.draft_version, content }
      : {
          ...source,
          id: 0,
          version: source.version + 1,
          status: 'draft',
          source: 'human_edited',
          content,
        }
    setProfileDraft(editable)
  }

  const saveProfileDraft = async () => {
    if (!profileDraft?.draft_version) return
    setSubmitting(true)
    setError('')
    try {
      await authorizationRequest(
        `/authorization/org-semantic-profiles/${profileDraft.org_unit.id}/draft`,
        {
          method: 'PATCH',
          body: JSON.stringify({
            content: profileDraft.draft_version.content,
            expected_revision: profileDraft.revision,
          }),
        },
      )
      setProfileDraft(null)
      await loadOrganizationSemantics()
    } catch (exc) {
      setError(apiErrorMessage(exc, '部门职责画像保存失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const confirmProfile = async () => {
    if (!profileConfirm) return
    const { profile } = profileConfirm
    setSubmitting(true)
    setError('')
    try {
      await authorizationRequest(
        `/authorization/org-semantic-profiles/${profile.org_unit.id}/confirm`,
        {
          method: 'POST',
          body: JSON.stringify({
            expected_revision: profile.revision,
          }),
        },
      )
      setProfileConfirm(null)
      await loadOrganizationSemantics()
    } catch (exc) {
      setError(apiErrorMessage(exc, '部门职责画像确认失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const retryProfile = async (profile: OrgSemanticProfile) => {
    setSubmitting(true)
    try {
      await authorizationRequest(`/authorization/org-semantic-profiles/${profile.org_unit.id}/retry`, {
        method: 'POST',
      })
      await loadOrganizationSemantics()
    } catch (exc) {
      setError(apiErrorMessage(exc, '画像重新生成失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const openAddAssignment = async (positionId: number) => {
    setError('')
    try {
      const accounts = await authorizationRequest<Account[]>('/authorization/users')
      setAllAccounts(accounts)
      setAssignmentDialog({
        mode: 'add', positionId, tab: 'existing', userId: '', username: '', email: '', password: '',
        isPrimary: false, startsAt: toLocalInput(), endsAt: '', status: true,
      })
    } catch (exc) {
      setError(apiErrorMessage(exc, '账号列表加载失败'))
    }
  }

  const openAssignUnassigned = async (account: Account) => {
    setError('')
    try {
      const availablePositions = await authorizationRequest<PositionSummary[]>('/authorization/positions')
      setAllPositions(availablePositions)
      setAssignmentDialog({
        mode: 'assign-unassigned', positionId: availablePositions[0]?.id || 0, fixedAccount: account,
        tab: 'existing', userId: account.id, username: '', email: '', password: '', isPrimary: false,
        startsAt: toLocalInput(), endsAt: '', status: true,
      })
    } catch (exc) {
      setError(apiErrorMessage(exc, '可用岗位加载失败'))
    }
  }

  const submitAssignment = async () => {
    if (!assignmentDialog) return
    setSubmitting(true)
    setError('')
    try {
      if (assignmentDialog.tab === 'new' && assignmentDialog.mode === 'add') {
        await authorizationRequest('/authorization/users', {
          method: 'POST',
          body: JSON.stringify({
            username: assignmentDialog.username.trim(),
            email: assignmentDialog.email.trim() || null,
            password: assignmentDialog.password,
            position_id: assignmentDialog.positionId,
            starts_at: new Date(assignmentDialog.startsAt).toISOString(),
            ends_at: assignmentDialog.endsAt ? new Date(assignmentDialog.endsAt).toISOString() : null,
          }),
        })
      } else {
        const existing = assignmentDialog.assignment
        await authorizationRequest(existing ? `/authorization/assignments/${existing.id}` : '/authorization/assignments', {
          method: existing ? 'PATCH' : 'POST',
          body: JSON.stringify({
            user_id: assignmentDialog.fixedAccount?.id || assignmentDialog.userId,
            position_id: assignmentDialog.positionId,
            is_primary: assignmentDialog.isPrimary,
            starts_at: new Date(assignmentDialog.startsAt).toISOString(),
            ends_at: assignmentDialog.endsAt ? new Date(assignmentDialog.endsAt).toISOString() : null,
            status: assignmentDialog.status,
          }),
        })
      }
      const targetPositionId = assignmentDialog.positionId
      const wasUnassigned = assignmentDialog.mode === 'assign-unassigned'
      setAssignmentDialog(null)
      await Promise.all([loadUnassigned(), loadOrganizations()])
      if (!wasUnassigned && openPositionId === targetPositionId) await loadAssignments(targetPositionId)
      if (selectedOrgId && !showUnassigned) {
        setPositions(await authorizationRequest<PositionSummary[]>(`/authorization/positions?org_unit_id=${selectedOrgId}`))
      }
    } catch (exc) {
      setError(apiErrorMessage(exc, '任职保存失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const submitAccount = async () => {
    if (!accountDialog) return
    setSubmitting(true)
    setError('')
    try {
      const body = accountDialog.mode === 'password'
        ? { password: accountDialog.password }
        : { username: accountDialog.username.trim(), email: accountDialog.email.trim() || null }
      await authorizationRequest(`/authorization/users/${accountDialog.account.id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
      setAccountDialog(null)
      if (showUnassigned) await loadUnassigned()
      else if (openPositionId) await loadAssignments(openPositionId)
    } catch (exc) {
      setError(apiErrorMessage(exc, '账号保存失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleteState) return
    setSubmitting(true)
    setError('')
    try {
      const { kind, id } = deleteState
      if (kind === 'org') await authorizationRequest(`/authorization/org-units/${id}`, { method: 'DELETE' })
      if (kind === 'position') await authorizationRequest(`/authorization/positions/${id}`, { method: 'DELETE' })
      if (kind === 'assignment') await authorizationRequest(`/authorization/assignments/${id}`, { method: 'DELETE' })
      if (kind === 'account') {
        await authorizationRequest(`/authorization/users/${id}?reason=${encodeURIComponent('管理员停用账号')}`, { method: 'DELETE' })
      }
      setDeleteState(null)
      await Promise.all([loadOrganizations(), loadUnassigned()])
      if (selectedOrgId && !showUnassigned) {
        setPositions(await authorizationRequest<PositionSummary[]>(`/authorization/positions?org_unit_id=${selectedOrgId}`))
      }
      if (openPositionId && kind !== 'position') await loadAssignments(openPositionId)
    } catch (exc) {
      if (exc instanceof AuthorizationApiError && exc.blockers) {
        setDeleteState(current => current ? { ...current, blockers: exc.blockers } : current)
      } else {
        setError(apiErrorMessage(exc, '删除失败'))
      }
    } finally {
      setSubmitting(false)
    }
  }

  const departmentPanel = (
    <DepartmentPanel
      tree={orgTree}
      selectedOrgId={selectedOrgId}
      showUnassigned={showUnassigned}
      unassignedCount={unassignedAccounts.length}
      expandedOrgIds={expandedOrgIds}
      canManage={canManageOrg}
      onToggle={orgId => setExpandedOrgIds(current => {
        const next = new Set(current)
        if (next.has(orgId)) next.delete(orgId)
        else next.add(orgId)
        return next
      })}
      onSelect={selectOrg}
      onSelectUnassigned={selectUnassigned}
      onCreateRoot={() => setOrgDialog({ mode: 'root', parentId: null, name: '', status: true })}
      onCreateChild={org => setOrgDialog({ mode: 'child', parentId: org.id, name: '', status: true })}
      onEdit={org => setOrgDialog({ mode: 'edit', item: org, parentId: org.parent_id, name: org.name, status: org.status })}
      onDelete={org => setDeleteState({ kind: 'org', id: org.id, name: org.name })}
    />
  )

  return (
    <TooltipProvider delayDuration={250}>
      <div className="flex-1 min-h-0 overflow-auto bg-manus p-4 md:p-6">
        <div className="mx-auto max-w-[1500px] space-y-5">
          <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-manus-subtle">系统管理</div>
              <h1 className="text-2xl font-semibold tracking-tight text-manus-text">组织与账号管理</h1>
              <p className="mt-1 text-sm text-manus-muted">从部门进入岗位，在同一处维护账号与任职关系。</p>
            </div>
            <Button variant="outline" onClick={() => void refreshBase()} disabled={loading} className="border-manus-border bg-manus-secondary">
              <RefreshCw className={cn(loading && 'animate-spin')} />刷新
            </Button>
          </header>

          {error && (
            <div className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
              <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
              <button className="ml-auto text-red-200 hover:text-white" onClick={() => setError('')}>关闭</button>
            </div>
          )}

          {canManageOrg && canManageSemanticAccess && (
            <Card className={cn(
              'flex flex-col gap-4 border-manus-border bg-manus-secondary p-5 sm:flex-row sm:items-center sm:justify-between',
              businessContext.quality.level === 'partial' && 'border-amber-500/30 bg-amber-500/5',
            )}>
              <div className="flex min-w-0 items-start gap-3">
                <div className="mt-0.5 rounded-lg bg-accent/15 p-2 text-accent"><Sparkles className="h-4 w-4" /></div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="font-medium text-manus-text">企业业务事实</div>
                    <StatusPill tone={
                      businessContext.quality.level === 'sufficient'
                        ? 'green'
                        : businessContext.quality.level === 'partial'
                          ? 'amber'
                          : 'neutral'
                    }>
                      {businessContextQualityLabel(businessContext.quality.level)}
                    </StatusPill>
                    <span className="text-xs text-manus-subtle">
                      建议事实 {businessContext.quality.required_completed}/{businessContext.quality.required_total}
                    </span>
                  </div>
                  <div className="mt-2 h-1.5 w-56 max-w-full overflow-hidden rounded-full bg-manus-tertiary">
                    <div
                      className={cn(
                        'h-full rounded-full transition-[width]',
                        businessContext.quality.level === 'sufficient' ? 'bg-emerald-400' : 'bg-accent',
                      )}
                      style={{ width: `${businessContext.quality.completeness_percent}%` }}
                    />
                  </div>
                </div>
              </div>
              <Button variant={businessContext.exists ? 'outline' : 'default'} onClick={() => {
                setContextDraft(businessContextDraftFrom(businessContext))
                setContextDialogOpen(true)
              }}>{businessContext.exists ? '编辑业务事实' : '完善业务事实'}</Button>
            </Card>
          )}

          <div className="lg:hidden">
            <Sheet open={mobileTreeOpen} onOpenChange={setMobileTreeOpen}>
              <SheetTrigger asChild>
                <Button variant="outline" className="w-full justify-start border-manus-border bg-manus-secondary">
                  <Menu />选择部门
                  <span className="ml-auto text-manus-muted">{showUnassigned ? '待分配账号' : selectedOrg?.name || '未选择'}</span>
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="border-manus-border bg-manus-secondary p-0">
                <SheetHeader className="sr-only"><SheetTitle>部门列表</SheetTitle></SheetHeader>
                {departmentPanel}
              </SheetContent>
            </Sheet>
          </div>

          <div className="grid min-h-[620px] grid-cols-1 gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
            <Card className="hidden overflow-hidden border-manus-border bg-manus-secondary lg:block">{departmentPanel}</Card>
            <Card className="overflow-hidden border-manus-border bg-manus-secondary">
              {loading ? (
                <CenteredState icon={<Loader2 className="animate-spin" />} title="正在加载组织信息" />
              ) : showUnassigned ? (
                <UnassignedPanel
                  accounts={visibleUnassigned}
                  search={accountSearch}
                  canManage={canManageUsers}
                  onSearch={setAccountSearch}
                  onAssign={account => void openAssignUnassigned(account)}
                  onEdit={account => setAccountDialog({ mode: 'edit', account, username: account.username, email: account.email || '', password: '' })}
                  onPassword={account => setAccountDialog({ mode: 'password', account, username: account.username, email: account.email || '', password: '' })}
                  onDisable={account => setDeleteState({ kind: 'account', id: account.id, name: account.username })}
                />
              ) : selectedOrg ? (
                <PositionsPanel
                  org={selectedOrg}
                  positions={positions}
                  openPositionId={openPositionId}
                  assignments={visibleAssignments}
                  assignmentState={assignmentState}
                  accountSearch={accountSearch}
                  contentLoading={contentLoading}
                  profile={selectedProfile}
                  canManageOrg={canManageOrg}
                  canViewUsers={canViewUsers}
                  canManageUsers={canManageUsers}
                  onCreate={() => setPositionDialog({ name: '', orgUnitId: selectedOrg.id })}
                  onEdit={position => setPositionDialog({ item: position, name: position.name, orgUnitId: position.org_unit_id })}
                  onDelete={position => setDeleteState({ kind: 'position', id: position.id, name: position.name })}
                  onToggle={togglePosition}
                  onAddAccount={positionId => void openAddAssignment(positionId)}
                  onAssignmentState={setAssignmentState}
                  onSearch={setAccountSearch}
                  onEditAccount={account => setAccountDialog({ mode: 'edit', account, username: account.username, email: account.email || '', password: '' })}
                  onPassword={account => setAccountDialog({ mode: 'password', account, username: account.username, email: account.email || '', password: '' })}
                  onDisableAccount={account => setDeleteState({ kind: 'account', id: account.id, name: account.username })}
                  onEditAssignment={item => setAssignmentDialog({
                    mode: 'edit', assignment: item, positionId: item.position_id, tab: 'existing', userId: item.user_id,
                    username: '', email: '', password: '', isPrimary: item.is_primary, startsAt: toLocalInput(item.starts_at),
                    endsAt: item.ends_at ? toLocalInput(item.ends_at) : '', status: item.status,
                  })}
                  onRemoveAssignment={item => setDeleteState({ kind: 'assignment', id: item.id, name: `${item.username} 的当前任职` })}
                  onOpenProfile={profile => setProfileViewerOrgId(profile.org_unit.id)}
                />
              ) : (
                <CenteredState icon={<Building2 />} title="暂无部门" description="请先在左侧新增一级部门。" />
              )}
            </Card>
          </div>
        </div>
      </div>

      <OrgEditorDialog state={orgDialog} orgs={orgs} submitting={submitting} onChange={setOrgDialog} onSubmit={() => void submitOrg()} />
      <PositionEditorDialog state={positionDialog} orgs={orgs} submitting={submitting} onChange={setPositionDialog} onSubmit={() => void submitPosition()} />
      <AssignmentEditorDialog
        state={assignmentDialog}
        accounts={allAccounts}
        positions={allPositions}
        submitting={submitting}
        onChange={setAssignmentDialog}
        onSubmit={() => void submitAssignment()}
      />
      <AccountEditorDialog state={accountDialog} submitting={submitting} onChange={setAccountDialog} onSubmit={() => void submitAccount()} />
      <DeleteDialog state={deleteState} submitting={submitting} onChange={setDeleteState} onConfirm={() => void confirmDelete()} />
      <Dialog open={contextDialogOpen} onOpenChange={setContextDialogOpen}>
        <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto border-manus-border bg-manus-secondary">
          <DialogHeader>
            <DialogTitle>企业业务事实</DialogTitle>
            <DialogDescription>
              结构化事实能减少AI套用通用部门模板。所有字段均可跳过；部门画像仍会生成，并明确标记待确认假设。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-5">
            <div className="rounded-xl border border-accent/25 bg-accent/5 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-manus-text">建议优先补充三项核心事实</div>
                  <div className="mt-1 text-xs text-manus-muted">所属行业、核心产品或服务、核心业务对象</div>
                </div>
                <StatusPill tone={
                  contextDraftCoreCount(contextDraft) === 3
                    ? 'green'
                    : contextDraftCoreCount(contextDraft) > 0
                      ? 'amber'
                      : 'neutral'
                }>{contextDraftCoreCount(contextDraft)}/3</StatusPill>
              </div>
              <div className="mt-3 grid gap-4 sm:grid-cols-2">
                <Field label="所属行业（建议）">
                  <Input
                    value={contextDraft.industry}
                    onChange={event => setContextDraft({ ...contextDraft, industry: event.target.value })}
                    maxLength={200}
                    placeholder="例如：工业设备制造与售后服务"
                    className="border-manus-border bg-manus-tertiary"
                  />
                </Field>
                <BusinessFactListField
                  label="核心产品或服务（建议）"
                  value={contextDraft.core_offerings}
                  onChange={core_offerings => setContextDraft({ ...contextDraft, core_offerings })}
                  placeholder={'工业设备生产\n设备安装与售后维保'}
                />
                <BusinessFactListField
                  label="核心业务对象（建议）"
                  value={contextDraft.business_objects}
                  onChange={business_objects => setContextDraft({ ...contextDraft, business_objects })}
                  placeholder={'客户\n订单\n设备\n供应商'}
                />
                <BusinessFactListField
                  label="主要业务流程"
                  value={contextDraft.business_processes}
                  onChange={business_processes => setContextDraft({ ...contextDraft, business_processes })}
                  placeholder="线索→订单→生产→交付→售后"
                />
              </div>
            </div>

            <div>
              <div className="mb-3 text-xs font-medium uppercase tracking-[0.14em] text-manus-subtle">可选补充事实</div>
              <div className="grid gap-4 sm:grid-cols-2">
                <BusinessFactListField
                  label="客户类型"
                  value={contextDraft.customer_types}
                  onChange={customer_types => setContextDraft({ ...contextDraft, customer_types })}
                  placeholder={'企业客户\n政府客户'}
                />
                <BusinessFactListField
                  label="经营区域"
                  value={contextDraft.operating_regions}
                  onChange={operating_regions => setContextDraft({ ...contextDraft, operating_regions })}
                  placeholder={'全国\n华东区域\n海外'}
                />
                <BusinessFactListField
                  label="特殊组织术语"
                  value={contextDraft.special_terms}
                  onChange={special_terms => setContextDraft({ ...contextDraft, special_terms })}
                  placeholder="总经办：承担战略与法务协调"
                />
                <BusinessFactListField
                  label="数据治理约束"
                  value={contextDraft.data_governance_constraints}
                  onChange={data_governance_constraints => setContextDraft({
                    ...contextDraft,
                    data_governance_constraints,
                  })}
                  placeholder={'薪酬数据仅人事与管理层使用\n客户联系方式属于受限数据'}
                />
              </div>
            </div>

            <Field label="其他背景说明（可选）">
              <textarea
                value={contextDraft.content}
                onChange={event => setContextDraft({ ...contextDraft, content: event.target.value })}
                maxLength={8000}
                rows={5}
                placeholder="补充企业特有的职责边界、协作方式或无法结构化描述的业务信息"
                className="w-full resize-y rounded-lg border border-manus-border bg-manus-tertiary p-3 text-sm text-manus-text outline-none focus:border-accent"
              />
              <div className="mt-1 text-right text-xs text-manus-muted">{contextDraft.content.length}/8000</div>
            </Field>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setContextDialogOpen(false)}>取消</Button>
            <Button onClick={() => void submitBusinessContext()} disabled={submitting}>
              {submitting && <Loader2 className="animate-spin" />}保存并更新画像
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ProfileDetailsDialog
        open={profileViewerOrgId != null}
        profile={viewedProfile}
        selectedOrgName={selectedOrg?.name || ''}
        submitting={submitting}
        peerTopDepartments={peerTopDepartments}
        onOpenChange={open => !open && setProfileViewerOrgId(null)}
        onEdit={openProfileEditor}
        onConfirm={profile => setProfileConfirm({ profile })}
        onRetry={profile => void retryProfile(profile)}
      />
      <ProfileEditorDialog
        profile={profileDraft}
        peerTopDepartments={peerTopDepartments}
        submitting={submitting}
        onChange={setProfileDraft}
        onSubmit={() => void saveProfileDraft()}
      />
      <ProfileConfirmDialog
        state={profileConfirm}
        submitting={submitting}
        onChange={setProfileConfirm}
        onConfirm={() => void confirmProfile()}
      />
    </TooltipProvider>
  )
}

function DepartmentPanel({
  tree, selectedOrgId, showUnassigned, unassignedCount, expandedOrgIds, canManage,
  onToggle, onSelect, onSelectUnassigned, onCreateRoot, onCreateChild, onEdit, onDelete,
}: {
  tree: OrgTreeNode[]
  selectedOrgId: number | null
  showUnassigned: boolean
  unassignedCount: number
  expandedOrgIds: Set<number>
  canManage: boolean
  onToggle: (orgId: number) => void
  onSelect: (orgId: number) => void
  onSelectUnassigned: () => void
  onCreateRoot: () => void
  onCreateChild: (org: OrgUnit) => void
  onEdit: (org: OrgUnit) => void
  onDelete: (org: OrgUnit) => void
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-manus-border px-4 py-4">
        <div>
          <div className="font-medium text-manus-text">部门</div>
          <div className="mt-0.5 text-xs text-manus-muted">选择部门查看直属岗位</div>
        </div>
        {canManage && (
          <Button size="sm" onClick={onCreateRoot}><Plus />一级部门</Button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {tree.length ? tree.map(node => (
          <DepartmentNode
            key={node.id}
            node={node}
            depth={0}
            selectedOrgId={selectedOrgId}
            expandedOrgIds={expandedOrgIds}
            canManage={canManage}
            onToggle={onToggle}
            onSelect={onSelect}
            onCreateChild={onCreateChild}
            onEdit={onEdit}
            onDelete={onDelete}
          />
        )) : <div className="px-3 py-12 text-center text-sm text-manus-muted">暂无部门</div>}
      </div>
      <div className="border-t border-manus-border p-2">
        <button
          className={cn(
            'flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm transition-colors',
            showUnassigned ? 'bg-accent/15 text-accent-muted' : 'text-manus-muted hover:bg-manus-hover hover:text-manus-text',
          )}
          onClick={onSelectUnassigned}
        >
          <Inbox className="h-4 w-4" />
          <span className="flex-1">待分配账号</span>
          <span className="rounded-full bg-manus-tertiary px-2 py-0.5 text-xs">{unassignedCount}</span>
        </button>
      </div>
    </div>
  )
}

function DepartmentNode({
  node, depth, selectedOrgId, expandedOrgIds, canManage, onToggle, onSelect, onCreateChild, onEdit, onDelete,
}: {
  node: OrgTreeNode
  depth: number
  selectedOrgId: number | null
  expandedOrgIds: Set<number>
  canManage: boolean
  onToggle: (orgId: number) => void
  onSelect: (orgId: number) => void
  onCreateChild: (org: OrgUnit) => void
  onEdit: (org: OrgUnit) => void
  onDelete: (org: OrgUnit) => void
}) {
  const expanded = expandedOrgIds.has(node.id)
  const selected = selectedOrgId === node.id
  return (
    <div>
      <div
        className={cn(
          'group flex items-center rounded-lg pr-1 transition-colors',
          selected ? 'bg-accent/15 text-accent-muted' : 'text-manus-text hover:bg-manus-hover',
        )}
        style={{ paddingLeft: `${Math.min(depth, 6) * 16 + 4}px` }}
      >
        <button
          type="button"
          className="flex h-8 w-7 shrink-0 items-center justify-center rounded text-manus-muted hover:text-manus-text"
          onClick={() => node.children.length && onToggle(node.id)}
          aria-label={expanded ? `收起${node.name}` : `展开${node.name}`}
          disabled={!node.children.length}
        >
          {node.children.length ? expanded ? <ChevronDown /> : <ChevronRight /> : <span className="h-1 w-1 rounded-full bg-manus-subtle" />}
        </button>
        <button type="button" className="min-w-0 flex-1 truncate py-2 text-left text-sm" onClick={() => onSelect(node.id)}>
          {node.name}
        </button>
        {canManage && (
          <div className={cn('flex shrink-0 items-center transition-opacity', selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100')}>
            <IconAction label="新增子部门" icon={<FolderPlus />} onClick={() => onCreateChild(node)} />
            <IconAction label="编辑部门" icon={<Pencil />} onClick={() => onEdit(node)} />
            <IconAction label="删除部门" danger icon={<Trash2 />} onClick={() => onDelete(node)} />
          </div>
        )}
      </div>
      {expanded && node.children.map(child => (
        <DepartmentNode
          key={child.id}
          node={child}
          depth={depth + 1}
          selectedOrgId={selectedOrgId}
          expandedOrgIds={expandedOrgIds}
          canManage={canManage}
          onToggle={onToggle}
          onSelect={onSelect}
          onCreateChild={onCreateChild}
          onEdit={onEdit}
          onDelete={onDelete}
        />
      ))}
    </div>
  )
}

function PositionsPanel({
  org, positions, openPositionId, assignments, assignmentState, accountSearch, contentLoading,
  canManageOrg, canViewUsers, canManageUsers, onCreate, onEdit, onDelete, onToggle, onAddAccount,
  onAssignmentState, onSearch, onEditAccount, onPassword, onDisableAccount, onEditAssignment, onRemoveAssignment,
  profile, onOpenProfile,
}: {
  org: OrgUnit
  positions: PositionSummary[]
  openPositionId: number | null
  assignments: AccountAssignment[]
  assignmentState: AssignmentState
  accountSearch: string
  contentLoading: boolean
  profile: OrgSemanticProfile | null
  canManageOrg: boolean
  canViewUsers: boolean
  canManageUsers: boolean
  onCreate: () => void
  onEdit: (position: PositionSummary) => void
  onDelete: (position: PositionSummary) => void
  onToggle: (positionId: number) => void
  onAddAccount: (positionId: number) => void
  onAssignmentState: (state: AssignmentState) => void
  onSearch: (value: string) => void
  onEditAccount: (account: Account) => void
  onPassword: (account: Account) => void
  onDisableAccount: (account: Account) => void
  onEditAssignment: (assignment: AccountAssignment) => void
  onRemoveAssignment: (assignment: AccountAssignment) => void
  onOpenProfile: (profile: OrgSemanticProfile) => void
}) {
  return (
    <div className="min-h-full">
      <div className="flex flex-col gap-3 border-b border-manus-border px-5 py-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Building2 className="h-5 w-5 text-accent" />
            <h2 className="text-lg font-semibold text-manus-text">{org.name}</h2>
          </div>
          <p className="mt-1 text-sm text-manus-muted">{positions.length} 个直属岗位</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {profile && (
            <Button
              variant="outline"
              className="border-manus-border bg-manus-tertiary"
              onClick={() => onOpenProfile(profile)}
            >
              <Sparkles className="text-accent" />
              部门职责画像
              <StatusPill tone={
                  profile.status === 'confirmed' ? 'green'
                    : profile.status === 'failed' ? 'red'
                      : profile.status === 'draft' ? 'amber' : 'neutral'
                }>{profileStatusLabel(profile.status)}</StatusPill>
              <ChevronRight className="text-manus-muted" />
            </Button>
          )}
          {canManageOrg && <Button onClick={onCreate}><Plus />新增岗位</Button>}
        </div>
      </div>

      {contentLoading && positions.length === 0 ? (
        <CenteredState icon={<Loader2 className="animate-spin" />} title="正在加载岗位" />
      ) : positions.length === 0 ? (
        <CenteredState icon={<BriefcaseBusiness />} title="该部门还没有岗位" description={canManageOrg ? '新增岗位后即可安排账号任职。' : '当前账号只有查看权限。'} action={canManageOrg ? <Button onClick={onCreate}><Plus />新增岗位</Button> : undefined} />
      ) : (
        <div className="divide-y divide-manus-border">
          {positions.map(position => {
            const open = position.id === openPositionId
            return (
              <div key={position.id} className={cn(open && 'bg-black/10')}>
                <div
                  className="group flex cursor-pointer items-center gap-3 px-5 py-4 transition-colors hover:bg-manus-hover/60"
                  onClick={() => onToggle(position.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') onToggle(position.id) }}
                  aria-expanded={open}
                >
                  <div className={cn('flex h-8 w-8 items-center justify-center rounded-lg border', open ? 'border-accent/40 bg-accent/15 text-accent-muted' : 'border-manus-border bg-manus-tertiary text-manus-muted')}>
                    <BriefcaseBusiness className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-manus-text">{position.name}</div>
                    <div className="mt-0.5 text-xs text-manus-muted">{position.headcount} 个有效账号</div>
                  </div>
                  {canManageOrg && (
                    <div className="flex items-center" onClick={event => event.stopPropagation()}>
                      <IconAction label="编辑岗位" icon={<Pencil />} onClick={() => onEdit(position)} />
                      <IconAction label="删除岗位" danger icon={<Trash2 />} onClick={() => onDelete(position)} />
                    </div>
                  )}
                  {open ? <ChevronDown className="h-4 w-4 text-manus-muted" /> : <ChevronRight className="h-4 w-4 text-manus-muted" />}
                </div>
                {open && (
                  <div className="border-t border-manus-border bg-manus/40 px-5 py-5">
                    {!canViewUsers ? (
                      <CenteredState icon={<Users />} title="无账号查看权限" description="你可以查看岗位，但不能查看该岗位下的账号。" compact />
                    ) : (
                      <>
                        <div className="mb-4 flex flex-col gap-3 xl:flex-row xl:items-center">
                          <div className="relative min-w-0 flex-1">
                            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-manus-muted" />
                            <Input value={accountSearch} onChange={event => onSearch(event.target.value)} placeholder="搜索登录名或邮箱" className="border-manus-border bg-manus-secondary pl-9" />
                          </div>
                          <NativeSelect value={assignmentState} onChange={value => onAssignmentState(value as AssignmentState)} ariaLabel="任职状态">
                            {assignmentStateOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                          </NativeSelect>
                          {canManageUsers && <Button onClick={() => onAddAccount(position.id)}><UserPlus />添加账号</Button>}
                        </div>
                        {contentLoading ? (
                          <CenteredState icon={<Loader2 className="animate-spin" />} title="正在加载账号" compact />
                        ) : assignments.length === 0 ? (
                          <CenteredState icon={<Users />} title="没有符合条件的任职" description={assignmentState === 'effective' ? '可添加账号，或切换状态查看历史任职。' : '尝试切换其他任职状态。'} compact />
                        ) : (
                          <div className="overflow-hidden rounded-lg border border-manus-border bg-manus-secondary">
                            {assignments.map(item => (
                              <AssignmentRow
                                key={item.id}
                                assignment={item}
                                canManage={canManageUsers}
                                onEditAccount={onEditAccount}
                                onPassword={onPassword}
                                onDisable={onDisableAccount}
                                onEditAssignment={onEditAssignment}
                                onRemoveAssignment={onRemoveAssignment}
                              />
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function businessContextQualityLabel(level: BusinessContext['quality']['level']) {
  return {
    missing: '信息有限',
    partial: '部分完善',
    sufficient: '核心事实完整',
  }[level]
}

function contextDraftCoreCount(context: BusinessContextDraft) {
  return Number(Boolean(context.industry.trim()))
    + Number(context.core_offerings.length > 0)
    + Number(context.business_objects.length > 0)
}

function BusinessFactListField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string[]
  onChange: (value: string[]) => void
  placeholder: string
}) {
  return (
    <Field label={label}>
      <textarea
        value={value.join('\n')}
        onChange={event => onChange(
          event.target.value
            .split('\n')
            .map(item => item.trim())
            .filter(Boolean)
            .slice(0, 50),
        )}
        rows={3}
        placeholder={placeholder}
        className="w-full resize-y rounded-lg border border-manus-border bg-manus-tertiary p-3 text-sm text-manus-text outline-none placeholder:text-manus-subtle focus:border-accent"
      />
      <div className="mt-1 text-xs text-manus-subtle">每行一项，最多 50 项</div>
    </Field>
  )
}

type EditableProfileListKey =
  | 'responsibilities'
  | 'business_objects'
  | 'data_produced'
  | 'data_consumed'
  | 'collaborations'
  | 'boundaries'
  | 'keywords'

const profileListFields: Array<{ key: EditableProfileListKey; label: string }> = [
  { key: 'responsibilities', label: '核心职责' },
  { key: 'business_objects', label: '业务对象' },
  { key: 'data_produced', label: '生产数据' },
  { key: 'data_consumed', label: '使用数据' },
  { key: 'collaborations', label: '协作关系' },
  { key: 'boundaries', label: '职责边界' },
  { key: 'keywords', label: '关键词' },
]

function profileStatusLabel(status: OrgSemanticProfile['status']) {
  return {
    missing: '未生成',
    generating: '生成中',
    draft: '待确认',
    confirmed: '已确认',
    stale: '已过期',
    failed: '生成失败',
  }[status]
}

function ProfileDetailsDialog({
  open,
  profile,
  selectedOrgName,
  submitting,
  peerTopDepartments,
  onOpenChange,
  onEdit,
  onConfirm,
  onRetry,
}: {
  open: boolean
  profile: OrgSemanticProfile | null
  selectedOrgName: string
  submitting: boolean
  peerTopDepartments: OrgUnit[]
  onOpenChange: (open: boolean) => void
  onEdit: (profile: OrgSemanticProfile) => void
  onConfirm: (profile: OrgSemanticProfile) => void
  onRetry: (profile: OrgSemanticProfile) => void
}) {
  const peerNames = peerTopDepartments.map(item => item.name)
  const content = profile?.draft_version?.content || profile?.active_version?.content
  const visibleContent = content ? {
    ...content,
    collaborations: restrictCollaborations(content.collaborations || [], peerNames),
  } : undefined
  const previousContent = profile?.active_version?.content
    ? {
        ...profile.active_version.content,
        collaborations: restrictCollaborations(
          profile.active_version.content.collaborations || [],
          peerNames,
        ),
      }
    : undefined
  const draftContent = profile?.draft_version?.content
    ? {
        ...profile.draft_version.content,
        collaborations: restrictCollaborations(
          profile.draft_version.content.collaborations || [],
          peerNames,
        ),
      }
    : undefined
  const inherited = Boolean(
    profile
    && selectedOrgName
    && selectedOrgName !== profile.org_unit.name,
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto border-manus-border bg-manus-secondary">
        <DialogHeader>
          <div className="flex flex-wrap items-center gap-2">
            <DialogTitle>{profile?.org_unit.name || ''}部门职责画像</DialogTitle>
            {profile && (
              <StatusPill tone={
                profile.status === 'confirmed' ? 'green'
                  : profile.status === 'failed' ? 'red'
                    : profile.status === 'draft' ? 'amber' : 'neutral'
              }>{profileStatusLabel(profile.status)}</StatusPill>
            )}
          </div>
          <DialogDescription>
            {inherited
              ? `当前部门“${selectedOrgName}”沿用所属一级部门的职责画像。`
              : '查看部门职责边界、业务对象以及与其他一级部门的协作关系。'}
          </DialogDescription>
        </DialogHeader>

        {!profile || profile.status === 'missing' ? (
          <div className="rounded-xl border border-dashed border-manus-border bg-manus-tertiary p-6 text-center text-sm text-manus-muted">
            画像尚未生成。系统会在组织和岗位信息准备好后自动生成。
          </div>
        ) : profile.error_message ? (
          <div className="rounded-xl border border-red-500/25 bg-red-500/10 p-4 text-sm text-red-300">
            {profile.error_message}
          </div>
        ) : (
          <div className="space-y-4">
            {(profile.status === 'generating' || profile.status === 'stale') && (
              <div className="flex items-center gap-2 rounded-xl border border-accent/20 bg-accent/5 px-4 py-3 text-sm text-manus-muted">
                <Loader2 className="h-4 w-4 animate-spin text-accent" />
                {profile.status === 'stale'
                  ? '组织事实已变化，正在生成新版草案；已确认画像仍保持有效。'
                  : '正在根据组织事实生成职责画像…'}
              </div>
            )}
            {visibleContent && <ProfileSummary content={visibleContent} />}
            {previousContent && draftContent && (
              <ProfileDiff previous={previousContent} next={draftContent} />
            )}
          </div>
        )}

        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            关闭
          </Button>
          <div className="flex flex-wrap justify-end gap-2">
            {profile?.status === 'failed' && (
              <Button variant="outline" onClick={() => onRetry(profile)} disabled={submitting}>
                <RefreshCw />重新生成
              </Button>
            )}
            {profile && (profile.draft_version || profile.active_version) && (
              <Button variant="outline" onClick={() => onEdit(profile)} disabled={submitting}>
                <Pencil />编辑画像
              </Button>
            )}
            {profile?.draft_version && (
              <Button onClick={() => onConfirm(profile)} disabled={submitting}>
                <CheckCircle2 />确认画像
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ProfileSummary({ content }: { content?: ProfileContent }) {
  if (!content) return null
  return (
    <div className="mt-3 space-y-3 text-sm">
      <div><span className="text-manus-muted">部门定位：</span><span className="text-manus-text">{content.positioning}</span></div>
      <div className="grid gap-3 md:grid-cols-2">
        {profileListFields.slice(0, 6).map(field => (
          <div key={field.key}>
            <div className="text-xs font-medium text-manus-muted">{field.label}</div>
            <div className="mt-1 text-manus-text">{content[field.key].join('；') || '—'}</div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {content.keywords.map(item => <span key={item} className="rounded-full bg-accent/10 px-2 py-1 text-xs text-accent-muted">{item}</span>)}
      </div>
    </div>
  )
}

function ProfileDiff({ previous, next }: { previous: ProfileContent; next: ProfileContent }) {
  const changed = (['positioning', ...profileListFields.map(item => item.key)] as Array<keyof ProfileContent>)
    .filter(key => JSON.stringify(previous[key]) !== JSON.stringify(next[key]))
  if (!changed.length) return <div className="mt-3 rounded-lg bg-manus px-3 py-2 text-xs text-manus-muted">草案内容与已确认画像一致。</div>
  const labels: Partial<Record<keyof ProfileContent, string>> = {
    positioning: '部门定位',
    responsibilities: '核心职责',
    business_objects: '业务对象',
    data_produced: '生产数据',
    data_consumed: '使用数据',
    collaborations: '协作关系',
    boundaries: '职责边界',
    keywords: '关键词',
  }
  return (
    <div className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
      相比当前确认版发生变化：{changed.map(key => labels[key] || key).join('、')}
    </div>
  )
}

function ProfileEditorDialog({ profile, peerTopDepartments, submitting, onChange, onSubmit }: {
  profile: OrgSemanticProfile | null
  peerTopDepartments: OrgUnit[]
  submitting: boolean
  onChange: (profile: OrgSemanticProfile | null) => void
  onSubmit: () => void
}) {
  const content = profile?.draft_version?.content
  const setContent = (next: ProfileContent) => {
    if (!profile?.draft_version) return
    onChange({ ...profile, draft_version: { ...profile.draft_version, content: next } })
  }
  return (
    <Dialog open={Boolean(profile)} onOpenChange={open => !open && onChange(null)}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto border-manus-border bg-manus-secondary">
        <DialogHeader>
          <DialogTitle>编辑{profile?.org_unit.name || ''}职责画像</DialogTitle>
          <DialogDescription>保存后形成待确认草案；再次确认前，当前已确认画像与线上权限不会改变。</DialogDescription>
        </DialogHeader>
        {content && <div className="space-y-4">
          <Field label="部门定位">
            <textarea value={content.positioning} onChange={event => setContent({ ...content, positioning: event.target.value })} rows={3} className="w-full rounded-lg border border-manus-border bg-manus-tertiary p-3 text-sm text-manus-text" />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            {profileListFields.map(field => field.key === 'collaborations' ? (
              <Field key={field.key} label={field.label}>
                <div className="min-h-28 rounded-lg border border-manus-border bg-manus-tertiary p-3">
                  {peerTopDepartments.length ? (
                    <div className="grid gap-2">
                      {peerTopDepartments.map(department => {
                        const selected = (content.collaborations || []).some(
                          value => collaborationReferencesDepartment(value, department.name),
                        )
                        return (
                          <label
                            key={department.id}
                            className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-manus-text hover:bg-manus-hover"
                          >
                            <input
                              type="checkbox"
                              checked={selected}
                              onChange={event => setContent({
                                ...content,
                                collaborations: setCollaborationDepartment(
                                  content.collaborations || [],
                                  peerTopDepartments.map(item => item.name),
                                  department.name,
                                  event.target.checked,
                                ),
                              })}
                              className="h-4 w-4 accent-[var(--accent)]"
                            />
                            <Building2 className="h-4 w-4 text-manus-muted" />
                            <span>{department.name}</span>
                          </label>
                        )
                      })}
                    </div>
                  ) : (
                    <div className="flex min-h-20 items-center justify-center text-center text-xs text-manus-muted">
                      暂无其他已创建的一级部门
                    </div>
                  )}
                </div>
                <div className="mt-1 text-xs text-manus-subtle">仅可选择当前工作区已创建的其他一级部门</div>
              </Field>
            ) : (
              <Field key={field.key} label={field.label}>
                <textarea
                  value={(content[field.key] || []).join('\n')}
                  onChange={event => setContent({
                    ...content,
                    [field.key]: event.target.value.split('\n').map(item => item.trim()).filter(Boolean),
                  })}
                  rows={4}
                  className="w-full rounded-lg border border-manus-border bg-manus-tertiary p-3 text-sm text-manus-text"
                />
              </Field>
            ))}
          </div>
        </div>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onChange(null)}>取消</Button>
          <Button onClick={onSubmit} disabled={submitting || !content?.positioning.trim()}>
            {submitting && <Loader2 className="animate-spin" />}保存草案
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ProfileConfirmDialog({
  state,
  submitting,
  onChange,
  onConfirm,
}: {
  state: ProfileConfirmState | null
  submitting: boolean
  onChange: (state: ProfileConfirmState | null) => void
  onConfirm: () => void
}) {
  return (
    <Dialog open={Boolean(state)} onOpenChange={open => !open && onChange(null)}>
      <DialogContent className="max-w-xl border-manus-border bg-manus-secondary">
        <DialogHeader>
          <DialogTitle>确认{state?.profile.org_unit.name || ''}职责画像</DialogTitle>
          <DialogDescription>
            请确认部门定位、核心职责和职责边界准确。确认后，该版本将成为生成访问依据的有效组织事实。
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-lg border border-manus-border bg-manus-tertiary p-3 text-sm text-manus-muted">
          确认后，系统会根据该画像自动生成或更新访问依据，具体权限仍需在“问数数据权限”中审核。
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onChange(null)} disabled={submitting}>返回检查</Button>
          <Button onClick={onConfirm} disabled={submitting}>
            {submitting && <Loader2 className="animate-spin" />}确认画像
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AssignmentRow({ assignment, canManage, onEditAccount, onPassword, onDisable, onEditAssignment, onRemoveAssignment }: {
  assignment: AccountAssignment
  canManage: boolean
  onEditAccount: (account: Account) => void
  onPassword: (account: Account) => void
  onDisable: (account: Account) => void
  onEditAssignment: (assignment: AccountAssignment) => void
  onRemoveAssignment: (assignment: AccountAssignment) => void
}) {
  const account: Account = { id: assignment.user_id, username: assignment.username, email: assignment.email, disabled: assignment.disabled }
  const state = assignmentStateLabel(assignment)
  return (
    <div className="flex items-center gap-3 border-b border-manus-border px-4 py-3 last:border-b-0">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent/15 text-sm font-semibold text-accent-muted">
        {assignment.username.slice(0, 1).toUpperCase()}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-manus-text">{assignment.username}</span>
          <StatusPill tone={assignment.is_primary ? 'blue' : 'neutral'}>{assignment.is_primary ? '主任职' : '兼职'}</StatusPill>
          <StatusPill tone={state === '有效' ? 'green' : state === '已停用' ? 'red' : 'amber'}>{state}</StatusPill>
        </div>
        <div className="mt-1 truncate text-xs text-manus-muted">
          {assignment.email || '未填写邮箱'} · {formatDateTime(assignment.starts_at)} 至 {formatDateTime(assignment.ends_at)}
        </div>
      </div>
      {canManage && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild><Button size="icon" variant="ghost" aria-label="账号操作"><MoreHorizontal /></Button></DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem onSelect={() => onEditAccount(account)}><UserRoundCog className="mr-2 h-4 w-4" />编辑账号</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onPassword(account)}><KeyRound className="mr-2 h-4 w-4" />重置密码</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onEditAssignment(assignment)}><BriefcaseBusiness className="mr-2 h-4 w-4" />编辑任职</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onRemoveAssignment(assignment)}><UserMinus className="mr-2 h-4 w-4" />移出当前岗位</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-red-400 focus:text-red-300" onSelect={() => onDisable(account)}><UserX className="mr-2 h-4 w-4" />停用账号</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}

function UnassignedPanel({ accounts, search, canManage, onSearch, onAssign, onEdit, onPassword, onDisable }: {
  accounts: Account[]
  search: string
  canManage: boolean
  onSearch: (value: string) => void
  onAssign: (account: Account) => void
  onEdit: (account: Account) => void
  onPassword: (account: Account) => void
  onDisable: (account: Account) => void
}) {
  return (
    <div>
      <div className="border-b border-manus-border px-5 py-5">
        <div className="flex items-center gap-2"><Inbox className="h-5 w-5 text-accent" /><h2 className="text-lg font-semibold text-manus-text">待分配账号</h2></div>
        <p className="mt-1 text-sm text-manus-muted">{accounts.length} 个账号当前没有有效岗位任职</p>
      </div>
      <div className="p-5">
        <div className="relative mb-4">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-manus-muted" />
          <Input value={search} onChange={event => onSearch(event.target.value)} placeholder="搜索登录名或邮箱" className="border-manus-border bg-manus-tertiary pl-9" />
        </div>
        {accounts.length === 0 ? (
          <CenteredState icon={<Inbox />} title="没有待分配账号" description="所有可用账号都已经安排了岗位。" />
        ) : (
          <div className="overflow-hidden rounded-lg border border-manus-border">
            {accounts.map(account => (
              <div key={account.id} className="flex items-center gap-3 border-b border-manus-border px-4 py-3 last:border-b-0">
                <div className="flex h-9 w-9 items-center justify-center rounded-full bg-manus-tertiary text-sm font-semibold text-manus-text">{account.username.slice(0, 1).toUpperCase()}</div>
                <div className="min-w-0 flex-1"><div className="font-medium text-manus-text">{account.username}</div><div className="truncate text-xs text-manus-muted">{account.email || '未填写邮箱'}{account.disabled ? ' · 已停用' : ''}</div></div>
                {canManage && !account.disabled && <Button size="sm" variant="outline" onClick={() => onAssign(account)}><UserPlus />安排岗位</Button>}
                {canManage && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button size="icon" variant="ghost" aria-label="账号操作"><MoreHorizontal /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => onEdit(account)}>编辑账号</DropdownMenuItem>
                      <DropdownMenuItem onSelect={() => onPassword(account)}>重置密码</DropdownMenuItem>
                      {!account.disabled && <DropdownMenuItem className="text-red-400" onSelect={() => onDisable(account)}>停用账号</DropdownMenuItem>}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function OrgEditorDialog({ state, orgs, submitting, onChange, onSubmit }: {
  state: OrgDialogState | null
  orgs: OrgUnit[]
  submitting: boolean
  onChange: (state: OrgDialogState | null) => void
  onSubmit: () => void
}) {
  const excluded = state?.item ? descendantOrgIds(orgs, state.item.id) : new Set<number>()
  const title = state?.mode === 'root' ? '新增一级部门' : state?.mode === 'child' ? '新增子部门' : '编辑部门'
  return (
    <Dialog open={Boolean(state)} onOpenChange={open => !open && onChange(null)}>
      <DialogContent className="border-manus-border bg-manus-secondary">
        <DialogHeader><DialogTitle className="text-manus-text">{title}</DialogTitle><DialogDescription>维护部门名称与组织归属，编码由系统兼容保留。</DialogDescription></DialogHeader>
        {state && <div className="space-y-4">
          <Field label="部门名称"><Input autoFocus value={state.name} onChange={event => onChange({ ...state, name: event.target.value })} placeholder="请输入部门名称" /></Field>
          {state.mode !== 'root' && <Field label="上级部门"><NativeSelect value={state.parentId == null ? '' : String(state.parentId)} onChange={value => onChange({ ...state, parentId: value ? Number(value) : null })} ariaLabel="上级部门"><option value="">无上级部门</option>{orgs.filter(org => !excluded.has(org.id)).map(org => <option key={org.id} value={org.id}>{'　'.repeat(Math.min(org.level, 5))}{org.name}</option>)}</NativeSelect></Field>}
          {state.mode === 'edit' && <div className="flex items-center justify-between rounded-lg border border-manus-border bg-manus-tertiary px-3 py-3"><div><div className="text-sm font-medium text-manus-text">启用部门</div><div className="text-xs text-manus-muted">停用后不能新增岗位或授权</div></div><Switch checked={state.status} onCheckedChange={status => onChange({ ...state, status })} /></div>}
        </div>}
        <DialogFooter><Button variant="outline" onClick={() => onChange(null)} disabled={submitting}>取消</Button><Button onClick={onSubmit} disabled={submitting || !state?.name.trim()}>{submitting && <Loader2 className="animate-spin" />}保存</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function PositionEditorDialog({ state, orgs, submitting, onChange, onSubmit }: {
  state: PositionDialogState | null
  orgs: OrgUnit[]
  submitting: boolean
  onChange: (state: PositionDialogState | null) => void
  onSubmit: () => void
}) {
  return (
    <Dialog open={Boolean(state)} onOpenChange={open => !open && onChange(null)}>
      <DialogContent className="border-manus-border bg-manus-secondary">
        <DialogHeader><DialogTitle className="text-manus-text">{state?.item ? '编辑岗位' : '新增岗位'}</DialogTitle><DialogDescription>岗位名称在所属部门内唯一。</DialogDescription></DialogHeader>
        {state && <div className="space-y-4"><Field label="岗位名称"><Input autoFocus value={state.name} onChange={event => onChange({ ...state, name: event.target.value })} placeholder="请输入岗位名称" /></Field><Field label="所属部门"><NativeSelect value={String(state.orgUnitId)} onChange={value => onChange({ ...state, orgUnitId: Number(value) })} ariaLabel="所属部门">{orgs.filter(org => org.status).map(org => <option key={org.id} value={org.id}>{'　'.repeat(Math.min(org.level, 5))}{org.name}</option>)}</NativeSelect></Field></div>}
        <DialogFooter><Button variant="outline" onClick={() => onChange(null)} disabled={submitting}>取消</Button><Button onClick={onSubmit} disabled={submitting || !state?.name.trim()}>{submitting && <Loader2 className="animate-spin" />}保存</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AssignmentEditorDialog({ state, accounts, positions, submitting, onChange, onSubmit }: {
  state: AssignmentDialogState | null
  accounts: Account[]
  positions: PositionSummary[]
  submitting: boolean
  onChange: (state: AssignmentDialogState | null) => void
  onSubmit: () => void
}) {
  const isNewAccount = state?.tab === 'new' && state.mode === 'add'
  const isEdit = state?.mode === 'edit'
  return (
    <Dialog open={Boolean(state)} onOpenChange={open => !open && onChange(null)}>
      <DialogContent className="max-h-[90vh] overflow-y-auto border-manus-border bg-manus-secondary">
        <DialogHeader><DialogTitle className="text-manus-text">{isEdit ? '编辑任职' : state?.mode === 'assign-unassigned' ? '安排岗位' : '添加账号'}</DialogTitle><DialogDescription>{isEdit ? '调整主任职、任职时间和状态。' : '同一账号可以在多个岗位任职。'}</DialogDescription></DialogHeader>
        {state && <div className="space-y-4">
          {state.mode === 'add' && <div className="grid grid-cols-2 rounded-lg bg-manus-tertiary p-1"><button className={cn('rounded-md px-3 py-2 text-sm', state.tab === 'existing' ? 'bg-manus-secondary text-manus-text shadow' : 'text-manus-muted')} onClick={() => onChange({ ...state, tab: 'existing' })}>选择已有账号</button><button className={cn('rounded-md px-3 py-2 text-sm', state.tab === 'new' ? 'bg-manus-secondary text-manus-text shadow' : 'text-manus-muted')} onClick={() => onChange({ ...state, tab: 'new' })}>创建新账号</button></div>}
          {state.mode === 'assign-unassigned' && state.fixedAccount && <div className="rounded-lg border border-manus-border bg-manus-tertiary px-3 py-3 text-sm text-manus-text">正在为 <b>{state.fixedAccount.username}</b> 安排岗位</div>}
          {state.mode === 'assign-unassigned' && <Field label="岗位"><NativeSelect value={String(state.positionId)} onChange={value => onChange({ ...state, positionId: Number(value) })} ariaLabel="岗位">{positions.filter(item => item.status).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</NativeSelect></Field>}
          {state.mode === 'add' && state.tab === 'existing' && <Field label="已有账号"><NativeSelect value={state.userId} onChange={userId => onChange({ ...state, userId })} ariaLabel="已有账号"><option value="">请选择账号</option>{accounts.filter(account => !account.disabled).map(account => <option key={account.id} value={account.id}>{account.username}{account.email ? ` · ${account.email}` : ''}</option>)}</NativeSelect></Field>}
          {isNewAccount && <><Field label="登录名"><Input value={state.username} onChange={event => onChange({ ...state, username: event.target.value })} placeholder="请输入登录名" /></Field><Field label="邮箱（可选）"><Input type="email" value={state.email} onChange={event => onChange({ ...state, email: event.target.value })} placeholder="name@example.com" /></Field><Field label="初始密码"><Input type="password" value={state.password} onChange={event => onChange({ ...state, password: event.target.value })} placeholder="至少 8 位" /></Field></>}
          {!isNewAccount && <div className="flex items-center justify-between rounded-lg border border-manus-border px-3 py-3"><div><div className="text-sm font-medium text-manus-text">设为主任职</div><div className="text-xs text-manus-muted">同一时间只能有一个主任职</div></div><Switch checked={state.isPrimary} onCheckedChange={isPrimary => onChange({ ...state, isPrimary })} /></div>}
          <div className="grid gap-4 sm:grid-cols-2"><Field label="开始时间"><Input type="datetime-local" value={state.startsAt} onChange={event => onChange({ ...state, startsAt: event.target.value })} /></Field><Field label="结束时间（可选）"><Input type="datetime-local" value={state.endsAt} onChange={event => onChange({ ...state, endsAt: event.target.value })} /></Field></div>
          {isEdit && <div className="flex items-center justify-between rounded-lg border border-manus-border px-3 py-3"><div className="text-sm font-medium text-manus-text">启用任职</div><Switch checked={state.status} onCheckedChange={status => onChange({ ...state, status })} /></div>}
        </div>}
        <DialogFooter><Button variant="outline" onClick={() => onChange(null)} disabled={submitting}>取消</Button><Button onClick={onSubmit} disabled={submitting || !state || !state.positionId || !state.startsAt || (state.tab === 'existing' && !state.fixedAccount && !state.assignment && !state.userId) || (isNewAccount && (!state.username.trim() || state.password.length < 8))}>{submitting && <Loader2 className="animate-spin" />}保存</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AccountEditorDialog({ state, submitting, onChange, onSubmit }: {
  state: AccountDialogState | null
  submitting: boolean
  onChange: (state: AccountDialogState | null) => void
  onSubmit: () => void
}) {
  const passwordMode = state?.mode === 'password'
  return (
    <Dialog open={Boolean(state)} onOpenChange={open => !open && onChange(null)}>
      <DialogContent className="border-manus-border bg-manus-secondary">
        <DialogHeader><DialogTitle className="text-manus-text">{passwordMode ? '重置密码' : '编辑账号'}</DialogTitle><DialogDescription>{passwordMode ? `为 ${state?.account.username || ''} 设置新密码。` : '修改登录名和邮箱。'}</DialogDescription></DialogHeader>
        {state && <div className="space-y-4">{passwordMode ? <Field label="新密码"><Input autoFocus type="password" value={state.password} onChange={event => onChange({ ...state, password: event.target.value })} placeholder="至少 8 位" /></Field> : <><Field label="登录名"><Input value={state.username} onChange={event => onChange({ ...state, username: event.target.value })} /></Field><Field label="邮箱"><Input type="email" value={state.email} onChange={event => onChange({ ...state, email: event.target.value })} /></Field></>}</div>}
        <DialogFooter><Button variant="outline" onClick={() => onChange(null)} disabled={submitting}>取消</Button><Button onClick={onSubmit} disabled={submitting || !state || (passwordMode ? state.password.length < 8 : !state.username.trim())}>{submitting && <Loader2 className="animate-spin" />}保存</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DeleteDialog({ state, submitting, onChange, onConfirm }: {
  state: DeleteState | null
  submitting: boolean
  onChange: (state: DeleteState | null) => void
  onConfirm: () => void
}) {
  const labels: Record<keyof DeletionBlockers, string> = { child_org_units: '子部门', positions: '岗位', assignments: '任职记录', role_bindings: '角色授权' }
  const blockerEntries = Object.entries(state?.blockers || {}).filter(([, count]) => Number(count) > 0) as Array<[keyof DeletionBlockers, number]>
  const isDisable = state?.kind === 'account'
  return (
    <Dialog open={Boolean(state)} onOpenChange={open => !open && onChange(null)}>
      <DialogContent className="border-manus-border bg-manus-secondary">
        <DialogHeader><DialogTitle className="text-manus-text">{blockerEntries.length ? '暂时无法删除' : isDisable ? `停用账号“${state?.name || ''}”` : `删除“${state?.name || ''}”`}</DialogTitle><DialogDescription>{blockerEntries.length ? '请先移动或清理以下关联内容，再重新删除。' : isDisable ? '停用账号会同时停用其全部有效任职，但不会删除历史记录。' : state?.kind === 'assignment' ? '只会移除当前岗位任职，不会停用账号或影响其他岗位。' : '此操作不可撤销，且不会级联删除关联内容。'}</DialogDescription></DialogHeader>
        {blockerEntries.length > 0 && <div className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">{blockerEntries.map(([key, count]) => <div key={key} className="flex items-center justify-between text-sm"><span className="text-amber-200">{labels[key]}</span><b className="text-amber-100">{count} 项</b></div>)}</div>}
        <DialogFooter><Button variant="outline" onClick={() => onChange(null)} disabled={submitting}>{blockerEntries.length ? '知道了' : '取消'}</Button>{blockerEntries.length === 0 && <Button variant="destructive" onClick={onConfirm} disabled={submitting}>{submitting && <Loader2 className="animate-spin" />}{isDisable ? '确认停用' : '确认删除'}</Button>}</DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function IconAction({ label, icon, onClick, danger = false }: { label: string; icon: ReactNode; onClick: () => void; danger?: boolean }) {
  return <Tooltip><TooltipTrigger asChild><button type="button" aria-label={label} className={cn('flex h-7 w-7 items-center justify-center rounded-md text-manus-muted transition-colors hover:bg-manus-tertiary hover:text-manus-text [&>svg]:h-3.5 [&>svg]:w-3.5', danger && 'hover:text-red-400')} onClick={event => { event.stopPropagation(); onClick() }}>{icon}</button></TooltipTrigger><TooltipContent>{label}</TooltipContent></Tooltip>
}

function NativeSelect({ value, onChange, children, ariaLabel }: { value: string; onChange: (value: string) => void; children: ReactNode; ariaLabel: string }) {
  return <select aria-label={ariaLabel} value={value} onChange={event => onChange(event.target.value)} className="h-9 min-w-36 rounded-md border border-manus-border bg-manus-tertiary px-3 text-sm text-manus-text outline-none focus:border-accent focus:ring-1 focus:ring-accent">{children}</select>
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block space-y-2"><span className="text-sm font-medium text-manus-text">{label}</span>{children}</label>
}

function StatusPill({ children, tone }: { children: ReactNode; tone: 'blue' | 'green' | 'amber' | 'red' | 'neutral' }) {
  const colors = { blue: 'bg-blue-500/15 text-blue-300', green: 'bg-emerald-500/15 text-emerald-300', amber: 'bg-amber-500/15 text-amber-300', red: 'bg-red-500/15 text-red-300', neutral: 'bg-manus-tertiary text-manus-muted' }
  return <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', colors[tone])}>{children}</span>
}

function CenteredState({ icon, title, description, action, compact = false }: { icon: ReactNode; title: string; description?: string; action?: ReactNode; compact?: boolean }) {
  return <div className={cn('flex flex-col items-center justify-center px-6 text-center', compact ? 'py-10' : 'min-h-[420px] py-16')}><div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-manus-border bg-manus-tertiary text-manus-muted [&>svg]:h-5 [&>svg]:w-5">{icon}</div><div className="font-medium text-manus-text">{title}</div>{description && <p className="mt-1 max-w-sm text-sm text-manus-muted">{description}</p>}{action && <div className="mt-4">{action}</div>}</div>
}
