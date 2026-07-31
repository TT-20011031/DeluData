import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
    Archive,
    AlertTriangle,
    FileWarning,
    Loader2,
    RefreshCw,
    ShieldCheck,
    Star,
    StarOff,
    Upload,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { wikiService } from '@/services/wikiService'
import type { WikiGovernanceAction, WikiPageSummary } from '@/types/wiki'

const PAGE_SIZE = 6

const RISK_LABELS: Record<string, string> = {
    source_missing: '无来源',
    low_source_count: '单来源',
    active_conflict: '冲突',
    duplicate_entity: '重复',
    source_deleted: '来源已删',
    source_expired: '来源过期',
    content_truncated: '内容截断',
    restricted_source_scope: '受限来源',
    merged_candidate: '已合并',
}

const BLOCKER_LABELS: Record<string, string> = {
    source_missing: '缺少来源',
    active_conflict: '存在冲突',
    duplicate_entity: '重复实体',
    restricted_source_scope: '来源权限受限',
    source_expired: '来源过期',
    merged_candidate: '已合并',
}

export function WikiCandidateGovernanceCard({
    onSelectSlug,
    scope,
    departmentId,
    businessDomain,
    documentType,
    confidentialityLevel,
}: {
    onSelectSlug?: (slug: string) => void
    scope?: string
    departmentId?: number
    businessDomain?: string
    documentType?: string
    confidentialityLevel?: string
}) {
    const [items, setItems] = useState<Array<WikiPageSummary & { governance: NonNullable<WikiPageSummary['governance']> }>>([])
    const [keyword, setKeyword] = useState('')
    const [loading, setLoading] = useState(false)
    const [actingSlug, setActingSlug] = useState<string | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [page, setPage] = useState(1)
    const [localBusinessDomain, setLocalBusinessDomain] = useState('')
    const [localDocumentType, setLocalDocumentType] = useState('')
    const [localConfidentiality, setLocalConfidentiality] = useState('')

    const counts = useMemo(() => {
        return items.reduce(
            (acc, item) => {
                acc[item.governance.priority] = (acc[item.governance.priority] || 0) + 1
                return acc
            },
            {} as Record<string, number>,
        )
    }, [items])
    const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))
    const currentPage = Math.min(page, totalPages)
    const pagedItems = items.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

    const load = async () => {
        setLoading(true)
        setError(null)
        try {
            const data = await wikiService.listCandidateGovernance({
                keyword: keyword.trim() || undefined,
                status: 'candidate,draft',
                scope,
                department_id: departmentId,
                business_domain: businessDomain || localBusinessDomain.trim() || undefined,
                document_type: documentType || localDocumentType.trim() || undefined,
                confidentiality_level: confidentialityLevel || localConfidentiality.trim() || undefined,
                limit: 200,
            })
            setItems(data.items)
            setPage(1)
        } catch (err) {
            setError(err instanceof Error ? err.message : 'failed_to_load_governance')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        load()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope, departmentId, businessDomain, documentType, confidentialityLevel])

    const act = async (slug: string, action: WikiGovernanceAction) => {
        setActingSlug(slug)
        setError(null)
        try {
            await wikiService.applyGovernanceAction(slug, {
                action,
                commit_message: `governance:${action}`,
            })
            await load()
        } catch (err) {
            setError(err instanceof Error ? err.message : 'governance_action_failed')
        } finally {
            setActingSlug(null)
        }
    }

    return (
        <div className="w-full max-w-4xl mx-auto rounded-lg border border-manus-border bg-manus-secondary p-4 space-y-3">
            <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4 text-accent" />
                    <span className="text-sm font-semibold text-manus-text">Wiki 候选治理台</span>
                    <span className="rounded bg-manus px-1.5 py-0.5 text-xs text-manus-muted">
                        {items.length}
                    </span>
                </div>
                <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
                    <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
                </Button>
            </div>

            <div className="grid grid-cols-3 gap-2">
                <Metric label="高优先级" value={String(counts.high || 0)} />
                <Metric label="中优先级" value={String(counts.medium || 0)} />
                <Metric label="低优先级" value={String(counts.low || 0)} />
            </div>

            <div className="flex gap-2">
                <Input
                    value={keyword}
                    onChange={(event) => setKeyword(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === 'Enter') load()
                    }}
                    placeholder="搜索候选标题、slug、摘要"
                    className="h-9"
                />
                <Button onClick={load} disabled={loading} className="shrink-0">
                    {loading ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : null}
                    查询
                </Button>
            </div>

            <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                <Input
                    value={localBusinessDomain}
                    onChange={(event) => setLocalBusinessDomain(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === 'Enter') load()
                    }}
                    placeholder="业务域"
                    className="h-8 text-xs"
                />
                <Input
                    value={localDocumentType}
                    onChange={(event) => setLocalDocumentType(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === 'Enter') load()
                    }}
                    placeholder="文档类型"
                    className="h-8 text-xs"
                />
                <Input
                    value={localConfidentiality}
                    onChange={(event) => setLocalConfidentiality(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === 'Enter') load()
                    }}
                    placeholder="密级"
                    className="h-8 text-xs"
                />
            </div>

            {error && (
                <div className="flex items-center gap-2 text-xs text-rose-500">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    {error}
                </div>
            )}

            <div className="space-y-2">
                {loading && items.length === 0 ? (
                    <div className="rounded-md border border-manus-border bg-manus p-3 text-xs text-manus-muted">
                        正在加载候选治理信息
                    </div>
                ) : items.length === 0 ? (
                    <div className="rounded-md border border-manus-border bg-manus p-3 text-xs text-manus-muted">
                        暂无待治理候选
                    </div>
                ) : (
                    pagedItems.map((item) => (
                        <CandidateRow
                            key={item.id}
                            item={item}
                            acting={actingSlug === item.slug}
                            onOpen={() => onSelectSlug?.(item.slug)}
                            onAction={(action) => act(item.slug, action)}
                        />
                    ))
                )}
            </div>

            {items.length > PAGE_SIZE && (
                <div className="flex items-center justify-between gap-3 border-t border-manus-border pt-3 text-xs text-manus-muted">
                    <span>
                        第 {currentPage} / {totalPages} 页 · 共 {items.length} 个候选
                    </span>
                    <div className="flex shrink-0 gap-1">
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={currentPage <= 1}
                            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
                        >
                            上一页
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={currentPage >= totalPages}
                            onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
                        >
                            下一页
                        </Button>
                    </div>
                </div>
            )}
        </div>
    )
}

function CandidateRow({
    item,
    acting,
    onOpen,
    onAction,
}: {
    item: WikiPageSummary & { governance: NonNullable<WikiPageSummary['governance']> }
    acting: boolean
    onOpen: () => void
    onAction: (action: WikiGovernanceAction) => void
}) {
    const g = item.governance
    const signals = g.signals || {}
    const isBoosted = Boolean(signals.manual_boost)
    return (
        <div className="rounded-md border border-manus-border bg-manus p-3 text-xs space-y-2">
            <div className="flex items-start justify-between gap-3">
                <button type="button" onClick={onOpen} className="min-w-0 text-left">
                    <div className="flex items-center gap-2">
                        <span className="font-medium text-manus-text truncate">{item.title}</span>
                        <PriorityBadge priority={g.priority} score={g.score} />
                    </div>
                    <div className="mt-0.5 text-manus-muted truncate">
                        {item.slug} · {item.domain} · {item.status}
                    </div>
                </button>
                <div className="flex shrink-0 gap-1">
                    <IconAction
                        title={isBoosted ? '取消提权' : '人工提权'}
                        disabled={acting}
                        onClick={() => onAction(isBoosted ? 'unboost' : 'boost')}
                        icon={isBoosted ? <StarOff className="h-3.5 w-3.5" /> : <Star className="h-3.5 w-3.5" />}
                    />
                    <IconAction
                        title="发布"
                        disabled={acting || !g.can_publish}
                        onClick={() => onAction('publish')}
                        icon={<Upload className="h-3.5 w-3.5" />}
                    />
                    <IconAction
                        title="退回草稿"
                        disabled={acting}
                        onClick={() => onAction('draft')}
                        icon={<FileWarning className="h-3.5 w-3.5" />}
                    />
                    <IconAction
                        title="归档"
                        disabled={acting}
                        onClick={() => onAction('archive')}
                        icon={<Archive className="h-3.5 w-3.5" />}
                    />
                </div>
            </div>

            <div className="flex flex-wrap gap-x-3 gap-y-1 text-manus-muted">
                <span>查询 {String(signals.query_frequency_30d || 0)}</span>
                <span>Gap {String(signals.wiki_gap_30d || 0)}</span>
                <span>来源 {String(signals.source_count || 0)}</span>
                <span>切片 {String(signals.chunk_count || 0)}</span>
                <span>触发 {String(signals.trigger_type || '-')}</span>
            </div>

            {(g.risks.length > 0 || g.publish_blockers.length > 0) && (
                <div className="flex flex-wrap gap-1">
                    {g.risks.map((risk) => (
                        <span key={risk} className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-600">
                            {RISK_LABELS[risk] || risk}
                        </span>
                    ))}
                    {g.publish_blockers.map((blocker) => (
                        <span key={blocker} className="rounded bg-rose-500/10 px-1.5 py-0.5 text-rose-600">
                            {BLOCKER_LABELS[blocker] || blocker}
                        </span>
                    ))}
                </div>
            )}
        </div>
    )
}

function IconAction({
    title,
    icon,
    disabled,
    onClick,
}: {
    title: string
    icon: ReactNode
    disabled?: boolean
    onClick: () => void
}) {
    return (
        <button
            type="button"
            title={title}
            disabled={disabled}
            onClick={onClick}
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-manus-border text-manus-muted hover:bg-manus-hover hover:text-manus-text disabled:cursor-not-allowed disabled:opacity-40"
        >
            {icon}
        </button>
    )
}

function PriorityBadge({ priority, score }: { priority: string; score: number }) {
    return (
        <span
            className={cn(
                'rounded px-1.5 py-0.5 text-[10px] uppercase',
                priority === 'high' && 'bg-emerald-500/10 text-emerald-600',
                priority === 'medium' && 'bg-amber-500/10 text-amber-600',
                priority === 'low' && 'bg-manus-secondary text-manus-muted',
            )}
        >
            {priority} {score}
        </span>
    )
}

function Metric({ label, value }: { label: string; value: string }) {
    return (
        <div className="rounded-md border border-manus-border bg-manus p-2">
            <div className="text-[10px] text-manus-muted">{label}</div>
            <div className="mt-0.5 text-base font-semibold tabular-nums text-manus-text">{value}</div>
        </div>
    )
}
