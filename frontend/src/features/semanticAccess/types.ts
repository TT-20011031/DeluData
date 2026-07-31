import type { SemanticAccessTableRule } from '@/types/extendConfig'

export type SemanticAccessTargetType = 'baseline' | 'org_unit' | 'position' | 'user'

export type SemanticAccessTargetSummary = {
  target_type: SemanticAccessTargetType
  target_id: string
  label: string
  binding_id: number | null
  revision: number
  configured: boolean
  include_descendants: boolean
  affected_user_count: number
  has_explicit_deny: boolean
  org_unit_id?: number | null
  parent_id?: number | null
  position_id?: number
  user_id?: string
  email?: string | null
  disabled?: boolean
  status?: boolean
}

export type SemanticAccessTargetPage = {
  items: SemanticAccessTargetSummary[]
  total: number
  page: number
  page_size: number
  can_manage_workspace: boolean
}

export type SemanticAccessVersion = {
  id: number
  binding_id: number
  version: number
  active?: boolean
  definition: {
    name?: string
    tables?: SemanticAccessTableRule[]
  }
  validation?: {
    blockers?: SemanticAccessValidationIssue[]
    warnings?: SemanticAccessValidationIssue[]
  }
  created_by?: string
  created_at?: string
  source_type?: 'manual' | 'natural_language' | 'rollback' | 'ai_bootstrap' | string
  source_ref?: string | null
}

export type SemanticAccessBootstrapIssue = {
  code: string
  message: string
  target?: string
  table_ids?: number[]
}

export type SemanticAccessBootstrapRunStatus =
  | 'pending'
  | 'running'
  | 'cancel_requested'
  | 'review_ready'
  | 'partial'
  | 'applied'
  | 'failed'
  | 'cancelled'
  | 'stale'
  | 'discarded'

export type SemanticAccessBootstrapRun = {
  run_id: number
  workspace_id?: string
  datasource_id: number
  evidence_set_id?: number | null
  status: SemanticAccessBootstrapRunStatus
  stage: string
  progress: number
  revision: number
  schema_fingerprint?: string | null
  organization_fingerprint?: string
  business_context?: string | null
  summary: Record<string, unknown> & {
    target_count?: number
    included_target_count?: number
    mapping_suggestion_count?: number
    selected_table_rule_count?: number
    applied_target_count?: number
    bootstrap_mode?: 'table_field_only' | string
    row_level_configured?: boolean
    low_confidence_table_count?: number
    low_confidence_field_count?: number
    sensitive_table_count?: number
    sensitive_field_count?: number
  }
  error_message?: string | null
  worker_id?: string | null
  attempt?: number
  max_attempts?: number
  retryable: boolean
  coalesced?: boolean
  cancel_requested_at?: string | null
  created_at?: string | null
  started_at?: string | null
  completed_at?: string | null
  applied_at?: string | null
}

export type SemanticAccessBootstrapReadiness = {
  ready: boolean
  datasource_id: number
  schema_fingerprint?: string | null
  organization_fingerprint: string
  eligible_table_count: number
  eligible_column_count: number
  eligible_table_ids: number[]
  ignored_table_count: number
  top_level_departments: Array<{ id: number; name: string; code?: string | null }>
  unconfigured_target_count: number
  configured_target_count: number
  configured_targets_skipped: Array<{ target_type: string; target_id: string }>
  ownership_mapping_count: number
  ownership_mapping_coverage: number
  blockers: SemanticAccessBootstrapIssue[]
  warnings: SemanticAccessBootstrapIssue[]
  active_run: SemanticAccessBootstrapRun | null
}

export type SemanticAccessBootstrapCandidate = {
  table_id: number
  selected: boolean
  decision?: 'visible' | 'hidden'
  review_state?: 'pending' | 'accepted' | 'rejected' | 'modified'
  confidence: number
  confidence_level: 'high' | 'medium' | 'low'
  reason: string
  row_scope: 'all' | 'target_org_tree' | null
  requires_mapping: boolean
  has_mapping_proposal: boolean
  sensitive: boolean
  hidden_column_ids: number[]
  hidden_metric_ids: number[]
  field_summary?: {
    visible: number
    hidden: number
    sensitive_hidden: number
    attention: number
  }
  field_suggestions?: Array<{
    column_id: number
    decision: 'visible' | 'hidden'
    effective_decision?: 'visible' | 'hidden'
    confidence: number
    confidence_level: 'high' | 'medium' | 'low'
    reason: string
    source: 'governance' | 'ai' | 'fallback' | string
    review_state?: 'pending' | 'accepted' | 'rejected' | 'modified'
  }>
}

export type SemanticAccessBootstrapTarget = {
  id: number | null
  run_id: number
  target_type: SemanticAccessTargetType
  target_id: string
  target_label: string
  include_descendants: boolean
  base_binding_id: number | null
  base_revision: number
  included: boolean
  status: string
  confidence: number
  definition: { name?: string; tables?: SemanticAccessTableRule[] }
  candidates: SemanticAccessBootstrapCandidate[]
  explanations: Array<{ table_id: number; confidence: number; reason: string }>
  validation: {
    blockers?: SemanticAccessBootstrapIssue[]
    warnings?: SemanticAccessBootstrapIssue[]
    base_active_version_id?: number | null
  }
  updated_at?: string
}

export type SemanticAccessBootstrapMapping = {
  id: number
  run_id: number
  table_id: number
  table_label: string
  status: string
  accepted: boolean
  confidence: number
  base_mapping: Record<string, unknown>
  proposed_mapping: {
    org_column_id?: number | null
    org_value_kind?: 'id' | 'code'
    user_column_id?: number | null
    user_value_kind?: 'id' | 'username'
  }
  evidence: string[]
  validation: { blockers?: SemanticAccessBootstrapIssue[]; warnings?: SemanticAccessBootstrapIssue[] }
  updated_at?: string
}

export type SemanticAccessBootstrapSuggestions = {
  run: SemanticAccessBootstrapRun
  targets: SemanticAccessBootstrapTarget[]
  ownership_mappings: SemanticAccessBootstrapMapping[]
  assets: {
    tables: Array<{
      id: number
      business_name: string
      physical_name: string
      is_sensitive: boolean
    }>
    columns: Array<{
      id: number
      table_id: number
      business_name: string
      physical_name: string
      data_type: string
      is_sensitive: boolean
      ordinal_position: number
    }>
  }
}

export type SemanticAccessBootstrapPreview = {
  run_id: number
  blockers: SemanticAccessBootstrapIssue[]
  missing_departments: Array<{ target_id: string; target_label: string }>
  items: Array<{
    user_id: string
    username: string
    assignments: Array<{ org_unit_name: string; position_name: string; is_primary: boolean }>
    semantic: {
      is_admin?: boolean
      decisions: SemanticEffectiveDecision[]
      table_predicates: Record<string, string>
    }
  }>
}

export type SemanticAccessBinding = {
  id: number | null
  binding_id: number | null
  datasource_id: number
  target_type: SemanticAccessTargetType
  target_id: string
  include_descendants: boolean
  active_version_id: number | null
  active_version: SemanticAccessVersion | null
  revision: number
  status: boolean
  affected_user_count?: number
}

export type SemanticAccessValidationIssue = {
  code: string
  message: string
}

export type SemanticOwnershipMapping = {
  table_id: number
  org_column_id: number | null
  org_value_kind: 'id' | 'code'
  user_column_id: number | null
  user_value_kind: 'id' | 'username'
}

export type SemanticPolicySource = {
  binding_id: number | null
  version_id: number | string | null
  target_type: SemanticAccessTargetType
  target_id: string
  effect_type: string
}

export type SemanticEffectiveDecision = {
  asset_key: string
  allowed: boolean
  reason: 'explicit_hidden' | 'explicit_visible' | 'default_deny' | string
  sources: SemanticPolicySource[]
}

export type SemanticEffectivePreview = {
  authorization: {
    assignments?: Array<{
      position_id: number
      position_name: string
      org_unit_id: number
      org_unit_name: string
      is_primary: boolean
    }>
  }
  semantic: {
    decisions: SemanticEffectiveDecision[]
    table_predicates: Record<string, string>
    candidate_validation?: {
      blockers?: SemanticAccessValidationIssue[]
      warnings?: SemanticAccessValidationIssue[]
    } | null
  }
}

export const semanticTargetKey = (
  target: Pick<SemanticAccessTargetSummary, 'target_type' | 'target_id'>,
) => `${target.target_type}:${target.target_id}`

export const semanticTargetLabel: Record<SemanticAccessTargetType, string> = {
  baseline: '全员基线',
  org_unit: '部门',
  position: '岗位',
  user: '账号',
}
