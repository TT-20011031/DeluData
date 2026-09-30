import type {
    ConditionGroup,
    ConditionRule,
    SemanticAccessRowScope,
    SemanticAccessTableRule,
} from '@/types/extendConfig'
import type {
    SemanticAccessPolicySourceSummary,
    SemanticAccessTargetType,
    SemanticEffectiveTableRule,
} from '@/features/semanticAccess/types'

export function bootstrapEffectiveRules(
    effectiveRules: SemanticEffectiveTableRule[] | undefined,
    activeRules: SemanticAccessTableRule[],
): SemanticEffectiveTableRule[] {
    if (effectiveRules !== undefined) return effectiveRules
    return activeRules.map(rule => ({
        ...rule,
        is_direct_override: true,
        source: 'direct',
        sources: [],
        override_seed: { ...rule },
    }))
}

export function persistedSemanticAccessTableRule(
    tableId: number,
    activeRules: SemanticAccessTableRule[],
    effectiveRules: SemanticEffectiveTableRule[],
) {
    const directRule = activeRules.find(item => item.table_id === tableId)
    const effectiveRule = effectiveRules.find(item => item.table_id === tableId)
    return {
        rule: directRule || effectiveRule,
        direct: Boolean(directRule),
        effectiveRule,
    }
}

export function semanticAccessDecisionOriginLabel(
    targetType: SemanticAccessTargetType,
    isDirect: boolean,
    sources: SemanticAccessPolicySourceSummary[] = [],
): string {
    if (isDirect) return '本级决策'
    const labels = [...new Set(sources.map(item => item.label).filter(Boolean))]
    if (labels.length > 0) return `跟随${labels.join('、')}决策`
    return targetType === 'baseline'
        ? '默认拒绝'
        : '跟随上级决策（默认拒绝）'
}

function normalizeValue(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(normalizeValue)
    if (value && typeof value === 'object') {
        return Object.fromEntries(
            Object.entries(value as Record<string, unknown>)
                .filter(([, item]) => item !== undefined)
                .sort(([left], [right]) => left.localeCompare(right))
                .map(([key, item]) => [key, normalizeValue(item)]),
        )
    }
    return value
}

function isConditionGroup(condition: ConditionRule | ConditionGroup): condition is ConditionGroup {
    return 'rules' in condition
}

function normalizeCondition(condition: ConditionRule | ConditionGroup): unknown {
    if (isConditionGroup(condition)) {
        return {
            op: condition.op,
            rules: condition.rules.map(normalizeCondition),
        }
    }
    return {
        column_id: condition.column_id,
        operator: condition.operator,
        value: normalizeValue(condition.value),
        value_source: condition.value_source ?? null,
    }
}

function normalizeRowScope(scope?: SemanticAccessRowScope): unknown {
    if (!scope) return null
    if (scope.type === 'authorization') return { type: 'authorization' }
    if (scope.type === 'all') return { type: 'all' }
    if (scope.type === 'self') {
        return { type: 'self', column_id: scope.column_id, identity: scope.identity }
    }
    if (scope.type === 'department') {
        return {
            type: 'department',
            column_id: scope.column_id,
            include_descendants: scope.include_descendants,
        }
    }
    if (scope.type === 'target_org' || scope.type === 'target_org_tree'
        || scope.type === 'primary_assignment' || scope.type === 'all_assignments') {
        return { type: scope.type }
    }
    if (scope.type === 'custom_org') {
        return {
            type: 'custom_org',
            org_unit_ids: sortedUnique(scope.org_unit_ids),
            include_descendants: scope.include_descendants,
        }
    }
    return { type: 'custom', condition: normalizeCondition(scope.condition) }
}

function sortedUnique(ids: number[]): number[] {
    return [...new Set(ids)].sort((left, right) => left - right)
}

export function semanticAccessPermissionFingerprint(rules: SemanticAccessTableRule[]): string {
    return JSON.stringify(
        rules
            .map(rule => ({
                table_id: rule.table_id,
                decision: rule.decision ?? null,
                hidden_column_ids: sortedUnique(rule.hidden_column_ids),
                hidden_metric_ids: sortedUnique(rule.hidden_metric_ids),
                row_scope: normalizeRowScope(rule.row_scope),
            }))
            .sort((left, right) => left.table_id - right.table_id),
    )
}

export function hasSemanticAccessPermissionChanges(
    currentRules: SemanticAccessTableRule[],
    activeRules: SemanticAccessTableRule[],
): boolean {
    return semanticAccessPermissionFingerprint(currentRules)
        !== semanticAccessPermissionFingerprint(activeRules)
}
