import { describe, expect, it } from 'vitest'

import {
  evidenceAccessLevel,
  evidenceBaselineAccess,
  evidenceFieldDecision,
  evidenceRequiresIndividualReview,
  isBulkReviewableFieldGrant,
  isBulkReviewableTableGrant,
  isLowEvidenceConfidence,
  isSafeDefault,
  summarizeEvidenceConfirmations,
  summarizeEvidenceReview,
} from './semanticAccessEvidenceV2Utils'

describe('semantic access evidence v2 mappings', () => {
  it('maps legacy asset classes without expanding access', () => {
    expect(evidenceBaselineAccess({ access_class: 'workspace_public' })).toBe('workspace_visible')
    expect(evidenceBaselineAccess({ access_class: 'department_scoped' })).toBe('controlled')
    expect(evidenceRequiresIndividualReview({ access_class: 'restricted' })).toBe(true)
    expect(evidenceRequiresIndividualReview({ is_sensitive: true })).toBe(true)
  })

  it('derives table access from legacy role and row scope', () => {
    expect(evidenceAccessLevel({
      asset_type: 'table',
      relation_role: 'none',
      row_scope: { type: 'all' },
    })).toBe('hidden')
    expect(evidenceAccessLevel({
      asset_type: 'table',
      relation_role: 'owner',
      row_scope: { type: 'all' },
    })).toBe('visible')
    expect(evidenceAccessLevel({
      asset_type: 'table',
      relation_role: 'required_consumer',
      row_scope: { type: 'target_org' },
    })).toBe('partial')
  })

  it('treats hidden decisions as safe defaults', () => {
    expect(evidenceFieldDecision({
      asset_type: 'column',
      access_decision: 'inherit',
    })).toBe('hidden')
    expect(isSafeDefault(undefined, {
      asset_type: 'column',
      field_decision: 'hidden',
    })).toBe(true)
    expect(isSafeDefault(undefined, {
      asset_type: 'column',
      field_decision: 'visible',
    })).toBe(false)
  })

  it('bulk reviews only ordinary full-row table grants', () => {
    const relation = {
      asset_type: 'table' as const,
      access_level: 'visible' as const,
      row_scope: { type: 'all' as const },
      review_status: 'pending',
    }
    expect(isBulkReviewableTableGrant({
      baseline_access: 'controlled',
    }, relation)).toBe(true)
    expect(isBulkReviewableTableGrant({
      baseline_access: 'controlled',
      requires_individual_review: true,
    }, relation)).toBe(false)
    expect(isBulkReviewableTableGrant({}, {
      ...relation,
      access_level: 'partial',
      row_scope: { type: 'target_org' },
    })).toBe(false)
  })

  it('marks confidence below sixty percent and keeps the boundary unmarked', () => {
    expect(isLowEvidenceConfidence(0.59)).toBe(true)
    expect(isLowEvidenceConfidence(0.6)).toBe(false)
    expect(isLowEvidenceConfidence(null)).toBe(false)
  })

  it('bulk reviews only non-sensitive fields on ordinary tables', () => {
    const relation = {
      asset_type: 'column' as const,
      field_decision: 'visible' as const,
      review_status: 'pending',
    }
    expect(isBulkReviewableFieldGrant({}, relation, false)).toBe(true)
    expect(isBulkReviewableFieldGrant({}, relation, true)).toBe(false)
    expect(isBulkReviewableFieldGrant({
      requires_individual_review: true,
    }, relation, false)).toBe(false)
    expect(isBulkReviewableFieldGrant({}, {
      ...relation,
      field_decision: 'hidden',
    }, false)).toBe(false)
  })

  it('summarizes per-table and per-department confirmation actions', () => {
    const assets = [
      {
        table_id: 10,
        baseline_access: 'controlled' as const,
        review_status: 'pending',
      },
      {
        table_id: 11,
        baseline_access: 'controlled' as const,
        requires_individual_review: true,
        review_status: 'pending',
      },
    ]
    const relations = [
      {
        asset_type: 'table' as const,
        asset_id: 10,
        table_id: 10,
        org_unit_id: 1,
        access_level: 'visible' as const,
        row_scope: { type: 'all' as const },
        review_status: 'pending',
      },
      {
        asset_type: 'column' as const,
        asset_id: 101,
        table_id: 10,
        org_unit_id: 1,
        field_decision: 'visible' as const,
        review_status: 'pending',
      },
      {
        asset_type: 'column' as const,
        asset_id: 102,
        table_id: 10,
        org_unit_id: 1,
        field_decision: 'visible' as const,
        review_status: 'pending',
      },
      {
        asset_type: 'table' as const,
        asset_id: 11,
        table_id: 11,
        org_unit_id: 1,
        access_level: 'visible' as const,
        row_scope: { type: 'all' as const },
        review_status: 'pending',
      },
    ]

    expect(summarizeEvidenceConfirmations(
      assets,
      relations,
      'org_unit',
      '1',
      new Set([102]),
      10,
    )).toEqual({
      tablePendingItems: 3,
      tableConfirmed: false,
      targetPendingItems: 4,
      pendingHighRiskTableIds: [11],
      pendingSensitiveTableIds: [10],
      pendingRiskTableIds: [10, 11],
    })
  })

  it('marks a table bundle confirmed after all of its visible items are accepted', () => {
    expect(summarizeEvidenceConfirmations(
      [{ table_id: 10, baseline_access: 'controlled', review_status: 'pending' }],
      [
        {
          asset_type: 'table',
          asset_id: 10,
          table_id: 10,
          org_unit_id: 1,
          access_level: 'visible',
          row_scope: { type: 'all' },
          review_status: 'accepted',
        },
        {
          asset_type: 'column',
          asset_id: 101,
          table_id: 10,
          org_unit_id: 1,
          field_decision: 'visible',
          review_status: 'accepted',
        },
      ],
      'org_unit',
      '1',
      new Set(),
      10,
    )).toEqual({
      tablePendingItems: 0,
      tableConfirmed: true,
      targetPendingItems: 0,
      pendingHighRiskTableIds: [],
      pendingSensitiveTableIds: [],
      pendingRiskTableIds: [],
    })
  })

  it('keeps a sensitive table in the department warning until its visible field is confirmed', () => {
    expect(summarizeEvidenceConfirmations(
      [{ table_id: 10, baseline_access: 'controlled', review_status: 'pending' }],
      [
        {
          asset_type: 'table',
          asset_id: 10,
          table_id: 10,
          org_unit_id: 1,
          access_level: 'visible',
          row_scope: { type: 'all' },
          review_status: 'accepted',
        },
        {
          asset_type: 'column',
          asset_id: 101,
          table_id: 10,
          org_unit_id: 1,
          field_decision: 'visible',
          review_status: 'pending',
        },
      ],
      'org_unit',
      '1',
      new Set([101]),
      10,
    )).toEqual({
      tablePendingItems: 1,
      tableConfirmed: false,
      targetPendingItems: 1,
      pendingHighRiskTableIds: [],
      pendingSensitiveTableIds: [10],
      pendingRiskTableIds: [10],
    })
  })

  it('summarizes department completion and remaining risk items', () => {
    const assets = [
      {
        table_id: 10,
        baseline_access: 'controlled' as const,
        requires_individual_review: true,
        review_status: 'pending',
      },
      {
        table_id: 11,
        baseline_access: 'controlled' as const,
        review_status: 'accepted',
      },
    ]
    const relations = [
      {
        asset_type: 'table' as const,
        asset_id: 10,
        table_id: 10,
        org_unit_id: 1,
        access_level: 'visible' as const,
        row_scope: { type: 'all' as const },
        review_status: 'pending',
      },
      {
        asset_type: 'column' as const,
        asset_id: 101,
        table_id: 10,
        org_unit_id: 1,
        field_decision: 'visible' as const,
        review_status: 'pending',
      },
      {
        asset_type: 'table' as const,
        asset_id: 11,
        table_id: 11,
        org_unit_id: 2,
        access_level: 'visible' as const,
        row_scope: { type: 'all' as const },
        review_status: 'accepted',
      },
    ]

    expect(summarizeEvidenceReview(
      assets,
      relations,
      [1, 2],
      new Set([101]),
    )).toEqual({
      departmentsReviewed: 1,
      departmentsTotal: 2,
      remainingRiskItems: 2,
    })
  })
})
