import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate, useSearchParams } from 'react-router-dom'
import { Building2, BriefcaseBusiness, Users, Shield, Link2, UserRoundCog, History, RefreshCw, Plus, Trash2, Pencil } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useAuthStore, fetchWithAuth } from '@/stores/authStore'
import { cn } from '@/lib/utils'

const API = import.meta.env.VITE_API_BASE_URL || '/api'

type Tab = 'org' | 'position' | 'assignment' | 'role' | 'binding' | 'exception' | 'audit'
type Entity = Record<string, any>

const tabs: Array<{ id: Tab; label: string; icon: typeof Building2 }> = [
  { id: 'org', label: '组织', icon: Building2 },
  { id: 'position', label: '岗位', icon: BriefcaseBusiness },
  { id: 'assignment', label: '人员任职', icon: Users },
  { id: 'role', label: '角色', icon: Shield },
  { id: 'binding', label: '角色授权', icon: Link2 },
  { id: 'exception', label: '用户例外', icon: UserRoundCog },
  { id: 'audit', label: '有效权限与审计', icon: History },
]

const tabCapability: Record<Tab, { view: string; manage?: string }> = {
  org: { view: 'org:view', manage: 'org:manage' },
  position: { view: 'org:view', manage: 'org:manage' },
  assignment: { view: 'user:view', manage: 'user:manage' },
  role: { view: 'role:view', manage: 'role:manage' },
  binding: { view: 'authorization:view', manage: 'authorization:manage' },
  exception: { view: 'authorization:view', manage: 'authorization:delegate' },
  audit: { view: 'authorization:view' },
}

const nowLocal = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
const afterDays = (days: number) => new Date(Date.now() + days * 86400_000 - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16)

async function request(path: string, init?: RequestInit) {
  const response = await fetchWithAuth(`${API}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload?.detail?.message || payload?.detail || `请求失败 (${response.status})`)
  }
  return response.status === 204 ? null : response.json()
}

export default function AuthorizationCenterPage() {
  const [searchParams] = useSearchParams()
  const legacySource = searchParams.get('from')
  const user = useAuthStore(state => state.user)
  const permissions = user?.permissions || []
  const hasCapability = useCallback((code: string) => permissions.includes('*') || permissions.includes(code), [permissions])
  const visibleTabs = useMemo(() => tabs.filter(item => hasCapability(tabCapability[item.id].view)), [hasCapability])
  const canView = visibleTabs.length > 0
  const [tab, setTab] = useState<Tab>('org')
  const [data, setData] = useState<Record<string, Entity[]>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedOrg, setSelectedOrg] = useState<number | null>(null)
  const [selectedUser, setSelectedUser] = useState('')
  const [effective, setEffective] = useState<Entity | null>(null)
  const [form, setForm] = useState<Record<string, any>>({ starts_at: nowLocal(), ends_at: afterDays(30) })

  const loadAll = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [orgs, positions, users, assignments, roles, bindings, exceptions, capabilities, audits] = await Promise.all([
        hasCapability('org:view') ? request('/authorization/org-units') : [],
        hasCapability('org:view') ? request('/authorization/positions') : [],
        hasCapability('user:view') ? request('/authorization/users') : [],
        hasCapability('user:view') ? request('/authorization/assignments') : [],
        hasCapability('role:view') ? request('/authorization/roles') : [],
        hasCapability('authorization:view') ? request('/authorization/role-bindings') : [],
        hasCapability('authorization:view') ? request('/authorization/exceptions') : [],
        hasCapability('authorization:view') || hasCapability('role:view') ? request('/authorization/capabilities') : [],
        hasCapability('authorization:view') ? request('/authorization/audit-events?limit=100') : [],
      ])
      setData({ orgs, positions, users, assignments, roles, bindings, exceptions, capabilities, audits })
      setSelectedOrg(current => current ?? orgs[0]?.id ?? null)
      setSelectedUser(current => current || users[0]?.id || '')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '加载失败')
    } finally { setLoading(false) }
  }, [hasCapability])

  useEffect(() => { if (canView) void loadAll() }, [canView, loadAll])
  useEffect(() => {
    if (!visibleTabs.some(item => item.id === tab) && visibleTabs[0]) setTab(visibleTabs[0].id)
  }, [tab, visibleTabs])

  const orgById = useMemo(() => new Map((data.orgs || []).map(item => [item.id, item])), [data.orgs])
  const userById = useMemo(() => new Map((data.users || []).map(item => [item.id, item])), [data.users])
  const canManageCurrent = Boolean(tabCapability[tab].manage && hasCapability(tabCapability[tab].manage!))
  if (!canView) return <Navigate to="/unauthorized" replace />

  const submit = async () => {
    setError('')
    try {
      if (tab === 'org') {
        const payload = { name: form.name, code: form.code || null, parent_id: form.parent_id ? Number(form.parent_id) : null, leader_id: form.leader_id || null, order_num: Number(form.order_num || 0), status: form.status !== false }
        await request(form._editing_id ? `/authorization/org-units/${form._editing_id}` : '/authorization/org-units', { method: form._editing_id ? 'PATCH' : 'POST', body: JSON.stringify(payload) })
      }
      if (tab === 'position') await request(form._editing_id ? `/authorization/positions/${form._editing_id}` : '/authorization/positions', { method: form._editing_id ? 'PATCH' : 'POST', body: JSON.stringify({ name: form.name, code: form.code || null, org_unit_id: Number(form.org_unit_id || selectedOrg), status: form.status !== false }) })
      if (tab === 'assignment' && form.create_user) await request('/authorization/users', { method: 'POST', body: JSON.stringify({ username: form.username, password: form.password, email: form.email || null, position_id: Number(form.position_id), starts_at: new Date(form.starts_at || nowLocal()).toISOString(), ends_at: form.ends_at ? new Date(form.ends_at).toISOString() : null }) })
      if (tab === 'assignment' && !form.create_user) await request(form._editing_id ? `/authorization/assignments/${form._editing_id}` : '/authorization/assignments', { method: form._editing_id ? 'PATCH' : 'POST', body: JSON.stringify({ user_id: form.user_id, position_id: Number(form.position_id), is_primary: Boolean(form.is_primary), starts_at: new Date(form.starts_at || nowLocal()).toISOString(), ends_at: form.ends_at ? new Date(form.ends_at).toISOString() : null, status: form.status !== false }) })
      if (tab === 'role') await request(form._editing_id ? `/authorization/roles/${form._editing_id}` : '/authorization/roles', { method: form._editing_id ? 'PATCH' : 'POST', body: JSON.stringify({ name: form.name, description: form.description || null, permission_codes: form.permission_codes || [] }) })
      if (tab === 'binding') await request(form._editing_id ? `/authorization/role-bindings/${form._editing_id}` : '/authorization/role-bindings', { method: form._editing_id ? 'PATCH' : 'POST', body: JSON.stringify({ position_id: Number(form.position_id), role_id: Number(form.role_id), scope_type: form.scope_type || 'self', scope_org_unit_id: form.scope_org_unit_id ? Number(form.scope_org_unit_id) : null, custom_org_unit_ids: (form.custom_org_unit_ids || []).map(Number), starts_at: new Date(form.starts_at || nowLocal()).toISOString(), ends_at: form.ends_at ? new Date(form.ends_at).toISOString() : null, status: form.status !== false }) })
      if (tab === 'exception') await request('/authorization/exceptions', { method: 'POST', body: JSON.stringify({ user_id: form.user_id, effect_type: form.effect_type || 'allow', capability_codes: form.capability_codes || [], scope_type: form.scope_type || 'workspace', scope_org_unit_ids: (form.scope_org_unit_ids || []).map(Number), reason: form.reason, owner_id: form.owner_id || user?.id, starts_at: new Date(form.starts_at || nowLocal()).toISOString(), ends_at: new Date(form.ends_at || afterDays(30)).toISOString() }) })
      setForm({ starts_at: nowLocal(), ends_at: afterDays(30) })
      await loadAll()
    } catch (exc) { setError(exc instanceof Error ? exc.message : '保存失败') }
  }

  const remove = async (path: string, message: string) => {
    if (!window.confirm(message)) return
    setError('')
    try { await request(path, { method: 'DELETE' }); await loadAll() }
    catch (exc) { setError(exc instanceof Error ? exc.message : '操作失败') }
  }

  const editOrg = (item: Entity) => {
    setSelectedOrg(item.id)
    setForm({ _editing_id: item.id, name: item.name, code: item.code || '', parent_id: item.parent_id || '', leader_id: item.leader_id || '', order_num: item.order_num || 0, status: item.status })
  }

  const loadEffective = async () => {
    if (!selectedUser) return
    try { setEffective(await request(`/authorization/effective-access?user_id=${encodeURIComponent(selectedUser)}`)) }
    catch (exc) { setError(exc instanceof Error ? exc.message : '预览失败') }
  }

  return (
    <div className="flex-1 min-h-0 overflow-auto bg-manus p-6">
      <div className="max-w-[1500px] mx-auto space-y-5">
        <div className="flex items-center justify-between">
          <div><h1 className="text-2xl font-semibold text-manus-text">组织权限中心</h1><p className="text-sm text-manus-muted mt-1">用户通过任职继承岗位的角色授权，所有数据范围均绑定组织边界。</p></div>
          <Button variant="outline" onClick={() => void loadAll()} disabled={loading}><RefreshCw className={cn('h-4 w-4 mr-2', loading && 'animate-spin')} />刷新</Button>
        </div>
        {legacySource && <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-700">原{({ users: '用户管理', roles: '角色管理', departments: '部门管理' } as Record<string, string>)[legacySource] || '权限'}页面已整合到“组织权限中心”；旧接口不再写入数据。</div>}
        {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-500">{error}</div>}
        <div className="flex flex-wrap gap-2 rounded-xl border border-manus-border bg-manus-secondary p-2">
          {visibleTabs.map(item => <Button key={item.id} variant={tab === item.id ? 'default' : 'ghost'} onClick={() => { setTab(item.id); setForm({ starts_at: nowLocal(), ends_at: afterDays(30) }) }}><item.icon className="h-4 w-4 mr-2" />{item.label}</Button>)}
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_380px] gap-5">
          <Card><CardHeader><CardTitle>{tabs.find(item => item.id === tab)?.label}</CardTitle></CardHeader><CardContent className="space-y-2">
            {tab === 'org' && (data.orgs || []).map(item => <div key={item.id} className={cn('flex items-center rounded-lg border', selectedOrg === item.id ? 'border-accent bg-accent/10' : 'border-manus-border')} style={{ marginLeft: `${item.level * 18}px` }}><button onClick={() => editOrg(item)} className="min-w-0 flex-1 p-3 text-left"><div className="font-medium text-manus-text">{item.name}</div><div className="text-xs text-manus-muted">{item.code || '无编码'} · 层级 {item.level} · {item.status ? '启用' : '停用'}</div></button>{canManageCurrent && <DeleteButton onClick={() => void remove(`/authorization/org-units/${item.id}`, `确认删除组织“${item.name}”？`)} />}</div>)}
            {tab === 'position' && (data.positions || []).map(item => <Row key={item.id} title={item.name} meta={`${orgById.get(item.org_unit_id)?.name || '未知组织'} · ${item.headcount ?? (data.assignments || []).filter(a => a.position_id === item.id && a.status).length} 人在岗 · ${item.status ? '启用' : '停用'}`} actions={canManageCurrent && <RowActions onEdit={() => setForm({ _editing_id: item.id, name: item.name, code: item.code || '', org_unit_id: item.org_unit_id, status: item.status })} onDelete={() => void remove(`/authorization/positions/${item.id}`, `确认删除岗位“${item.name}”？`)} />} />)}
            {tab === 'assignment' && <><h3 className="pt-1 text-sm font-medium text-manus-text">账号</h3>{(data.users || []).map(item => <Row key={`user-${item.id}`} title={item.username} meta={`${item.email || '无邮箱'} · ${item.disabled ? '已停用' : '启用'}`} actions={canManageCurrent && !item.disabled && <DeleteButton onClick={() => void remove(`/authorization/users/${item.id}?reason=${encodeURIComponent('管理员停用账号')}`, `确认停用账号“${item.username}”？其有效任职将一并停用。`)} />} />)}<h3 className="pt-4 text-sm font-medium text-manus-text">任职</h3>{(data.assignments || []).map(item => <Row key={`assignment-${item.id}`} title={userById.get(item.user_id)?.username || item.user_id} meta={`${item.position_name} · ${item.is_primary ? '主任职' : '兼任'} · ${item.status ? '有效' : '停用'}`} actions={canManageCurrent && <RowActions onEdit={() => setForm({ _editing_id: item.id, user_id: item.user_id, position_id: item.position_id, is_primary: item.is_primary, starts_at: toLocalInput(item.starts_at), ends_at: item.ends_at ? toLocalInput(item.ends_at) : '', status: item.status })} onDelete={() => void remove(`/authorization/assignments/${item.id}`, '确认删除这条任职？')} />} />)}</>}
            {tab === 'role' && (data.roles || []).map(item => <Row key={item.id} title={item.name} meta={`${item.permission_codes.length} 项能力 · ${item.is_system ? '系统模板（不可编辑）' : '自定义角色'}`} tags={item.permission_codes} actions={canManageCurrent && !item.is_system && <RowActions onEdit={() => setForm({ _editing_id: item.id, name: item.name, description: item.description || '', permission_codes: [...item.permission_codes] })} onDelete={() => void remove(`/authorization/roles/${item.id}`, `确认删除角色“${item.name}”？`)} />} />)}
            {tab === 'binding' && (data.bindings || []).map(item => <Row key={item.id} title={`${item.position_name} → ${item.role_name}`} meta={`${orgById.get(item.org_unit_id)?.name || '未知组织'} · ${scopeLabel(item.scope_type)} · 影响 ${item.affected_user_count ?? (data.assignments || []).filter(a => a.position_id === item.position_id && a.status).length} 人 · ${formatRange(item.starts_at, item.ends_at)}`} actions={canManageCurrent && <RowActions onEdit={() => setForm({ _editing_id: item.id, position_id: item.position_id, role_id: item.role_id, scope_type: item.scope_type, scope_org_unit_id: item.scope_org_unit_id || '', custom_org_unit_ids: [...(item.custom_org_unit_ids || [])], starts_at: toLocalInput(item.starts_at), ends_at: item.ends_at ? toLocalInput(item.ends_at) : '', status: item.status })} onDelete={() => void remove(`/authorization/role-bindings/${item.id}`, '确认撤销这条角色授权？')} />} />)}
            {tab === 'exception' && (data.exceptions || []).map(item => <Row key={item.id} title={`${userById.get(item.user_id)?.username || item.user_id} · ${item.effect_type === 'deny' ? '拒绝' : '允许'}`} meta={`${item.reason} · 责任人 ${userById.get(item.owner_id)?.username || item.owner_id} · 到期 ${formatTime(item.ends_at)} · ${item.status ? '有效' : '已撤销'}`} tags={item.capability_codes} actions={canManageCurrent && item.status && <DeleteButton onClick={() => void remove(`/authorization/exceptions/${item.id}?reason=${encodeURIComponent('管理员手动撤销')}`, '确认撤销这条例外？')} />} />)}
            {tab === 'audit' && <AuditPanel users={data.users || []} selectedUser={selectedUser} setSelectedUser={setSelectedUser} loadEffective={loadEffective} effective={effective} audits={data.audits || []} />}
            {!loading && tab !== 'audit' && !(tab === 'assignment'
              ? (data.users || []).length || (data.assignments || []).length
              : (data[tab === 'org' ? 'orgs' : `${tab}s`] || []).length) && <div className="py-16 text-center text-manus-muted">暂无数据</div>}
          </CardContent></Card>

          {tab !== 'audit' && <Card><CardHeader><CardTitle className="flex items-center justify-between"><span className="flex items-center"><Plus className="h-4 w-4 mr-2" />{form._editing_id ? '编辑' : '新增'}{tabs.find(item => item.id === tab)?.label}</span>{form._editing_id && <Button size="sm" variant="ghost" onClick={() => setForm({ starts_at: nowLocal(), ends_at: afterDays(30) })}>取消编辑</Button>}</CardTitle></CardHeader><CardContent>{canManageCurrent ? <><Editor tab={tab} form={form} setForm={setForm} data={data} selectedOrg={selectedOrg} /><Button className="w-full mt-4" onClick={() => void submit()}>保存并立即生效</Button></> : <div className="py-10 text-center text-sm text-manus-muted">当前账号只有查看权限</div>}</CardContent></Card>}
        </div>
      </div>
    </div>
  )
}

function Editor({ tab, form, setForm, data, selectedOrg }: { tab: Tab; form: Entity; setForm: (value: Entity) => void; data: Record<string, Entity[]>; selectedOrg: number | null }) {
  const field = (key: string, value: any) => setForm({ ...form, [key]: value })
  const toggleNumber = (key: string, value: number, checked: boolean) => field(key, checked ? [...(form[key] || []), value] : (form[key] || []).filter((id: number) => id !== value))
  if (tab === 'org') return <div className="space-y-3"><Input placeholder="组织名称" value={form.name || ''} onChange={e => field('name', e.target.value)} /><Input placeholder="组织编码" value={form.code || ''} onChange={e => field('code', e.target.value)} /><Select value={form.parent_id || ''} onChange={v => field('parent_id', v)} options={(data.orgs || []).filter(x => x.id !== form._editing_id).map(x => [x.id, x.name])} placeholder="上级组织（可选，可在此移动）" /><label className="flex items-center gap-2 text-sm text-manus-text"><input type="checkbox" checked={form.status !== false} onChange={e => field('status', e.target.checked)} />启用组织</label></div>
  if (tab === 'position') return <div className="space-y-3"><Input placeholder="岗位名称" value={form.name || ''} onChange={e => field('name', e.target.value)} /><Input placeholder="岗位编码" value={form.code || ''} onChange={e => field('code', e.target.value)} /><Select value={form.org_unit_id || selectedOrg || ''} onChange={v => field('org_unit_id', v)} options={(data.orgs || []).map(x => [x.id, x.name])} placeholder="所属组织" /></div>
  if (tab === 'assignment') return <div className="space-y-3"><label className="flex items-center gap-2 text-sm text-manus-text"><input type="checkbox" checked={Boolean(form.create_user)} onChange={e => field('create_user', e.target.checked)} />同时创建新账号和主任职</label>{form.create_user ? <><Input placeholder="登录名" value={form.username || ''} onChange={e => field('username', e.target.value)} /><Input type="password" placeholder="初始密码（至少 8 位）" value={form.password || ''} onChange={e => field('password', e.target.value)} /><Input type="email" placeholder="邮箱（可选）" value={form.email || ''} onChange={e => field('email', e.target.value)} /></> : <Select value={form.user_id || ''} onChange={v => field('user_id', v)} options={(data.users || []).filter(x => !x.disabled).map(x => [x.id, x.username])} placeholder="选择人员" />}<Select value={form.position_id || ''} onChange={v => field('position_id', v)} options={(data.positions || []).filter(x => x.status).map(x => [x.id, x.name])} placeholder="选择岗位" />{!form.create_user && <label className="flex items-center gap-2 text-sm text-manus-text"><input type="checkbox" checked={Boolean(form.is_primary)} onChange={e => field('is_primary', e.target.checked)} />设为主任职</label>}<DateFields form={form} field={field} /></div>
  if (tab === 'role') return <div className="space-y-3"><Input placeholder="角色名称" value={form.name || ''} onChange={e => field('name', e.target.value)} /><Input placeholder="角色说明" value={form.description || ''} onChange={e => field('description', e.target.value)} /><div className="max-h-72 overflow-auto space-y-2">{(data.capabilities || []).map(item => <label key={item.code} className="flex items-start gap-2 rounded border border-manus-border p-2 text-sm"><input type="checkbox" checked={(form.permission_codes || []).includes(item.code)} onChange={e => field('permission_codes', e.target.checked ? [...(form.permission_codes || []), item.code] : (form.permission_codes || []).filter((x: string) => x !== item.code))} /><span><b className="text-manus-text">{item.description}</b><small className="block text-manus-muted">{item.code}</small></span></label>)}</div></div>
  if (tab === 'binding') return <div className="space-y-3"><Select value={form.position_id || ''} onChange={v => field('position_id', v)} options={(data.positions || []).filter(x => x.status).map(x => [x.id, `${x.name}（${(data.assignments || []).filter(a => a.position_id === x.id && a.status).length} 人）`])} placeholder="选择岗位" /><Select value={form.role_id || ''} onChange={v => field('role_id', v)} options={(data.roles || []).map(x => [x.id, x.name])} placeholder="选择角色" /><Select value={form.scope_type || 'self'} onChange={v => field('scope_type', v)} options={[['self','岗位所在组织'],['org_unit','指定组织'],['org_tree','指定组织及下级'],['custom','自定义组织集合'],['workspace','整个工作区']]} placeholder="授权范围" />{['org_unit','org_tree'].includes(form.scope_type) && <Select value={form.scope_org_unit_id || ''} onChange={v => field('scope_org_unit_id', v)} options={(data.orgs || []).map(x => [x.id, x.name])} placeholder="选择授权组织" />}{form.scope_type === 'custom' && <OrgChecks orgs={data.orgs || []} selected={form.custom_org_unit_ids || []} onToggle={(id, checked) => toggleNumber('custom_org_unit_ids', id, checked)} />}<DateFields form={form} field={field} /></div>
  return <div className="space-y-3"><Select value={form.user_id || ''} onChange={v => field('user_id', v)} options={(data.users || []).map(x => [x.id, x.username])} placeholder="选择用户" /><Select value={form.effect_type || 'allow'} onChange={v => field('effect_type', v)} options={[['allow','临时允许'],['deny','临时拒绝']]} placeholder="例外类型" /><Select value={form.owner_id || ''} onChange={v => field('owner_id', v)} options={(data.users || []).map(x => [x.id, x.username])} placeholder="责任人（默认当前操作者）" /><Select value={form.scope_type || 'workspace'} onChange={v => field('scope_type', v)} options={[['workspace','整个可委派范围'],['custom','指定组织集合']]} placeholder="组织范围" />{form.scope_type === 'custom' && <OrgChecks orgs={data.orgs || []} selected={form.scope_org_unit_ids || []} onToggle={(id, checked) => toggleNumber('scope_org_unit_ids', id, checked)} />}<Input placeholder="原因（必填）" value={form.reason || ''} onChange={e => field('reason', e.target.value)} /><div className="max-h-56 overflow-auto space-y-2">{(data.capabilities || []).map(item => <label key={item.code} className="flex gap-2 text-sm"><input type="checkbox" checked={(form.capability_codes || []).includes(item.code)} onChange={e => field('capability_codes', e.target.checked ? [...(form.capability_codes || []), item.code] : (form.capability_codes || []).filter((x: string) => x !== item.code))} />{item.description}</label>)}</div><DateFields form={form} field={field} /><p className="text-xs text-manus-muted">例外必须有责任人和到期时间，单次最长 90 天；服务端会校验操作者的能力与组织委派边界。</p></div>
}

function DateFields({ form, field }: { form: Entity; field: (key: string, value: any) => void }) { return <div className="grid grid-cols-2 gap-2"><label className="text-xs text-manus-muted">开始<Input type="datetime-local" value={form.starts_at || nowLocal()} onChange={e => field('starts_at', e.target.value)} /></label><label className="text-xs text-manus-muted">结束<Input type="datetime-local" value={form.ends_at || ''} onChange={e => field('ends_at', e.target.value)} /></label></div> }
function Select({ value, onChange, options, placeholder }: { value: any; onChange: (value: string) => void; options: any[][]; placeholder: string }) { return <select value={value} onChange={e => onChange(e.target.value)} className="h-10 w-full rounded-md border border-manus-border bg-manus px-3 text-sm text-manus-text"><option value="">{placeholder}</option>{options.map(([id,label]) => <option key={id} value={id}>{label}</option>)}</select> }
function Row({ title, meta, tags = [], actions }: { title: string; meta: string; tags?: string[]; actions?: ReactNode }) { return <div className="flex items-start gap-3 rounded-lg border border-manus-border p-3"><div className="min-w-0 flex-1"><div className="font-medium text-manus-text">{title}</div><div className="text-xs text-manus-muted mt-1">{meta}</div>{tags.length > 0 && <div className="flex flex-wrap gap-1 mt-2">{tags.slice(0,8).map(tag => <span key={tag} className="rounded bg-manus-tertiary px-2 py-0.5 text-[11px] text-manus-muted">{tag}</span>)}</div>}</div>{actions}</div> }
function DeleteButton({ onClick }: { onClick: () => void }) { return <Button type="button" size="icon" variant="ghost" className="shrink-0 text-manus-muted hover:text-red-500" onClick={onClick}><Trash2 className="h-4 w-4" /></Button> }
function RowActions({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) { return <div className="flex shrink-0"><Button type="button" size="icon" variant="ghost" className="text-manus-muted" onClick={onEdit}><Pencil className="h-4 w-4" /></Button><DeleteButton onClick={onDelete} /></div> }
function OrgChecks({ orgs, selected, onToggle }: { orgs: Entity[]; selected: number[]; onToggle: (id: number, checked: boolean) => void }) { return <div className="max-h-44 overflow-auto rounded-md border border-manus-border p-2 space-y-1">{orgs.map(org => <label key={org.id} className="flex items-center gap-2 py-1 text-sm text-manus-text" style={{ paddingLeft: `${org.level * 12}px` }}><input type="checkbox" checked={selected.includes(org.id)} onChange={e => onToggle(org.id, e.target.checked)} />{org.name}</label>)}</div> }
function AuditPanel({ users, selectedUser, setSelectedUser, loadEffective, effective, audits }: any) { return <div className="space-y-5"><div className="flex gap-2"><Select value={selectedUser} onChange={setSelectedUser} options={users.map((x: any) => [x.id,x.username])} placeholder="选择用户" /><Button onClick={loadEffective}>计算最终权限</Button></div>{effective && <div className="rounded-lg border border-accent/30 bg-accent/5 p-4 space-y-2"><div className="font-medium text-manus-text">授权版本 {effective.revision}</div><div className="text-sm text-manus-muted">{effective.assignments.length} 条有效任职 · {effective.role_bindings.length} 条角色授权 · {effective.exception_ids.length} 条用户例外</div><div className="flex flex-wrap gap-1">{effective.capabilities.map((code: string) => <span key={code} className="rounded bg-accent/15 px-2 py-1 text-xs text-accent">{code}</span>)}</div></div>}<h3 className="font-medium text-manus-text">最近变更</h3>{audits.map((item: any) => <Row key={item.id} title={item.action} meta={`${item.actor_id} · ${formatTime(item.created_at)} · ${item.target_type}#${item.target_id || '-'}`} />)}</div> }
function formatTime(value: string) { return value ? new Date(value).toLocaleString() : '长期' }
function toLocalInput(value: string) { return new Date(new Date(value).getTime() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16) }
function formatRange(startsAt: string, endsAt: string) { return `${formatTime(startsAt)} 至 ${formatTime(endsAt)}` }
function scopeLabel(value: string) { return ({ self: '岗位所在组织', org_unit: '指定组织', org_tree: '组织及下级', workspace: '整个工作区', custom: '自定义组织' } as Record<string,string>)[value] || value }
