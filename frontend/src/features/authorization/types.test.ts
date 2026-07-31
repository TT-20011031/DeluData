import { describe, expect, it } from 'vitest'
import { assignmentStateLabel, buildOrgTree, descendantOrgIds, type AccountAssignment, type OrgUnit } from './types'

const org = (id: number, parent_id: number | null, order_num = 0): OrgUnit => ({
  id,
  parent_id,
  order_num,
  workspace_id: 'w',
  name: `部门${id}`,
  code: null,
  leader_id: null,
  ancestors: '/',
  level: parent_id == null ? 0 : 1,
  status: true,
})

const assignment = (overrides: Partial<AccountAssignment> = {}): AccountAssignment => ({
  id: 1,
  user_id: 'u',
  username: 'tester',
  email: null,
  disabled: false,
  position_id: 1,
  position_name: '岗位',
  org_unit_id: 1,
  is_primary: false,
  starts_at: '2026-01-01T00:00:00Z',
  ends_at: null,
  status: true,
  ...overrides,
})

describe('authorization organization helpers', () => {
  it('builds and sorts a department tree', () => {
    const tree = buildOrgTree([org(3, 1), org(2, null, 2), org(1, null, 1)])
    expect(tree.map(item => item.id)).toEqual([1, 2])
    expect(tree[0].children.map(item => item.id)).toEqual([3])
  })

  it('collects the selected department and every descendant', () => {
    expect([...descendantOrgIds([org(1, null), org(2, 1), org(3, 2), org(4, null)], 1)]).toEqual([1, 2, 3])
  })

  it('labels assignment lifecycle states', () => {
    const now = new Date('2026-07-16T00:00:00Z')
    expect(assignmentStateLabel(assignment(), now)).toBe('有效')
    expect(assignmentStateLabel(assignment({ starts_at: '2026-08-01T00:00:00Z' }), now)).toBe('未生效')
    expect(assignmentStateLabel(assignment({ ends_at: '2026-06-01T00:00:00Z' }), now)).toBe('已到期')
    expect(assignmentStateLabel(assignment({ status: false }), now)).toBe('已停用')
  })
})
