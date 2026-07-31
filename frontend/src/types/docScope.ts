/**
 * Shared knowledge scope used by chat, skills, RAG retrieval, and Wiki routing.
 *
 * `domains` and `wiki_slugs` are kept for backward compatibility with older
 * saved skill steps, but the normal chat UI no longer exposes a separate Wiki
 * range. Department and visibility filters should constrain RAG and Wiki
 * together.
 */

export interface DocScope {
    folder_ids: string[]
    file_ids: string[]
    include_subfolders: boolean
    visibilities: string[]
    dept_ids: string[]
    scope?: string
    department_id?: string | number | null
    business_domain?: string
    document_type?: string
    confidentiality_level?: string
    domains: string[]
    wiki_slugs: string[]
}

export const DEFAULT_DOC_SCOPE: DocScope = {
    folder_ids: [],
    file_ids: [],
    include_subfolders: true,
    visibilities: [],
    dept_ids: [],
    domains: [],
    wiki_slugs: [],
}

function toIdList(input: unknown): string[] {
    if (input == null) return []
    const raw = Array.isArray(input) ? input : [input]
    const seen = new Set<string>()
    const result: string[] = []
    for (const item of raw) {
        const text = String(item ?? '').trim()
        if (!text || seen.has(text)) continue
        seen.add(text)
        result.push(text)
    }
    return result
}

function optionalText(input: unknown): string | undefined {
    const text = String(input ?? '').trim()
    return text || undefined
}

export function normalizeDocScope(input: unknown): DocScope {
    if (!input || typeof input !== 'object') {
        return { ...DEFAULT_DOC_SCOPE, domains: [], wiki_slugs: [] }
    }
    const raw = input as Record<string, unknown>
    const departmentId = raw.department_id
    return {
        folder_ids: toIdList(raw.folder_ids),
        file_ids: toIdList(raw.file_ids),
        include_subfolders: raw.include_subfolders !== false,
        visibilities: toIdList(raw.visibilities),
        dept_ids: toIdList(raw.dept_ids),
        scope: optionalText(raw.scope),
        department_id: typeof departmentId === 'string' || typeof departmentId === 'number'
            ? departmentId
            : undefined,
        business_domain: optionalText(raw.business_domain),
        document_type: optionalText(raw.document_type),
        confidentiality_level: optionalText(raw.confidentiality_level),
        domains: toIdList(raw.domains),
        wiki_slugs: toIdList(raw.wiki_slugs),
    }
}

export function isDocScopeEmpty(scope: DocScope | null | undefined): boolean {
    if (!scope) return true
    return (
        scope.folder_ids.length === 0
        && scope.file_ids.length === 0
        && scope.visibilities.length === 0
        && scope.dept_ids.length === 0
        && !scope.scope
        && !scope.department_id
        && !scope.business_domain
        && !scope.document_type
        && !scope.confidentiality_level
        && scope.domains.length === 0
        && scope.wiki_slugs.length === 0
    )
}

export function compactDocScope(scope: unknown): DocScope | null {
    const normalized = normalizeDocScope(scope)
    if (isDocScopeEmpty(normalized)) return null
    return normalized
}

function eqSorted(left: string[], right: string[]): boolean {
    if (left.length !== right.length) return false
    const a = [...left].sort()
    const b = [...right].sort()
    return a.every((item, index) => item === b[index])
}

export function compareDocScope(a: unknown, b: unknown): boolean {
    const left = normalizeDocScope(a)
    const right = normalizeDocScope(b)
    if (left.include_subfolders !== right.include_subfolders) {
        return false
    }
    return (
        eqSorted(left.folder_ids, right.folder_ids)
        && eqSorted(left.file_ids, right.file_ids)
        && eqSorted(left.visibilities, right.visibilities)
        && eqSorted(left.dept_ids, right.dept_ids)
        && (left.scope || '') === (right.scope || '')
        && String(left.department_id || '') === String(right.department_id || '')
        && (left.business_domain || '') === (right.business_domain || '')
        && (left.document_type || '') === (right.document_type || '')
        && (left.confidentiality_level || '') === (right.confidentiality_level || '')
        && eqSorted(left.domains, right.domains)
        && eqSorted(left.wiki_slugs, right.wiki_slugs)
    )
}

export function summarizeDocScope(scope: unknown): string {
    const normalized = normalizeDocScope(scope)
    const folderCount = normalized.folder_ids.length
    const fileCount = normalized.file_ids.length
    const visibilityCount = normalized.visibilities.length
    const deptCount = normalized.dept_ids.length

    if (
        folderCount === 0
        && fileCount === 0
        && visibilityCount === 0
        && deptCount === 0
        && !normalized.scope
        && !normalized.department_id
        && !normalized.business_domain
        && !normalized.document_type
        && !normalized.confidentiality_level
        && normalized.domains.length === 0
        && normalized.wiki_slugs.length === 0
    ) {
        return '全知识库（RAG + Wiki）'
    }

    const parts: string[] = []
    if (visibilityCount > 0) parts.push(`${visibilityCount} 个可见范围`)
    if (deptCount > 0) parts.push(`${deptCount} 个部门`)
    if (normalized.business_domain) parts.push(`业务域 ${normalized.business_domain}`)
    if (normalized.document_type) parts.push(`文档类型 ${normalized.document_type}`)
    if (normalized.confidentiality_level) parts.push(`密级 ${normalized.confidentiality_level}`)
    if (folderCount > 0) parts.push(`${folderCount} 个文件夹`)
    if (fileCount > 0) parts.push(`${fileCount} 个文件`)

    const includeSubLabel = normalized.include_subfolders ? '已含子目录' : '仅当前目录'
    return `${parts.join(' / ')} · ${includeSubLabel} · RAG + Wiki`
}
