import { describe, expect, it } from 'vitest'
import type { SemanticAccessTableRule } from '@/types/extendConfig'
import {
    bootstrapEffectiveRules,
    hasSemanticAccessPermissionChanges,
    persistedSemanticAccessTableRule,
    semanticAccessDecisionOriginLabel,
    semanticAccessPermissionFingerprint,
} from './semanticAccessPolicyDraftUtils'

const visibleRule: SemanticAccessTableRule = {
    table_id: 1,
    decision: 'visible',
    hidden_column_ids: [5, 2],
    hidden_metric_ids: [8],
    row_scope: { type: 'all' },
}

describe('semantic access permission fingerprint', () => {
    it('keeps the saved decision visible until a staged follow change is saved', () => {
        const inherited = {
            ...visibleRule,
            decision: 'hidden' as const,
            is_direct_override: false,
            source: 'org_unit' as const,
            sources: [{
                binding_id: 8,
                target_type: 'org_unit' as const,
                target_id: '22',
                label: '生产部',
            }],
            override_seed: { ...visibleRule, decision: 'hidden' as const },
        }

        expect(persistedSemanticAccessTableRule(1, [visibleRule], [inherited])).toEqual({
            rule: visibleRule,
            direct: true,
            effectiveRule: inherited,
        })
        expect(persistedSemanticAccessTableRule(1, [], [inherited])).toEqual({
            rule: inherited,
            direct: false,
            effectiveRule: inherited,
        })
    })

    it('uses inherited effective rules while the first-run draft remains open', () => {
        const inherited = [{
            ...visibleRule,
            is_direct_override: false,
            source: 'org_unit' as const,
            sources: [{
                binding_id: 8,
                target_type: 'org_unit' as const,
                target_id: '22',
                label: '生产部',
            }],
            override_seed: visibleRule,
        }]

        expect(bootstrapEffectiveRules(inherited, [])).toEqual(inherited)
    })

    it('labels direct and inherited decisions without changing effective visibility', () => {
        expect(semanticAccessDecisionOriginLabel('position', true, [])).toBe('本级决策')
        expect(semanticAccessDecisionOriginLabel('position', false, [{
            binding_id: 8,
            target_type: 'org_unit',
            target_id: '22',
            label: '生产部',
        }])).toBe('跟随生产部决策')
        expect(semanticAccessDecisionOriginLabel('user', false, [])).toBe('跟随上级决策（默认拒绝）')
    })

    it('treats table and hidden asset ordering as equivalent', () => {
        const left = [visibleRule, {
            table_id: 2,
            decision: 'hidden' as const,
            hidden_column_ids: [],
            hidden_metric_ids: [],
        }]
        const right = [{
            table_id: 2,
            decision: 'hidden' as const,
            hidden_column_ids: [],
            hidden_metric_ids: [],
        }, { ...visibleRule, hidden_column_ids: [2, 5, 2] }]

        expect(semanticAccessPermissionFingerprint(left)).toBe(semanticAccessPermissionFingerprint(right))
        expect(hasSemanticAccessPermissionChanges(left, right)).toBe(false)
    })

    it('detects a table decision change', () => {
        expect(hasSemanticAccessPermissionChanges(
            [visibleRule],
            [{ ...visibleRule, decision: 'hidden', row_scope: undefined }],
        )).toBe(true)
    })

    it('detects a hidden field or metric change', () => {
        expect(hasSemanticAccessPermissionChanges(
            [{ ...visibleRule, hidden_column_ids: [2, 5, 9] }],
            [visibleRule],
        )).toBe(true)
        expect(hasSemanticAccessPermissionChanges(
            [{ ...visibleRule, hidden_metric_ids: [] }],
            [visibleRule],
        )).toBe(true)
    })

    it('detects row scope changes, including custom conditions', () => {
        expect(hasSemanticAccessPermissionChanges(
            [{ ...visibleRule, row_scope: { type: 'self', column_id: 5, identity: 'username' } }],
            [visibleRule],
        )).toBe(true)
        expect(hasSemanticAccessPermissionChanges(
            [{
                ...visibleRule,
                row_scope: {
                    type: 'custom',
                    condition: { op: 'AND', rules: [{ column_id: 5, operator: '=', value: 'test1' }] },
                },
            }],
            [visibleRule],
        )).toBe(true)
    })

    it('treats authorization scope as a stable first-class scope', () => {
        const authorizationRule = { ...visibleRule, row_scope: { type: 'authorization' as const } }
        expect(hasSemanticAccessPermissionChanges([authorizationRule], [authorizationRule])).toBe(false)
        expect(hasSemanticAccessPermissionChanges([authorizationRule], [visibleRule])).toBe(true)
    })

    it('marks an applied natural-language table patch as changed', () => {
        const suggestedRules: SemanticAccessTableRule[] = [{
            table_id: 3,
            decision: 'visible',
            hidden_column_ids: [11],
            hidden_metric_ids: [],
            row_scope: { type: 'department', column_id: 12, include_descendants: true },
        }]

        expect(hasSemanticAccessPermissionChanges(suggestedRules, [])).toBe(true)
    })

    it('normalizes custom organization scope ordering', () => {
        const left: SemanticAccessTableRule[] = [{
            ...visibleRule,
            row_scope: {
                type: 'custom_org',
                org_unit_ids: [9, 3, 9],
                include_descendants: true,
            },
        }]
        const right: SemanticAccessTableRule[] = [{
            ...visibleRule,
            row_scope: {
                type: 'custom_org',
                org_unit_ids: [3, 9],
                include_descendants: true,
            },
        }]

        expect(hasSemanticAccessPermissionChanges(left, right)).toBe(false)
    })

    it('distinguishes account assignment scopes', () => {
        const primary: SemanticAccessTableRule[] = [{
            ...visibleRule,
            row_scope: { type: 'primary_assignment' },
        }]
        const allAssignments: SemanticAccessTableRule[] = [{
            ...visibleRule,
            row_scope: { type: 'all_assignments' },
        }]

        expect(hasSemanticAccessPermissionChanges(primary, allAssignments)).toBe(true)
    })
})
