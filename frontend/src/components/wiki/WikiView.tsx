/**
 * Wiki 视图：左 INDEX + 右详情面板的组合容器。
 *
 * 由 KnowledgeBasePage 的 viewMode='wiki' 时挂载。
 */
import { useEffect, useMemo, useState } from 'react'
import { BookOpen, Building2 } from 'lucide-react'

import { useAuthStore } from '@/stores/authStore'
import { Button } from '@/components/ui/button'
import { knowledgeService } from '@/services/knowledgeService'
import type { DepartmentOption } from '@/types/knowledge'

import { WikiIndexView } from './WikiIndexView'
import { WikiPageDetailView } from './WikiPageDetailView'
import { RetrievalDiagnosticsCard } from './RetrievalDiagnosticsCard'
import { WikiRouteHealthCard } from './WikiRouteHealthCard'
import { WikiCandidateGovernanceCard } from './WikiCandidateGovernanceCard'

export function WikiView({ selectedSlugFromUrl }: { selectedSlugFromUrl?: string | null }) {
    const user = useAuthStore((state) => state.user)
    const isAdmin = Boolean(user?.permissions?.some((code) => code === '*' || code === 'knowledge:manage'))

    const [selectedSlug, setSelectedSlug] = useState<string | null>(null)
    const [scope, setScope] = useState<string>('all')
    const [departmentId, setDepartmentId] = useState<number | undefined>(undefined)
    const [departments, setDepartments] = useState<DepartmentOption[]>([])

    useEffect(() => {
        knowledgeService.fetchDepartments().then(setDepartments).catch(() => setDepartments([]))
    }, [])

    useEffect(() => {
        if (selectedSlugFromUrl) {
            setSelectedSlug(selectedSlugFromUrl)
        }
    }, [selectedSlugFromUrl])

    const effectiveDepartmentId = useMemo(() => {
        if (scope === 'department') return departmentId
        return undefined
    }, [departmentId, scope])

    const handleScopeChange = (nextScope: string) => {
        setScope(nextScope)
        setSelectedSlug(null)
        if (nextScope !== 'department') {
            setDepartmentId(undefined)
        } else if (departmentId === undefined && departments[0]) {
            setDepartmentId(departments[0].id)
        }
    }

    return (
        <div className="flex h-full flex-col bg-manus">
            <div className="border-b border-manus-border bg-manus-secondary px-4 py-2">
                <div className="flex flex-wrap items-center gap-2">
                    <div className="flex items-center gap-1.5 text-sm font-semibold text-manus-text">
                        <Building2 className="h-4 w-4 text-accent" />
                        知识范围
                    </div>
                    <ScopeButton label="全部" active={scope === 'all'} onClick={() => handleScopeChange('all')} />
                    <ScopeButton label="公共知识" active={scope === 'public'} onClick={() => handleScopeChange('public')} />
                    <ScopeButton label="未分类" active={scope === 'unclassified'} onClick={() => handleScopeChange('unclassified')} />
                    <ScopeButton label="跨部门" active={scope === 'cross_department'} onClick={() => handleScopeChange('cross_department')} />
                    {departments.length > 0 && (
                        <select
                            value={scope === 'department' ? String(departmentId ?? '') : ''}
                            onChange={(event) => {
                                const value = Number(event.target.value)
                                setDepartmentId(Number.isFinite(value) ? value : undefined)
                                setScope('department')
                                setSelectedSlug(null)
                            }}
                            className="h-8 rounded-md border border-manus-border bg-manus px-2 text-xs text-manus-text"
                        >
                            <option value="">指定部门</option>
                            {departments.map((dept) => (
                                <option key={dept.id} value={dept.id}>
                                    {dept.name}
                                </option>
                            ))}
                        </select>
                    )}
                </div>
            </div>
            <div className="flex min-h-0 flex-1">
                <div className="w-72 shrink-0 border-r border-manus-border">
                    <WikiIndexView
                        selectedSlug={selectedSlug}
                        onSelectSlug={setSelectedSlug}
                        isAdmin={isAdmin}
                        scope={scope}
                        departmentId={effectiveDepartmentId}
                    />
                </div>
                <div className="flex-1 min-w-0">
                    {selectedSlug ? (
                        <WikiPageDetailView
                            slug={selectedSlug}
                            onSelectSlug={setSelectedSlug}
                            onBack={() => setSelectedSlug(null)}
                            isAdmin={isAdmin}
                        />
                    ) : (
                        <EmptyDetail
                            showHealth={isAdmin}
                            onSelectSlug={setSelectedSlug}
                            scope={scope}
                            departmentId={effectiveDepartmentId}
                        />
                    )}
                </div>
            </div>
        </div>
    )
}

function ScopeButton({
    label,
    active,
    onClick,
}: {
    label: string
    active: boolean
    onClick: () => void
}) {
    return (
        <Button
            type="button"
            variant={active ? 'secondary' : 'outline'}
            size="sm"
            className="h-8"
            onClick={onClick}
        >
            {label}
        </Button>
    )
}

function EmptyDetail({
    showHealth,
    onSelectSlug,
    scope,
    departmentId,
}: {
    showHealth: boolean
    onSelectSlug?: (slug: string) => void
    scope?: string
    departmentId?: number
}) {
    return (
        <div className="h-full overflow-y-auto text-manus-muted text-sm px-6 py-8">
            <div className="mx-auto flex w-full max-w-4xl flex-col items-center gap-3">
                <BookOpen className="h-12 w-12 opacity-30" />
                <div>从左侧选择一个实体页</div>
                <div className="text-[12px] text-manus-muted/70 max-w-md text-center">
                    Wiki 是从高频、重点、已验证知识中沉淀出的语义层。
                    <br />
                    普通文件先进入 RAG 原文证据层，候选页经过治理后再参与 Wiki-First 回答。
                </div>
            </div>
            {showHealth && (
                <div className="mx-auto mt-6 w-full max-w-4xl space-y-4 pb-10">
                    <WikiCandidateGovernanceCard
                        onSelectSlug={onSelectSlug}
                        scope={scope}
                        departmentId={departmentId}
                    />
                    <WikiRouteHealthCard />
                    <RetrievalDiagnosticsCard scope={scope} departmentId={departmentId} />
                </div>
            )}
        </div>
    )
}
