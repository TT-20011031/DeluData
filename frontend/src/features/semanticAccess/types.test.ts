import { describe, expect, it } from 'vitest'
import { semanticTargetKey, semanticTargetLabel } from './types'

describe('semantic organization targets', () => {
  it('uses stable target keys across organization layers', () => {
    expect(semanticTargetKey({ target_type: 'org_unit', target_id: '12' })).toBe('org_unit:12')
    expect(semanticTargetKey({ target_type: 'position', target_id: '8' })).toBe('position:8')
    expect(semanticTargetKey({ target_type: 'user', target_id: 'user-a' })).toBe('user:user-a')
  })

  it('contains no legacy role or exception labels', () => {
    expect(Object.keys(semanticTargetLabel)).toEqual(['baseline', 'org_unit', 'position', 'user'])
    expect(Object.values(semanticTargetLabel)).toEqual(['全员基线', '部门', '岗位', '账号'])
  })
})
