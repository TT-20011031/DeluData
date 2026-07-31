export interface OrgUnit {
  id: number
  workspace_id: string
  parent_id: number | null
  name: string
  code: string | null
  leader_id: string | null
  ancestors: string
  level: number
  order_num: number
  status: boolean
}

export interface OrgTreeNode extends OrgUnit {
  children: OrgTreeNode[]
}

export interface PositionSummary {
  id: number
  org_unit_id: number
  name: string
  code: string | null
  status: boolean
  legacy_generated: boolean
  headcount: number
}

export interface Account {
  id: string
  username: string
  email: string | null
  disabled: boolean
}

export type AssignmentState = 'effective' | 'upcoming' | 'expired' | 'disabled' | 'all'

export interface AccountAssignment {
  id: number
  user_id: string
  username: string
  email: string | null
  disabled: boolean
  position_id: number
  position_name: string
  org_unit_id: number
  is_primary: boolean
  starts_at: string
  ends_at: string | null
  status: boolean
}

export interface Capability {
  code: string
  module: string
  description: string
}

export interface AuthorizationRole {
  id: number
  name: string
  description: string | null
  permission_codes: string[]
  is_system: boolean
}

export type ScopeType = 'self' | 'org_unit' | 'org_tree' | 'custom' | 'workspace'

export interface RoleBinding {
  id: number
  position_id: number
  position_name: string
  role_id: number
  role_name: string
  org_unit_id: number
  scope_type: ScopeType
  scope_org_unit_id: number | null
  custom_org_unit_ids: number[]
  starts_at: string
  ends_at: string | null
  status: boolean
  affected_user_count?: number
}

export interface DeletionBlockers {
  child_org_units?: number
  positions?: number
  assignments?: number
  role_bindings?: number
}

export function buildOrgTree(orgUnits: OrgUnit[]): OrgTreeNode[] {
  const nodes = new Map<number, OrgTreeNode>()
  orgUnits.forEach(org => nodes.set(org.id, { ...org, children: [] }))

  const roots: OrgTreeNode[] = []
  nodes.forEach(node => {
    const parent = node.parent_id == null ? null : nodes.get(node.parent_id)
    if (parent) parent.children.push(node)
    else roots.push(node)
  })

  const sortNodes = (items: OrgTreeNode[]) => {
    items.sort((a, b) => a.order_num - b.order_num || a.id - b.id)
    items.forEach(item => sortNodes(item.children))
  }
  sortNodes(roots)
  return roots
}

export function descendantOrgIds(orgUnits: OrgUnit[], orgId: number): Set<number> {
  const descendants = new Set<number>([orgId])
  let changed = true
  while (changed) {
    changed = false
    orgUnits.forEach(org => {
      if (org.parent_id != null && descendants.has(org.parent_id) && !descendants.has(org.id)) {
        descendants.add(org.id)
        changed = true
      }
    })
  }
  return descendants
}

export function assignmentStateLabel(assignment: AccountAssignment, now = new Date()): string {
  if (assignment.disabled || !assignment.status) return '已停用'
  const startsAt = new Date(assignment.starts_at)
  const endsAt = assignment.ends_at ? new Date(assignment.ends_at) : null
  if (startsAt > now) return '未生效'
  if (endsAt && endsAt <= now) return '已到期'
  return '有效'
}

export function toLocalInput(value?: string | null): string {
  const date = value ? new Date(value) : new Date()
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

export function formatDateTime(value?: string | null): string {
  return value ? new Date(value).toLocaleString() : '长期'
}
