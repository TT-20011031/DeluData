import { describe, expect, it } from 'vitest'
import {
  collaborationReferencesDepartment,
  restrictCollaborations,
  setCollaborationDepartment,
} from './organizationProfileUtils'

describe('organizationProfileUtils', () => {
  it('keeps only collaborations backed by existing peer departments', () => {
    expect(restrictCollaborations(
      ['销售部（接收订单）', '采购部（获取原料）', '财务部：成本核算'],
      ['销售部', '财务部'],
    )).toEqual(['销售部（接收订单）', '财务部：成本核算'])
  })

  it('does not treat a similarly prefixed department as the same department', () => {
    expect(collaborationReferencesDepartment('销售二部', '销售')).toBe(false)
  })

  it('adds and removes departments without preserving invalid entries', () => {
    expect(setCollaborationDepartment(
      ['不存在部门', '销售部（接收订单）'],
      ['销售部', '财务部'],
      '财务部',
      true,
    )).toEqual(['销售部（接收订单）', '财务部'])
    expect(setCollaborationDepartment(
      ['销售部（接收订单）', '财务部'],
      ['销售部', '财务部'],
      '销售部',
      false,
    )).toEqual(['财务部'])
  })
})
