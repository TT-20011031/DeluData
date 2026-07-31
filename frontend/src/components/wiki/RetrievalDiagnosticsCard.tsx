import { useCallback, useEffect, useMemo, useState } from 'react'
import {
    AlertTriangle,
    ChevronDown,
    ChevronUp,
    FileText,
    Loader2,
    Route,
    SearchCheck,
    Shield,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { wikiService, type ConversationDiagnosticItem } from '@/services/wikiService'
import type {
    KnowledgePath,
    RetrievalDiagnosticHit,
    RetrievalDiagnosticsResponse,
} from '@/types/wiki'

const PATH_OPTIONS: Array<{ value: KnowledgePath | 'auto'; label: string }> = [
    { value: 'auto', label: 'Auto' },
    { value: 'rag', label: 'RAG' },
    { value: 'wiki', label: 'Wiki' },
    { value: 'both', label: 'Both' },
]

const REASON_LABELS: Record<string, string> = {
    trusted_wiki_context: '正式 Wiki 上下文',
    kept_for_context: '进入候选上下文',
    final_context: '最终上下文',
    filtered_by_rerank_threshold: '低于 rerank 阈值',
    explicit_request_path: '手动指定路径',
    fallback_to_rag_evidence: '默认回退 RAG',
    explicit_wiki_scope: '显式 Wiki 范围',
}

const ISSUE_LABELS: Record<string, string> = {
    wiki_missing: '正式 Wiki 未命中',
    candidate_exists_but_unpublished: '存在未发布候选',
    rag_no_candidates: 'RAG 未召回',
    rerank_dropped: 'Rerank 全部丢弃',
    permission_filtered: '权限过滤',
    metadata_scope_mismatch: '范围/元数据不匹配',
    wiki_conflict: 'Wiki 冲突',
    source_expired: '来源过期',
    no_final_context: '无最终上下文',
    answer_generation_risk: '生成风险',
    route_mismatch: '路由不一致',
}

export function RetrievalDiagnosticsCard({
    scope,
    departmentId,
    businessDomain,
    documentType,
    confidentialityLevel,
}: {
    scope?: string
    departmentId?: number
    businessDomain?: string
    documentType?: string
    confidentialityLevel?: string
}) {
    const [query, setQuery] = useState('')
    const [path, setPath] = useState<KnowledgePath | 'auto'>('auto')
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [result, setResult] = useState<RetrievalDiagnosticsResponse | null>(null)
    const [expanded, setExpanded] = useState<Set<string>>(new Set())
    const [conversationItems, setConversationItems] = useState<ConversationDiagnosticItem[]>([])
    const [conversationLoading, setConversationLoading] = useState(false)

    const keptHits = useMemo(
        () => (result?.hits || []).filter((hit) => hit.kept).length,
        [result],
    )

    const run = async () => {
        const text = query.trim()
        if (!text || loading) return
        setLoading(true)
        setError(null)
        setExpanded(new Set())
        try {
            const hasScope =
                !!scope && scope !== 'all' ||
                departmentId !== undefined ||
                !!businessDomain ||
                !!documentType ||
                !!confidentialityLevel
            const data = await wikiService.diagnoseRetrieval({
                query: text,
                knowledge_path: path === 'auto' ? undefined : path,
                doc_scope: hasScope
                    ? {
                        scope,
                        department_id: departmentId,
                        business_domain: businessDomain || undefined,
                        document_type: documentType || undefined,
                        confidentiality_level: confidentialityLevel || undefined,
                    }
                    : undefined,
                top_k: 8,
            })
            setResult(data)
            loadConversationDiagnostics()
        } catch (err) {
            setError(err instanceof Error ? err.message : 'diagnostics_failed')
        } finally {
            setLoading(false)
        }
    }

    const loadConversationDiagnostics = useCallback(async () => {
        setConversationLoading(true)
        try {
            const data = await wikiService.listConversationDiagnostics({ limit: 10 })
            setConversationItems(data.items || [])
        } catch {
            setConversationItems([])
        } finally {
            setConversationLoading(false)
        }
    }, [])

    useEffect(() => {
        loadConversationDiagnostics()
    }, [loadConversationDiagnostics])

    const toggleExpanded = (id: string) => {
        setExpanded((prev) => {
            const next = new Set(prev)
            if (next.has(id)) {
                next.delete(id)
            } else {
                next.add(id)
            }
            return next
        })
    }

    return (
        <div className="w-full max-w-4xl mx-auto rounded-lg border border-manus-border bg-manus-secondary p-4 space-y-3">
            <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                    <SearchCheck className="h-4 w-4 text-accent" />
                    <span className="text-sm font-semibold text-manus-text">召回诊断台</span>
                </div>
                <div className="flex rounded-md border border-manus-border p-0.5">
                    {PATH_OPTIONS.map((opt) => (
                        <button
                            key={opt.value}
                            type="button"
                            onClick={() => setPath(opt.value)}
                            className={cn(
                                'px-2 py-0.5 text-[11px] rounded transition-colors',
                                path === opt.value
                                    ? 'bg-accent/15 text-accent'
                                    : 'text-manus-subtle hover:text-manus-text',
                            )}
                        >
                            {opt.label}
                        </button>
                    ))}
                </div>
            </div>

            <div className="flex gap-2">
                <Input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === 'Enter') run()
                    }}
                    placeholder="输入一个知识库问题"
                    className="h-9"
                />
                <Button onClick={run} disabled={loading || !query.trim()} className="shrink-0">
                    {loading ? (
                        <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                    ) : (
                        <Route className="h-4 w-4 mr-1" />
                    )}
                    诊断
                </Button>
            </div>

            {error && (
                <div className="flex items-center gap-2 text-xs text-rose-500">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    {error}
                </div>
            )}

            {result && (
                <div className="space-y-3">
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                        <Metric label="路径" value={String(result.knowledge_path).toUpperCase()} />
                        <Metric label="命中" value={String(result.hits.length)} />
                        <Metric label="保留" value={String(keptHits)} />
                        <Metric label="上下文" value={String(result.final_context.length)} />
                    </div>

                    <div className="rounded-md border border-manus-border bg-manus p-2.5 text-xs text-manus-muted space-y-1">
                        <div className="flex items-center gap-1.5 text-manus-text font-medium">
                            <Shield className="h-3.5 w-3.5" />
                            权限与路由
                        </div>
                        <div>
                            {result.knowledge_path_source}:{' '}
                            {REASON_LABELS[result.knowledge_path_reason] ||
                                result.knowledge_path_reason}
                        </div>
                        <div>
                            workspace {String(result.permissions.workspace_id || '-')} · data_scope{' '}
                            {String(result.permissions.data_scope || '-')} · filter{' '}
                            {String(result.permissions.permission_filter_applied)}
                        </div>
                        {result.route_trace && (
                            <div>
                                inferred {String(result.route_trace.inferred_path || '-')} · final{' '}
                                {String(result.route_trace.final_path || '-')} · source{' '}
                                {String(result.route_trace.inferred_source || '-')}
                            </div>
                        )}
                        {result.metadata_filter_summary && (
                            <div>
                                scope {String(result.metadata_filter_summary.scope || scope || 'all')} · dept{' '}
                                {String(result.metadata_filter_summary.department_id || departmentId || '-')} · metadata mismatch{' '}
                                {String(result.metadata_filter_summary.metadata_scope_mismatch || false)}
                            </div>
                        )}
                        {Object.keys(result.counts || {}).length > 0 && (
                            <div className="flex flex-wrap gap-x-3 gap-y-1">
                                {Object.entries(result.counts).map(([key, value]) => (
                                    <span key={key}>
                                        {key}: {value}
                                    </span>
                                ))}
                            </div>
                        )}
                        {result.issues.length > 0 && (
                            <div className="flex flex-wrap gap-1">
                                {result.issues.map((item) => (
                                    <span key={item} className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-600">
                                        {ISSUE_LABELS[item] || item}
                                    </span>
                                ))}
                            </div>
                        )}
                    </div>

                    <DiagnosticSection
                        title="最终上下文"
                        empty="没有证据进入最终上下文"
                        hits={result.final_context}
                        expanded={expanded}
                        onToggle={toggleExpanded}
                        namespace="final"
                    />

                    <DiagnosticSection
                        title="全部命中"
                        empty="没有命中结果"
                        hits={result.hits}
                        expanded={expanded}
                        onToggle={toggleExpanded}
                        namespace="hit"
                    />
                </div>
            )}

            <ConversationContextSection
                items={conversationItems}
                loading={conversationLoading}
                onRefresh={loadConversationDiagnostics}
            />
        </div>
    )
}

function ConversationContextSection({
    items,
    loading,
    onRefresh,
}: {
    items: ConversationDiagnosticItem[]
    loading: boolean
    onRefresh: () => void
}) {
    return (
        <div className="space-y-2 rounded-md border border-manus-border bg-manus p-3">
            <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-manus-text">
                    <Route className="h-3.5 w-3.5 text-accent" />
                    主对话最终上下文
                </div>
                <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={onRefresh} disabled={loading}>
                    {loading ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : null}
                    刷新
                </Button>
            </div>
            {items.length === 0 ? (
                <div className="text-xs text-manus-muted">
                    暂无主对话诊断记录。发起一次知识库问答后会显示真实回答使用的上下文。
                </div>
            ) : (
                <div className="space-y-2">
                    {items.map((item) => (
                        <div key={item.id} className="rounded border border-manus-border bg-manus-secondary p-2 text-xs">
                            <div className="flex items-center justify-between gap-2">
                                <span className="font-medium text-manus-text truncate">
                                    {item.user_query || item.message_id || item.id}
                                </span>
                                <span className="shrink-0 text-[10px] uppercase text-manus-muted">
                                    {item.knowledge_path} · {item.answer_context_source}
                                </span>
                            </div>
                            <div className="mt-1 text-[11px] text-manus-muted">
                                Wiki {item.wiki_chunks_used}/{item.wiki_chunks_count} · RAG {item.rag_chunks_used}
                                {item.wiki_fallback_to_rag ? ' · fallback RAG' : ''}
                                {item.wrong_tool_repair ? ' · wrong tool repair' : ''}
                            </div>
                            {item.issues.length > 0 && (
                                <div className="mt-1 flex flex-wrap gap-1">
                                    {item.issues.slice(0, 6).map((issue) => (
                                        <span key={issue} className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-600">
                                            {ISSUE_LABELS[issue] || issue}
                                        </span>
                                    ))}
                                </div>
                            )}
                            {item.final_context.length > 0 && (
                                <div className="mt-2 space-y-1">
                                    {item.final_context.slice(0, 3).map((ctx, idx) => (
                                        <div key={`${item.id}-${idx}`} className="rounded bg-white/60 p-1.5 text-[11px] text-manus-subtle">
                                            <span className="font-medium uppercase">{ctx.source || 'ctx'}</span>
                                            {' · '}
                                            {ctx.title || ctx.file_name || ctx.slug || '未命名证据'}
                                            {ctx.page_number ? ` · p.${ctx.page_number}` : ''}
                                            {ctx.preview ? `：${ctx.preview}` : ''}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

function DiagnosticSection({
    title,
    empty,
    hits,
    expanded,
    onToggle,
    namespace,
}: {
    title: string
    empty: string
    hits: RetrievalDiagnosticHit[]
    expanded: Set<string>
    onToggle: (id: string) => void
    namespace: string
}) {
    return (
        <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs text-manus-muted">
                <span className="font-medium text-manus-text">{title}</span>
                <span>{hits.length} 条</span>
            </div>
            {hits.length === 0 ? (
                <div className="rounded-md border border-manus-border bg-manus p-3 text-xs text-manus-muted">
                    {empty}
                </div>
            ) : (
                hits.map((hit, index) => {
                    const id = `${namespace}-${hit.source}-${hit.chunk_id || index}`
                    return (
                        <DiagnosticHitCard
                            key={id}
                            hit={hit}
                            expanded={expanded.has(id)}
                            onToggle={() => onToggle(id)}
                        />
                    )
                })
            )}
        </div>
    )
}

function DiagnosticHitCard({
    hit,
    expanded,
    onToggle,
}: {
    hit: RetrievalDiagnosticHit
    expanded: boolean
    onToggle: () => void
}) {
    const content = hit.content || hit.preview || ''
    const hasLongContent = content.length > 160 || content.includes('\n')
    return (
        <div
            className={cn(
                'rounded-md border p-2 text-xs',
                hit.kept
                    ? 'border-emerald-500/30 bg-emerald-500/5'
                    : 'border-manus-border bg-manus',
            )}
        >
            <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 min-w-0">
                    <FileText className="h-3.5 w-3.5 text-manus-muted shrink-0" />
                    <span className="font-medium text-manus-text truncate">
                        {hit.title || hit.file_name || hit.chunk_id || hit.source}
                    </span>
                </div>
                <span className="shrink-0 text-[10px] uppercase text-manus-muted">
                    {hit.source === 'wiki_candidate' ? 'candidate' : hit.source} · {hit.kept ? 'kept' : ISSUE_LABELS[hit.reason || ''] || hit.reason || 'dropped'}
                </span>
            </div>
            <div className="mt-1 text-[11px] text-manus-muted">
                score {hit.score.toFixed(3)}
                {hit.rerank_score != null ? ` · rerank ${hit.rerank_score.toFixed(3)}` : ''}
                {hit.page_number ? ` · p.${hit.page_number}` : ''}
                {hit.domain ? ` · ${hit.domain}` : ''}
                {hit.status ? ` · ${hit.status}` : ''}
            </div>
            {content && (
                <pre
                    className={cn(
                        'mt-2 whitespace-pre-wrap break-words rounded bg-white/60 p-2 leading-relaxed text-manus-subtle',
                        !expanded && 'max-h-20 overflow-hidden',
                    )}
                >
                    {content}
                </pre>
            )}
            {hasLongContent && (
                <button
                    type="button"
                    onClick={onToggle}
                    className="mt-1 inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
                >
                    {expanded ? (
                        <>
                            <ChevronUp className="h-3 w-3" />
                            收起
                        </>
                    ) : (
                        <>
                            <ChevronDown className="h-3 w-3" />
                            展开完整内容
                        </>
                    )}
                </button>
            )}
        </div>
    )
}

function Metric({ label, value }: { label: string; value: string }) {
    return (
        <div className="rounded-md border border-manus-border bg-manus p-2">
            <div className="text-[10px] text-manus-muted">{label}</div>
            <div className="mt-0.5 text-base font-semibold tabular-nums text-manus-text truncate">
                {value}
            </div>
        </div>
    )
}
