import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import {
  CircleAlert,
  Link2,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  Users,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'
import { cn } from '@/lib/utils'
import { apiErrorMessage, authorizationRequest } from '@/features/authorization/api'
import {
  formatDateTime,
  toLocalInput,
  type AuthorizationRole,
  type Capability,
  type OrgUnit,
  type PositionSummary,
  type RoleBinding,
  type ScopeType,
} from '@/features/authorization/types'

type PageTab = 'roles' | 'bindings'

type RoleDialogState = {
  item?: AuthorizationRole
  name: string
  description: string
  permissionCodes: string[]
}

type BindingDialogState = {
  item?: RoleBinding
  positionId: number
  roleId: number
  scopeType: ScopeType
  scopeOrgUnitId: number | null
  customOrgUnitIds: number[]
  startsAt: string
  endsAt: string
  status: boolean
}

type DeleteState = { kind: 'role' | 'binding'; id: number; name: string }

const scopeLabels: Record<ScopeType, string> = {
  self: '岗位所在部门',
  org_unit: '指定部门',
  org_tree: '指定部门及下级',
  custom: '自定义部门集合',
  workspace: '整个工作区',
}

export default function AuthorizationRolesPage() {
  const user = useAuthStore(state => state.user)
  const permissions = useMemo(() => user?.permissions || [], [user?.permissions])
  const hasCapability = useCallback(
    (code: string) => permissions.includes('*') || permissions.includes(code),
    [permissions],
  )
  const canViewRoles = hasCapability('role:view')
  const canManageRoles = hasCapability('role:manage')
  const canViewBindings = hasCapability('authorization:view')
  const canManageBindings = hasCapability('authorization:manage')

  const [tab, setTab] = useState<PageTab>(canViewRoles ? 'roles' : 'bindings')
  const [roles, setRoles] = useState<AuthorizationRole[]>([])
  const [bindings, setBindings] = useState<RoleBinding[]>([])
  const [capabilities, setCapabilities] = useState<Capability[]>([])
  const [positions, setPositions] = useState<PositionSummary[]>([])
  const [orgs, setOrgs] = useState<OrgUnit[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [roleDialog, setRoleDialog] = useState<RoleDialogState | null>(null)
  const [bindingDialog, setBindingDialog] = useState<BindingDialogState | null>(null)
  const [deleteState, setDeleteState] = useState<DeleteState | null>(null)

  const orgById = useMemo(() => new Map(orgs.map(item => [item.id, item])), [orgs])
  const capabilitiesByModule = useMemo(() => {
    const groups = new Map<string, Capability[]>()
    capabilities.forEach(item => groups.set(item.module, [...(groups.get(item.module) || []), item]))
    return [...groups.entries()]
  }, [capabilities])

  const loadData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [roleData, bindingData, capabilityData, positionData, orgData] = await Promise.all([
        canViewRoles ? authorizationRequest<AuthorizationRole[]>('/authorization/roles') : Promise.resolve([]),
        canViewBindings ? authorizationRequest<RoleBinding[]>('/authorization/role-bindings') : Promise.resolve([]),
        canViewRoles ? authorizationRequest<Capability[]>('/authorization/capabilities') : Promise.resolve([]),
        canViewBindings ? authorizationRequest<PositionSummary[]>('/authorization/positions') : Promise.resolve([]),
        canViewBindings ? authorizationRequest<OrgUnit[]>('/authorization/org-units') : Promise.resolve([]),
      ])
      setRoles(roleData)
      setBindings(bindingData)
      setCapabilities(capabilityData)
      setPositions(positionData)
      setOrgs(orgData)
    } catch (exc) {
      setError(apiErrorMessage(exc, '角色数据加载失败'))
    } finally {
      setLoading(false)
    }
  }, [canViewBindings, canViewRoles])

  useEffect(() => { void loadData() }, [loadData])
  useEffect(() => {
    if (tab === 'roles' && !canViewRoles) setTab('bindings')
    if (tab === 'bindings' && !canViewBindings) setTab('roles')
  }, [canViewBindings, canViewRoles, tab])

  if (!canViewRoles && !canViewBindings) return <Navigate to="/unauthorized" replace />

  const submitRole = async () => {
    if (!roleDialog?.name.trim()) return
    setSubmitting(true)
    setError('')
    try {
      const item = roleDialog.item
      await authorizationRequest(item ? `/authorization/roles/${item.id}` : '/authorization/roles', {
        method: item ? 'PATCH' : 'POST',
        body: JSON.stringify({
          name: roleDialog.name.trim(),
          description: roleDialog.description.trim() || null,
          permission_codes: roleDialog.permissionCodes,
        }),
      })
      setRoleDialog(null)
      await loadData()
    } catch (exc) {
      setError(apiErrorMessage(exc, '角色保存失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const submitBinding = async () => {
    if (!bindingDialog?.positionId || !bindingDialog.roleId) return
    setSubmitting(true)
    setError('')
    try {
      const item = bindingDialog.item
      await authorizationRequest(item ? `/authorization/role-bindings/${item.id}` : '/authorization/role-bindings', {
        method: item ? 'PATCH' : 'POST',
        body: JSON.stringify({
          position_id: bindingDialog.positionId,
          role_id: bindingDialog.roleId,
          scope_type: bindingDialog.scopeType,
          scope_org_unit_id: bindingDialog.scopeOrgUnitId,
          custom_org_unit_ids: bindingDialog.customOrgUnitIds,
          starts_at: new Date(bindingDialog.startsAt).toISOString(),
          ends_at: bindingDialog.endsAt ? new Date(bindingDialog.endsAt).toISOString() : null,
          status: bindingDialog.status,
        }),
      })
      setBindingDialog(null)
      await loadData()
    } catch (exc) {
      setError(apiErrorMessage(exc, '角色授权保存失败'))
    } finally {
      setSubmitting(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleteState) return
    setSubmitting(true)
    setError('')
    try {
      await authorizationRequest(
        deleteState.kind === 'role' ? `/authorization/roles/${deleteState.id}` : `/authorization/role-bindings/${deleteState.id}`,
        { method: 'DELETE' },
      )
      setDeleteState(null)
      await loadData()
    } catch (exc) {
      setError(apiErrorMessage(exc, deleteState.kind === 'role' ? '角色仍在使用，无法删除' : '撤销授权失败'))
      setDeleteState(null)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <TooltipProvider delayDuration={250}>
      <div className="flex-1 min-h-0 overflow-auto bg-manus p-4 md:p-6">
        <div className="mx-auto max-w-[1400px] space-y-5">
          <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-manus-subtle">系统管理</div>
              <h1 className="text-2xl font-semibold tracking-tight text-manus-text">角色管理</h1>
              <p className="mt-1 text-sm text-manus-muted">维护角色能力，并将角色授权给组织中的岗位。</p>
            </div>
            <Button variant="outline" onClick={() => void loadData()} disabled={loading} className="border-manus-border bg-manus-secondary">
              <RefreshCw className={cn(loading && 'animate-spin')} />刷新
            </Button>
          </header>

          {error && <div className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"><CircleAlert className="mt-0.5 h-4 w-4 shrink-0" /><span>{error}</span><button className="ml-auto text-red-200 hover:text-white" onClick={() => setError('')}>关闭</button></div>}

          <Tabs value={tab} onValueChange={value => setTab(value as PageTab)}>
            <TabsList className="h-11 border border-manus-border bg-manus-secondary p-1">
              {canViewRoles && <TabsTrigger value="roles" className="gap-2 px-4 data-[state=active]:bg-manus-tertiary"><Shield />角色</TabsTrigger>}
              {canViewBindings && <TabsTrigger value="bindings" className="gap-2 px-4 data-[state=active]:bg-manus-tertiary"><Link2 />角色授权</TabsTrigger>}
            </TabsList>
          </Tabs>

          <Card className="overflow-hidden border-manus-border bg-manus-secondary">
            {loading ? <CenteredState icon={<Loader2 className="animate-spin" />} title="正在加载角色数据" /> : tab === 'roles' ? (
              <RolesPanel
                roles={roles}
                canManage={canManageRoles}
                onCreate={() => setRoleDialog({ name: '', description: '', permissionCodes: [] })}
                onEdit={role => setRoleDialog({ item: role, name: role.name, description: role.description || '', permissionCodes: [...role.permission_codes] })}
                onDelete={role => setDeleteState({ kind: 'role', id: role.id, name: role.name })}
              />
            ) : (
              <BindingsPanel
                bindings={bindings}
                orgById={orgById}
                canManage={canManageBindings}
                onCreate={() => setBindingDialog({
                  positionId: positions[0]?.id || 0,
                  roleId: roles[0]?.id || 0,
                  scopeType: 'self',
                  scopeOrgUnitId: null,
                  customOrgUnitIds: [],
                  startsAt: toLocalInput(),
                  endsAt: '',
                  status: true,
                })}
                onEdit={binding => setBindingDialog({
                  item: binding,
                  positionId: binding.position_id,
                  roleId: binding.role_id,
                  scopeType: binding.scope_type,
                  scopeOrgUnitId: binding.scope_org_unit_id,
                  customOrgUnitIds: [...binding.custom_org_unit_ids],
                  startsAt: toLocalInput(binding.starts_at),
                  endsAt: binding.ends_at ? toLocalInput(binding.ends_at) : '',
                  status: binding.status,
                })}
                onDelete={binding => setDeleteState({ kind: 'binding', id: binding.id, name: `${binding.position_name} → ${binding.role_name}` })}
              />
            )}
          </Card>
        </div>
      </div>

      <RoleEditorDialog state={roleDialog} capabilityGroups={capabilitiesByModule} submitting={submitting} onChange={setRoleDialog} onSubmit={() => void submitRole()} />
      <BindingEditorDialog state={bindingDialog} positions={positions} roles={roles} orgs={orgs} submitting={submitting} onChange={setBindingDialog} onSubmit={() => void submitBinding()} />
      <DeleteDialog state={deleteState} submitting={submitting} onChange={setDeleteState} onConfirm={() => void confirmDelete()} />
    </TooltipProvider>
  )
}

function RolesPanel({ roles, canManage, onCreate, onEdit, onDelete }: {
  roles: AuthorizationRole[]
  canManage: boolean
  onCreate: () => void
  onEdit: (role: AuthorizationRole) => void
  onDelete: (role: AuthorizationRole) => void
}) {
  return <div><PanelHeader title="角色" description={`${roles.length} 个角色模板`} action={canManage ? <Button onClick={onCreate}><Plus />新增角色</Button> : undefined} />{roles.length === 0 ? <CenteredState icon={<Shield />} title="暂无角色" /> : <div className="divide-y divide-manus-border">{roles.map(role => <div key={role.id} className="flex items-start gap-4 px-5 py-4"><div className="flex h-9 w-9 items-center justify-center rounded-lg border border-manus-border bg-manus-tertiary text-manus-muted"><Shield className="h-4 w-4" /></div><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="font-medium text-manus-text">{role.name}</span>{role.is_system && <span className="rounded-full bg-blue-500/15 px-2 py-0.5 text-[11px] text-blue-300">系统角色</span>}</div><div className="mt-1 text-sm text-manus-muted">{role.description || '暂无说明'}</div><div className="mt-2 flex flex-wrap gap-1.5">{role.permission_codes.slice(0, 8).map(code => <span key={code} className="rounded bg-manus-tertiary px-2 py-1 text-[11px] text-manus-muted">{code}</span>)}{role.permission_codes.length > 8 && <span className="px-1 py-1 text-[11px] text-manus-subtle">+{role.permission_codes.length - 8}</span>}</div></div>{canManage && !role.is_system && <div className="flex"><IconAction label="编辑角色" icon={<Pencil />} onClick={() => onEdit(role)} /><IconAction label="删除角色" danger icon={<Trash2 />} onClick={() => onDelete(role)} /></div>}</div>)}</div>}</div>
}

function BindingsPanel({ bindings, orgById, canManage, onCreate, onEdit, onDelete }: {
  bindings: RoleBinding[]
  orgById: Map<number, OrgUnit>
  canManage: boolean
  onCreate: () => void
  onEdit: (binding: RoleBinding) => void
  onDelete: (binding: RoleBinding) => void
}) {
  return <div><PanelHeader title="角色授权" description={`${bindings.length} 条岗位授权`} action={canManage ? <Button onClick={onCreate}><Plus />新增授权</Button> : undefined} />{bindings.length === 0 ? <CenteredState icon={<Link2 />} title="暂无角色授权" description="把角色授权给岗位后，岗位下的账号将继承相应能力。" /> : <div className="divide-y divide-manus-border">{bindings.map(binding => <div key={binding.id} className="flex items-start gap-4 px-5 py-4"><div className="flex h-9 w-9 items-center justify-center rounded-lg border border-manus-border bg-manus-tertiary text-manus-muted"><Link2 className="h-4 w-4" /></div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="font-medium text-manus-text">{binding.position_name}</span><span className="text-manus-subtle">→</span><span className="font-medium text-accent-muted">{binding.role_name}</span>{!binding.status && <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] text-red-300">已停用</span>}</div><div className="mt-1 text-sm text-manus-muted">{orgById.get(binding.org_unit_id)?.name || '未知部门'} · {scopeLabels[binding.scope_type]} · {formatDateTime(binding.starts_at)} 至 {formatDateTime(binding.ends_at)}</div><div className="mt-2 flex items-center gap-1.5 text-xs text-manus-muted"><Users className="h-3.5 w-3.5" />影响 {binding.affected_user_count || 0} 个账号</div></div>{canManage && <div className="flex"><IconAction label="编辑授权" icon={<Pencil />} onClick={() => onEdit(binding)} /><IconAction label="撤销授权" danger icon={<Trash2 />} onClick={() => onDelete(binding)} /></div>}</div>)}</div>}</div>
}

function RoleEditorDialog({ state, capabilityGroups, submitting, onChange, onSubmit }: {
  state: RoleDialogState | null
  capabilityGroups: Array<[string, Capability[]]>
  submitting: boolean
  onChange: (state: RoleDialogState | null) => void
  onSubmit: () => void
}) {
  return <Dialog open={Boolean(state)} onOpenChange={open => !open && onChange(null)}><DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto border-manus-border bg-manus-secondary"><DialogHeader><DialogTitle className="text-manus-text">{state?.item ? '编辑角色' : '新增角色'}</DialogTitle><DialogDescription>选择该角色提供的功能能力；数据范围由岗位授权决定。</DialogDescription></DialogHeader>{state && <div className="space-y-4"><Field label="角色名称"><Input value={state.name} onChange={event => onChange({ ...state, name: event.target.value })} placeholder="请输入角色名称" /></Field><Field label="角色说明"><Input value={state.description} onChange={event => onChange({ ...state, description: event.target.value })} placeholder="说明该角色的使用场景" /></Field><div className="space-y-3"><div className="text-sm font-medium text-manus-text">功能能力</div>{capabilityGroups.map(([module, items]) => <div key={module} className="rounded-lg border border-manus-border"><div className="border-b border-manus-border bg-manus-tertiary px-3 py-2 text-xs font-medium uppercase tracking-wide text-manus-muted">{module}</div><div className="grid gap-2 p-3 sm:grid-cols-2">{items.map(item => <label key={item.code} className="flex cursor-pointer items-start gap-2 rounded-md p-2 hover:bg-manus-hover"><input type="checkbox" className="mt-1" checked={state.permissionCodes.includes(item.code)} onChange={event => onChange({ ...state, permissionCodes: event.target.checked ? [...state.permissionCodes, item.code] : state.permissionCodes.filter(code => code !== item.code) })} /><span><span className="block text-sm text-manus-text">{item.description}</span><span className="text-[11px] text-manus-subtle">{item.code}</span></span></label>)}</div></div>)}</div></div>}<DialogFooter><Button variant="outline" onClick={() => onChange(null)} disabled={submitting}>取消</Button><Button onClick={onSubmit} disabled={submitting || !state?.name.trim()}>{submitting && <Loader2 className="animate-spin" />}保存</Button></DialogFooter></DialogContent></Dialog>
}

function BindingEditorDialog({ state, positions, roles, orgs, submitting, onChange, onSubmit }: {
  state: BindingDialogState | null
  positions: PositionSummary[]
  roles: AuthorizationRole[]
  orgs: OrgUnit[]
  submitting: boolean
  onChange: (state: BindingDialogState | null) => void
  onSubmit: () => void
}) {
  return <Dialog open={Boolean(state)} onOpenChange={open => !open && onChange(null)}><DialogContent className="max-h-[90vh] overflow-y-auto border-manus-border bg-manus-secondary"><DialogHeader><DialogTitle className="text-manus-text">{state?.item ? '编辑角色授权' : '新增角色授权'}</DialogTitle><DialogDescription>账号通过岗位继承角色，授权范围决定能力可作用的组织边界。</DialogDescription></DialogHeader>{state && <div className="space-y-4"><Field label="岗位"><NativeSelect value={String(state.positionId)} onChange={value => onChange({ ...state, positionId: Number(value) })}>{positions.filter(item => item.status).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</NativeSelect></Field><Field label="角色"><NativeSelect value={String(state.roleId)} onChange={value => onChange({ ...state, roleId: Number(value) })}>{roles.map(role => <option key={role.id} value={role.id}>{role.name}</option>)}</NativeSelect></Field><Field label="授权范围"><NativeSelect value={state.scopeType} onChange={value => onChange({ ...state, scopeType: value as ScopeType, scopeOrgUnitId: null, customOrgUnitIds: [] })}>{Object.entries(scopeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</NativeSelect></Field>{['org_unit', 'org_tree'].includes(state.scopeType) && <Field label="授权部门"><NativeSelect value={state.scopeOrgUnitId ? String(state.scopeOrgUnitId) : ''} onChange={value => onChange({ ...state, scopeOrgUnitId: value ? Number(value) : null })}><option value="">请选择部门</option>{orgs.map(org => <option key={org.id} value={org.id}>{'　'.repeat(Math.min(org.level, 5))}{org.name}</option>)}</NativeSelect></Field>}{state.scopeType === 'custom' && <div className="space-y-2"><div className="text-sm font-medium text-manus-text">选择部门</div><div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-manus-border p-2">{orgs.map(org => <label key={org.id} className="flex items-center gap-2 rounded px-2 py-1.5 text-sm text-manus-text hover:bg-manus-hover" style={{ paddingLeft: `${org.level * 14 + 8}px` }}><input type="checkbox" checked={state.customOrgUnitIds.includes(org.id)} onChange={event => onChange({ ...state, customOrgUnitIds: event.target.checked ? [...state.customOrgUnitIds, org.id] : state.customOrgUnitIds.filter(id => id !== org.id) })} />{org.name}</label>)}</div></div>}<div className="grid gap-4 sm:grid-cols-2"><Field label="开始时间"><Input type="datetime-local" value={state.startsAt} onChange={event => onChange({ ...state, startsAt: event.target.value })} /></Field><Field label="结束时间（可选）"><Input type="datetime-local" value={state.endsAt} onChange={event => onChange({ ...state, endsAt: event.target.value })} /></Field></div>{state.item && <div className="flex items-center justify-between rounded-lg border border-manus-border px-3 py-3"><div className="text-sm font-medium text-manus-text">启用授权</div><Switch checked={state.status} onCheckedChange={status => onChange({ ...state, status })} /></div>}</div>}<DialogFooter><Button variant="outline" onClick={() => onChange(null)} disabled={submitting}>取消</Button><Button onClick={onSubmit} disabled={submitting || !state?.positionId || !state.roleId || (state.scopeType === 'custom' && state.customOrgUnitIds.length === 0) || (['org_unit', 'org_tree'].includes(state?.scopeType || '') && !state?.scopeOrgUnitId)}>{submitting && <Loader2 className="animate-spin" />}保存</Button></DialogFooter></DialogContent></Dialog>
}

function DeleteDialog({ state, submitting, onChange, onConfirm }: { state: DeleteState | null; submitting: boolean; onChange: (state: DeleteState | null) => void; onConfirm: () => void }) {
  return <Dialog open={Boolean(state)} onOpenChange={open => !open && onChange(null)}><DialogContent className="border-manus-border bg-manus-secondary"><DialogHeader><DialogTitle className="text-manus-text">{state?.kind === 'role' ? '删除角色' : '撤销角色授权'}</DialogTitle><DialogDescription>确认{state?.kind === 'role' ? '删除' : '撤销'}“{state?.name || ''}”？正在使用的角色会被服务端保护。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => onChange(null)} disabled={submitting}>取消</Button><Button variant="destructive" onClick={onConfirm} disabled={submitting}>{submitting && <Loader2 className="animate-spin" />}确认</Button></DialogFooter></DialogContent></Dialog>
}

function PanelHeader({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="flex items-center justify-between border-b border-manus-border px-5 py-5"><div><h2 className="text-lg font-semibold text-manus-text">{title}</h2><p className="mt-1 text-sm text-manus-muted">{description}</p></div>{action}</div>
}

function IconAction({ label, icon, onClick, danger = false }: { label: string; icon: ReactNode; onClick: () => void; danger?: boolean }) {
  return <Tooltip><TooltipTrigger asChild><button type="button" aria-label={label} className={cn('flex h-8 w-8 items-center justify-center rounded-md text-manus-muted hover:bg-manus-hover hover:text-manus-text [&>svg]:h-4 [&>svg]:w-4', danger && 'hover:text-red-400')} onClick={onClick}>{icon}</button></TooltipTrigger><TooltipContent>{label}</TooltipContent></Tooltip>
}

function NativeSelect({ value, onChange, children }: { value: string; onChange: (value: string) => void; children: ReactNode }) {
  return <select value={value} onChange={event => onChange(event.target.value)} className="h-10 w-full rounded-md border border-manus-border bg-manus-tertiary px-3 text-sm text-manus-text outline-none focus:border-accent focus:ring-1 focus:ring-accent">{children}</select>
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block space-y-2"><span className="text-sm font-medium text-manus-text">{label}</span>{children}</label>
}

function CenteredState({ icon, title, description }: { icon: ReactNode; title: string; description?: string }) {
  return <div className="flex min-h-[430px] flex-col items-center justify-center px-6 text-center"><div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-manus-border bg-manus-tertiary text-manus-muted [&>svg]:h-5 [&>svg]:w-5">{icon}</div><div className="font-medium text-manus-text">{title}</div>{description && <p className="mt-1 max-w-sm text-sm text-manus-muted">{description}</p>}</div>
}
