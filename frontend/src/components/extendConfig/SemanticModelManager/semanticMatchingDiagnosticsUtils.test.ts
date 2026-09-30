import { describe, expect, it } from 'vitest'

import type { SemanticMatchingDiagnostic } from '@/types/extendConfig'
import {
    matchingDiagnosticLabel,
    matchingDiagnosticsForTable,
} from './semanticMatchingDiagnosticsUtils'

const diagnostics: SemanticMatchingDiagnostic[] = [{
    type: 'duplicate_term',
    severity: 'warning',
    term: '销售金额',
    objects: [
        { object_type: 'metric', object_id: 1, table_id: 33, business_name: '销售金额', source: 'business_name' },
        { object_type: 'column', object_id: 2, table_id: 27, business_name: '明细金额', source: 'synonym' },
    ],
}]

describe('semantic matching diagnostics', () => {
    it('keeps missing diagnostics backward compatible', () => {
        expect(matchingDiagnosticsForTable(undefined, 33)).toEqual([])
    })

    it('filters conflicts for the table being edited', () => {
        expect(matchingDiagnosticsForTable(diagnostics, 33)).toEqual(diagnostics)
        expect(matchingDiagnosticsForTable(diagnostics, 48)).toEqual([])
        expect(matchingDiagnosticLabel(diagnostics[0])).toBe('跨表重复词')
    })
})
