import type {
  SemanticAccessBootstrapRun,
  SemanticAccessBootstrapSuggestions,
} from '@/features/semanticAccess/types'

export const BOOTSTRAP_POLLING_STATUSES = new Set<SemanticAccessBootstrapRun['status']>([
  'pending',
  'running',
  'cancel_requested',
])

export const BOOTSTRAP_MATRIX_LABEL_WIDTH = 280
export const BOOTSTRAP_MATRIX_TARGET_WIDTH = 190

export function bootstrapMatrixWidth(targetCount: number) {
  return BOOTSTRAP_MATRIX_LABEL_WIDTH
    + Math.max(0, targetCount) * BOOTSTRAP_MATRIX_TARGET_WIDTH
}

export function bootstrapRunNeedsPolling(status: SemanticAccessBootstrapRun['status']) {
  return BOOTSTRAP_POLLING_STATUSES.has(status)
}

export function bootstrapValidationBlockers(
  suggestions: SemanticAccessBootstrapSuggestions | null,
) {
  return suggestions?.targets.flatMap(target => target.included
    ? (target.validation.blockers || []).map(issue => ({
        ...issue,
        target: target.target_label,
      }))
    : []) || []
}

export function canApplyBootstrapReview(
  run: SemanticAccessBootstrapRun | null,
  suggestions: SemanticAccessBootstrapSuggestions | null,
  hasUnsavedRule: boolean,
) {
  return Boolean(
    run?.status === 'review_ready'
    && suggestions
    && !hasUnsavedRule
    && bootstrapValidationBlockers(suggestions).length === 0
    && suggestions.targets.some(target => (
      target.included && (target.definition.tables?.length || 0) > 0
    )),
  )
}

export function bootstrapReviewSummary(
  suggestions: SemanticAccessBootstrapSuggestions | null,
) {
  const candidates = suggestions?.targets.flatMap(target => target.candidates.map(candidate => ({
    candidate,
    rule: target.definition.tables?.find(rule => rule.table_id === candidate.table_id),
  }))) || []
  return {
    recommended: candidates.filter(item => (
      item.candidate.selected && (item.candidate.review_state || 'pending') === 'pending'
    )).length,
    accepted: candidates.filter(item => (
      ['accepted', 'modified'].includes(item.candidate.review_state || '')
    )).length,
    review: candidates.filter(item => (
      item.candidate.confidence_level === 'medium' && !item.rule
    )).length,
    blocked: bootstrapValidationBlockers(suggestions).length,
  }
}
