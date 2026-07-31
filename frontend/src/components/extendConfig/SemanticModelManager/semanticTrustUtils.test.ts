import { describe, expect, it } from 'vitest'
import { diffRisk, readinessGuidance, readinessLabel } from './semanticTrustUtils'

describe('semantic trust presentation rules', () => {
    it('uses explicit readiness language', () => {
        expect(readinessLabel('not_scanned')).toBe('未建立基线')
        expect(readinessLabel('ready')).toBe('可信就绪')
    })

    it('never labels a blocking schema change as safe', () => {
        expect(diffRisk({
            object_type: 'column',
            change_type: 'modified',
            physical_identity: 'orders.amount',
            before: { type: 'int' },
            after: { type: 'decimal' },
            severity: 'critical',
            blocking: true,
        })).toBe('critical')
    })

    it('turns partial readiness into an actionable governance instruction', () => {
        expect(readinessGuidance({
            status: 'partially_ready',
            runtime_mode: 'shadow',
            structure: {},
            semantics: {
                queryable_tables: 18,
                queryable_columns: 146,
                confirmed_metrics: 12,
                confirmed_relationships: 9,
                stale_assets: 3,
            },
            security: { readonly_configured: true, blocking: false },
            governance: {
                last_run: null,
                evidence_freshness: 'missing',
                profile_coverage: 0,
                pending_candidates: 0,
                high_risk_candidates: 0,
                recent_auto_applied: 0,
            },
            blockers: [],
        })).toBe('3 个资产待复核；当前仅已治理范围可问数。')
    })
})
