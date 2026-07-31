import type { SemanticAccessRowScope } from '@/types/extendConfig'

export type EvidenceAccessLevel = 'hidden' | 'visible' | 'partial'
export type EvidenceFieldDecision = 'visible' | 'hidden'
export type EvidenceBaselineAccess = 'workspace_visible' | 'controlled'
export const EVIDENCE_LOW_CONFIDENCE_THRESHOLD = 0.6

export type EvidenceAssetLike = {
  baseline_access?: EvidenceBaselineAccess | null
  requires_individual_review?: boolean
  access_class?: 'workspace_public' | 'department_scoped' | 'restricted'
  is_sensitive?: boolean
  review_status?: string
}

export type EvidenceRelationLike = {
  asset_type: 'table' | 'column'
  access_level?: EvidenceAccessLevel | null
  field_decision?: EvidenceFieldDecision | null
  relation_role?: 'owner' | 'producer' | 'required_consumer' | 'conditional_consumer' | 'none'
  access_decision?: 'inherit' | 'visible' | 'hidden'
  row_scope?: SemanticAccessRowScope
  review_status?: string
}

export type EvidenceReviewAssetLike = EvidenceAssetLike & {
  asset_type?: 'table' | 'column'
  table_id: number
}

export type EvidenceReviewRelationLike = EvidenceRelationLike & {
  asset_id: number
  table_id: number
  org_unit_id: number
}

export type EvidenceConfirmationSummary = {
  tablePendingItems: number
  tableConfirmed: boolean
  targetPendingItems: number
  pendingHighRiskTableIds: number[]
  pendingSensitiveTableIds: number[]
  pendingRiskTableIds: number[]
}

export function evidenceBaselineAccess(asset: EvidenceAssetLike): EvidenceBaselineAccess {
  if (asset.baseline_access) return asset.baseline_access
  return asset.access_class === 'workspace_public' ? 'workspace_visible' : 'controlled'
}

export function evidenceRequiresIndividualReview(asset: EvidenceAssetLike): boolean {
  return Boolean(
    asset.requires_individual_review
    || asset.is_sensitive
    || asset.access_class === 'restricted',
  )
}

export function evidenceAccessLevel(relation: EvidenceRelationLike): EvidenceAccessLevel {
  if (relation.access_level) return relation.access_level
  if (!relation.relation_role || relation.relation_role === 'none') return 'hidden'
  return (relation.row_scope?.type || 'all') === 'all' ? 'visible' : 'partial'
}

export function evidenceFieldDecision(relation: EvidenceRelationLike): EvidenceFieldDecision {
  if (relation.field_decision) return relation.field_decision
  return relation.access_decision === 'visible' ? 'visible' : 'hidden'
}

export function isManuallyApproved(reviewStatus?: string): boolean {
  return reviewStatus === 'accepted' || reviewStatus === 'modified'
}

export function isSafeDefault(
  asset: EvidenceAssetLike | undefined,
  relation?: EvidenceRelationLike,
): boolean {
  if (relation?.asset_type === 'table') return evidenceAccessLevel(relation) === 'hidden'
  if (relation?.asset_type === 'column') return evidenceFieldDecision(relation) === 'hidden'
  return asset ? evidenceBaselineAccess(asset) === 'controlled' : true
}

export function isBulkReviewableTableGrant(
  asset: EvidenceAssetLike | undefined,
  relation: EvidenceRelationLike,
): boolean {
  return relation.asset_type === 'table'
    && evidenceAccessLevel(relation) === 'visible'
    && (relation.row_scope?.type || 'all') === 'all'
    && !evidenceRequiresIndividualReview(asset || {})
    && relation.review_status === 'pending'
}

export function isBulkReviewableFieldGrant(
  asset: EvidenceAssetLike | undefined,
  relation: EvidenceRelationLike,
  sensitive: boolean,
): boolean {
  return relation.asset_type === 'column'
    && evidenceFieldDecision(relation) === 'visible'
    && relation.review_status === 'pending'
    && !sensitive
    && !evidenceRequiresIndividualReview(asset || {})
}

export function isLowEvidenceConfidence(confidence?: number | null): boolean {
  return confidence != null && confidence < EVIDENCE_LOW_CONFIDENCE_THRESHOLD
}

export function summarizeEvidenceConfirmations(
  assets: EvidenceReviewAssetLike[],
  relations: EvidenceReviewRelationLike[],
  targetType: 'baseline' | 'org_unit',
  targetId: string,
  sensitiveColumnIds: Set<number>,
  tableId?: number,
): EvidenceConfirmationSummary {
  const tableAssets = assets.filter(asset => (
    asset.asset_type == null || asset.asset_type === 'table'
  ))
  const tableAssetById = new Map(tableAssets.map(asset => [asset.table_id, asset]))
  const targetOrgId = targetType === 'org_unit' ? Number(targetId) : null
  const targetRelations = targetType === 'org_unit'
    ? relations.filter(relation => relation.org_unit_id === targetOrgId)
    : []

  const grantTableIds = new Set<number>()
  const pendingByTableId = new Map<number, number>()
  const addPending = (pendingTableId: number) => {
    pendingByTableId.set(
      pendingTableId,
      (pendingByTableId.get(pendingTableId) || 0) + 1,
    )
  }

  if (targetType === 'baseline') {
    for (const asset of tableAssets) {
      if (evidenceBaselineAccess(asset) !== 'workspace_visible') continue
      grantTableIds.add(asset.table_id)
      if (!isManuallyApproved(asset.review_status)) addPending(asset.table_id)
    }
  } else {
    for (const relation of targetRelations) {
      if (relation.asset_type !== 'table') continue
      if (!['visible', 'partial'].includes(evidenceAccessLevel(relation))) continue
      grantTableIds.add(relation.table_id)
      if (!isManuallyApproved(relation.review_status)) addPending(relation.table_id)
    }
    for (const relation of targetRelations) {
      if (
        relation.asset_type !== 'column'
        || !grantTableIds.has(relation.table_id)
        || evidenceFieldDecision(relation) !== 'visible'
        || isManuallyApproved(relation.review_status)
      ) continue
      addPending(relation.table_id)
    }
  }

  const pendingTableIds = [...pendingByTableId.keys()]
  const pendingHighRiskTableIds = pendingTableIds.filter(pendingTableId => (
    evidenceRequiresIndividualReview(tableAssetById.get(pendingTableId) || {})
  )).sort((left, right) => left - right)
  const pendingSensitiveTableIds = pendingTableIds.filter(pendingTableId => {
    const asset = tableAssetById.get(pendingTableId)
    if (asset?.is_sensitive) return true
    return targetRelations.some(relation => (
      relation.asset_type === 'column'
      && relation.table_id === pendingTableId
      && evidenceFieldDecision(relation) === 'visible'
      && !isManuallyApproved(relation.review_status)
      && sensitiveColumnIds.has(relation.asset_id)
    ))
  }).sort((left, right) => left - right)
  const pendingRiskTableIds = [...new Set([
    ...pendingHighRiskTableIds,
    ...pendingSensitiveTableIds,
  ])].sort((left, right) => left - right)
  const tableIsGrant = tableId != null && grantTableIds.has(tableId)
  const tablePendingItems = tableId == null ? 0 : pendingByTableId.get(tableId) || 0

  return {
    tablePendingItems,
    tableConfirmed: Boolean(tableIsGrant && tablePendingItems === 0),
    targetPendingItems: [...pendingByTableId.values()]
      .reduce((total, pending) => total + pending, 0),
    pendingHighRiskTableIds,
    pendingSensitiveTableIds,
    pendingRiskTableIds,
  }
}

export function summarizeEvidenceReview(
  assets: EvidenceReviewAssetLike[],
  relations: EvidenceReviewRelationLike[],
  topLevelDepartmentIds: number[],
  sensitiveColumnIds: Set<number>,
): {
  departmentsReviewed: number
  departmentsTotal: number
  remainingRiskItems: number
} {
  const tableAssetById = new Map(
    assets
      .filter(asset => asset.asset_type == null || asset.asset_type === 'table')
      .map(asset => [asset.table_id, asset]),
  )
  const pendingGrantsByDepartment = new Set<number>()

  for (const relation of relations) {
    const pending = !isManuallyApproved(relation.review_status)
    const isGrant = relation.asset_type === 'table'
      ? ['visible', 'partial'].includes(evidenceAccessLevel(relation))
      : evidenceFieldDecision(relation) === 'visible'
    if (pending && isGrant) pendingGrantsByDepartment.add(relation.org_unit_id)
  }

  let remainingRiskItems = assets.filter(asset => (
    (asset.asset_type == null || asset.asset_type === 'table')
    && evidenceBaselineAccess(asset) === 'workspace_visible'
    && evidenceRequiresIndividualReview(asset)
    && !isManuallyApproved(asset.review_status)
  )).length

  remainingRiskItems += relations.filter(relation => {
    if (isManuallyApproved(relation.review_status)) return false
    if (relation.asset_type === 'column') {
      return evidenceFieldDecision(relation) === 'visible'
        && sensitiveColumnIds.has(relation.asset_id)
    }
    return ['visible', 'partial'].includes(evidenceAccessLevel(relation))
      && evidenceRequiresIndividualReview(
        tableAssetById.get(relation.table_id) || {},
      )
  }).length

  return {
    departmentsReviewed: topLevelDepartmentIds.filter(
      id => !pendingGrantsByDepartment.has(id),
    ).length,
    departmentsTotal: topLevelDepartmentIds.length,
    remainingRiskItems,
  }
}

export function accessLevelLabel(level: EvidenceAccessLevel): string {
  return {
    hidden: '不可见',
    visible: '可见',
    partial: '部分可见',
  }[level]
}
