import type { SemanticMatchingDiagnostic } from '@/types/extendConfig'

export function matchingDiagnosticsForTable(
    diagnostics: SemanticMatchingDiagnostic[] | null | undefined,
    tableId: number | null | undefined,
): SemanticMatchingDiagnostic[] {
    if (!tableId) return []
    return (diagnostics || []).filter(diagnostic =>
        diagnostic.objects.some(object => object.table_id === tableId)
    )
}

export function matchingDiagnosticLabel(diagnostic: SemanticMatchingDiagnostic): string {
    return diagnostic.type === 'generic_term' ? '泛化词' : '跨表重复词'
}
