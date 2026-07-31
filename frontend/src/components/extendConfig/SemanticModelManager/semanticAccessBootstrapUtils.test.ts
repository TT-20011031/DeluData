import { describe, expect, it } from 'vitest'
import type {
  SemanticAccessBootstrapRun,
  SemanticAccessBootstrapSuggestions,
  SemanticAccessBootstrapTarget,
} from '@/features/semanticAccess/types'
import {
  bootstrapMatrixWidth,
  bootstrapRunNeedsPolling,
  bootstrapReviewSummary,
  bootstrapValidationBlockers,
  canApplyBootstrapReview,
} from './semanticAccessBootstrapUtils'

const run = (status: SemanticAccessBootstrapRun['status']): SemanticAccessBootstrapRun => ({
  run_id: 1,
  datasource_id: 8,
  status,
  stage: 'review_ready',
  progress: 100,
  revision: 2,
  schema_fingerprint: 'schema',
  organization_fingerprint: 'org',
  business_context: null,
  summary: {},
  error_message: null,
  retryable: false,
  completed_at: null,
  applied_at: null,
})

const target = (blockers: Array<{ code: string; message: string }> = []): SemanticAccessBootstrapTarget => ({
  id: 2,
  run_id: 1,
  target_type: 'baseline',
  target_id: '*',
  target_label: '全员基线',
  include_descendants: false,
  base_binding_id: null,
  base_revision: 0,
  included: true,
  status: 'proposed',
  confidence: .9,
  definition: {
    name: '全员基线',
    tables: [{
      table_id: 11,
      decision: 'visible',
      hidden_column_ids: [],
      hidden_metric_ids: [],
      row_scope: { type: 'all' },
    }],
  },
  candidates: [],
  explanations: [],
  validation: { blockers, warnings: [] },
})

const suggestions = (targets: SemanticAccessBootstrapTarget[]): SemanticAccessBootstrapSuggestions => ({
  run: run('review_ready'),
  targets,
  ownership_mappings: [],
  assets: { tables: [], columns: [] },
})

describe('semantic access bootstrap state', () => {
  it('reserves a full-width column for every authorization target', () => {
    expect(bootstrapMatrixWidth(0)).toBe(280)
    expect(bootstrapMatrixWidth(7)).toBe(1610)
  })

  it('polls only queued or actively running states', () => {
    expect(bootstrapRunNeedsPolling('pending')).toBe(true)
    expect(bootstrapRunNeedsPolling('running')).toBe(true)
    expect(bootstrapRunNeedsPolling('cancel_requested')).toBe(true)
    expect(bootstrapRunNeedsPolling('review_ready')).toBe(false)
    expect(bootstrapRunNeedsPolling('partial')).toBe(false)
  })

  it('ignores blockers on excluded targets', () => {
    const excluded = { ...target([{ code: 'missing_mapping', message: '缺少归属映射' }]), included: false }
    expect(bootstrapValidationBlockers(suggestions([excluded]))).toEqual([])
  })

  it('disables publish for blockers, partial runs, and unsaved inspector edits', () => {
    const clean = suggestions([target()])
    const blocked = suggestions([target([{ code: 'missing_mapping', message: '缺少归属映射' }])])

    expect(canApplyBootstrapReview(run('review_ready'), clean, false)).toBe(true)
    expect(canApplyBootstrapReview(run('partial'), clean, false)).toBe(false)
    expect(canApplyBootstrapReview(run('review_ready'), blocked, false)).toBe(false)
    expect(canApplyBootstrapReview(run('review_ready'), clean, true)).toBe(false)
  })

  it('summarizes recommended, accepted, medium-review, and blocked cells', () => {
    const reviewedTarget = target([{ code: 'review_pending', message: '1 条 AI 推荐尚未接受' }])
    reviewedTarget.definition.tables = [
      ...(reviewedTarget.definition.tables || []),
      {
        table_id: 12,
        decision: 'visible',
        hidden_column_ids: [102],
        hidden_metric_ids: [],
        row_scope: { type: 'all' },
      },
    ]
    reviewedTarget.candidates = [
      {
        table_id: 11,
        selected: true,
        review_state: 'pending',
        confidence: .81,
        confidence_level: 'high',
        reason: '公共表',
        row_scope: 'all',
        requires_mapping: false,
        has_mapping_proposal: false,
        sensitive: false,
        hidden_column_ids: [],
        hidden_metric_ids: [],
        field_suggestions: [],
      },
      {
        table_id: 12,
        selected: true,
        review_state: 'accepted',
        confidence: .78,
        confidence_level: 'high',
        reason: '公共表',
        row_scope: 'all',
        requires_mapping: false,
        has_mapping_proposal: false,
        sensitive: false,
        hidden_column_ids: [102],
        hidden_metric_ids: [],
        field_suggestions: [{
          column_id: 102,
          decision: 'hidden',
          confidence: 1,
          confidence_level: 'high',
          reason: '敏感字段',
          source: 'governance',
        }],
      },
      {
        table_id: 13,
        selected: false,
        review_state: 'pending',
        confidence: .62,
        confidence_level: 'medium',
        reason: '需审核',
        row_scope: 'all',
        requires_mapping: false,
        has_mapping_proposal: false,
        sensitive: false,
        hidden_column_ids: [],
        hidden_metric_ids: [],
        field_suggestions: [],
      },
    ]

    expect(bootstrapReviewSummary(suggestions([reviewedTarget]))).toEqual({
      recommended: 1,
      accepted: 1,
      review: 1,
      blocked: 1,
    })
  })
})
