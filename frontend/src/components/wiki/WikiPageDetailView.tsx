import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
    Archive,
    ArrowLeft,
    AlertOctagon,
    CheckCircle2,
    FileText,
    History,
    Link2,
    Loader2,
    RefreshCw,
    ShieldCheck,
    Upload,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { FilePreviewSheet } from '@/components/knowledge/FilePreviewSheet'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/ui/toast'
import { wikiService } from '@/services/wikiService'
import type { WikiEvidenceChunkBrief, WikiPageDetail, WikiPageStatus, WikiSourceBrief } from '@/types/wiki'

interface WikiPageDetailViewProps {
    slug: string
    onBack?: () => void
    onSelectSlug?: (slug: string) => void
    isAdmin?: boolean
}

const LINK_TYPE_LABEL: Record<string, string> = {
    mentions: '提及',
    related: '相关',
    supersedes: '取代',
    contradicts: '冲突',
}

const RISK_LABEL: Record<string, string> = {
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

const BLOCKER_LABEL: Record<string, string> = {
    source_missing: '缺少来源',
    source_deleted: '来源已删',
    source_expired: '来源过期',
    active_conflict: '存在冲突',
    duplicate_entity: '重复实体',
    restricted_source_scope: '来源权限受限',
    merged_candidate: '已合并',
}

const DOMAIN_LABEL: Record<string, string> = {
    general: '通用',
    policy: '制度',
    product: '产品',
    customer: '客户',
    term: '术语',
    decision: '决策',
}

const STATUS_LABEL: Record<string, string> = {
    candidate: '候选',
    draft: '草稿',
    published: '已发布',
    verified: '已验证',
    deprecated: '已废弃',
    archived: '已归档',
}

const VISIBILITY_LABEL: Record<string, string> = {
    public: '公共',
    workspace: '工作区',
    department: '部门',
    private: '私有',
    unknown: '未知',
}

const PROPERTY_VALUE_LABEL: Record<string, string> = {
    test_method: '检验方法',
    general: '通用',
}

const MAJOR_SECTION_TITLES = new Set([
    '核心属性',
    '详细说明',
    '相关实体',
    '关键检验项目',
    '检验标准体系',
    '适用范围',
    '定义',
    '摘要',
])

const SOURCE_REF_PATTERN = /见\s*(?:表|图)\s*[A-Za-z0-9一二三四五六七八九十百千万.\-－—~～]+|见\s*第\s*[A-Za-z0-9一二三四五六七八九十百千万.\-－—~～]+\s*(?:章|节|条|款)?/g
const CHUNK_ID_PATTERN = /chunk_id\s*[:：]\s*([a-zA-Z0-9_-]+)/i
const CHUNK_BLOCK_PATTERN = /[（(][^()（）]*chunk_id\s*[:：][^()（）]*[)）]/gi

interface PreviewTarget {
    id: string
    name: string
    type: string
    page?: number | null
    requestKey: number
}

export function WikiPageDetailView({
    slug,
    onBack,
    onSelectSlug,
    isAdmin = false,
}: WikiPageDetailViewProps) {
    const [detail, setDetail] = useState<WikiPageDetail | null>(null)
    const [loading, setLoading] = useState(true)
    const [updatingStatus, setUpdatingStatus] = useState<WikiPageStatus | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [previewTarget, setPreviewTarget] = useState<PreviewTarget | null>(null)
    const [previewPanelWidth, setPreviewPanelWidth] = useState(560)
    const { toast } = useToast()

    const evidenceChunks = useMemo(() => collectEvidenceChunks(detail), [detail])
    const readableMarkdown = useMemo(
        () => normalizeWikiMarkdown(detail?.markdown_body || '', detail?.title || '', evidenceChunks),
        [detail?.markdown_body, detail?.title, evidenceChunks],
    )

    const load = async () => {
        setLoading(true)
        setError(null)
        try {
            setDetail(await wikiService.getPage(slug))
        } catch (err) {
            setError(err instanceof Error ? err.message : 'unknown_error')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        if (slug) load()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [slug])

    const handleStatusChange = async (nextStatus: WikiPageStatus) => {
        if (!detail || updatingStatus) return
        setUpdatingStatus(nextStatus)
        try {
            const result = await wikiService.updatePage(detail.slug, {
                status: nextStatus,
                commit_message: `status:${detail.status}->${nextStatus}`,
            })
            if (!result.success) {
                throw new Error(result.error || 'status_update_rejected')
            }
            toast({
                type: 'success',
                title: '状态已更新',
                description: `${detail.title} 已更新为 ${nextStatus}`,
            })
            await load()
        } catch (err) {
            toast({
                type: 'error',
                title: '状态更新失败',
                description: err instanceof Error ? err.message : '未知错误',
            })
        } finally {
            setUpdatingStatus(null)
        }
    }

    const openEvidence = (chunkId?: string | null) => {
        if (!detail) return
        const targetChunk = findEvidenceChunk(detail.sources, chunkId)
        const source = targetChunk
            ? detail.sources.find((item) => item.file_id === targetChunk.file_id)
            : detail.sources[0]
        const fileId = targetChunk?.file_id || source?.file_id
        if (!fileId) return
        const fileName = targetChunk?.file_name || source?.file_name || '来源'
        setPreviewTarget({
            id: fileId,
            name: fileName,
            type: getFileType(fileName),
            page: targetChunk?.page_number ?? null,
            requestKey: Date.now(),
        })
    }

    return (
        <div className="relative flex h-full overflow-hidden">
            <div className="flex-1 flex flex-col min-w-0 border-r border-manus-border">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-manus-border bg-manus-secondary">
                    {onBack && (
                        <Button variant="ghost" size="sm" onClick={onBack}>
                            <ArrowLeft className="h-4 w-4 mr-1" /> 返回
                        </Button>
                    )}
                    <div className="flex-1 min-w-0">
                        <div className="text-sm text-manus-muted truncate">{slug}</div>
                        <div className="text-base font-semibold truncate">
                            {detail?.title || slug}
                        </div>
                    </div>
                    <Button variant="outline" size="sm" disabled={loading} onClick={load}>
                        <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
                    </Button>
                </div>

                {loading ? (
                    <div className="flex-1 flex items-center justify-center text-manus-muted">
                        <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载中...
                    </div>
                ) : error ? (
                    <div className="flex-1 flex items-center justify-center text-error">
                        <AlertOctagon className="h-5 w-5 mr-2" />
                        加载失败：{error}
                    </div>
                ) : detail ? (
                    <ScrollArea className="flex-1">
                        <div className="px-6 py-5 max-w-3xl mx-auto">
                            {detail.summary && (
                                <blockquote className="mb-4 border-l-2 border-accent/40 pl-3 text-sm text-manus-muted italic">
                                    {detail.summary}
                                </blockquote>
                            )}
                            <div className="wiki-readable-body max-w-none break-words text-[15px] leading-8 text-manus-text">
                                <ReactMarkdown
                                    remarkPlugins={[remarkGfm]}
                                    urlTransform={wikiUrlTransform}
                                    components={{
                                        h1: ({ children }) => (
                                            <h1 className="mb-4 mt-7 border-b border-manus-border pb-2 text-2xl font-semibold leading-tight tracking-normal text-manus-text">
                                                {children}
                                            </h1>
                                        ),
                                        h2: ({ children }) => (
                                            <h2 className="mb-3 mt-7 text-xl font-semibold leading-snug tracking-normal text-manus-text">
                                                {children}
                                            </h2>
                                        ),
                                        h3: ({ children }) => (
                                            <h3 className="mb-2.5 mt-5 text-base font-semibold leading-snug tracking-normal text-manus-text">
                                                {children}
                                            </h3>
                                        ),
                                        p: ({ children }) => (
                                            <p className="my-3 text-[15px] leading-8 text-manus-text/88">
                                                {children}
                                            </p>
                                        ),
                                        ul: ({ children }) => (
                                            <ul className="my-3 space-y-1.5 pl-5 text-[15px] leading-8 text-manus-text/88">
                                                {children}
                                            </ul>
                                        ),
                                        ol: ({ children }) => (
                                            <ol className="my-3 space-y-1.5 pl-5 text-[15px] leading-8 text-manus-text/88">
                                                {children}
                                            </ol>
                                        ),
                                        li: ({ children }) => <li className="pl-1">{children}</li>,
                                        strong: ({ children }) => (
                                            <strong className="font-semibold text-manus-text">
                                                {children}
                                            </strong>
                                        ),
                                        a: ({ href, children }) => {
                                            const link = String(href || '')
                                            if (link.startsWith('wiki://')) {
                                                const targetSlug = decodeURIComponent(link.replace('wiki://', ''))
                                                return (
                                                    <button
                                                        type="button"
                                                        className="rounded bg-accent/10 px-1.5 py-0.5 font-medium text-accent underline-offset-2 hover:bg-accent/15 hover:underline"
                                                        onClick={() => targetSlug && onSelectSlug?.(targetSlug)}
                                                    >
                                                        {children}
                                                    </button>
                                                )
                                            }
                                            if (link.startsWith('source://')) {
                                                const chunkId = decodeURIComponent(link.replace('source://', ''))
                                                const chunk = findEvidenceChunk(detail.sources, chunkId)
                                                return (
                                                    <button
                                                        type="button"
                                                        className="rounded border border-accent/25 bg-accent/[0.08] px-1.5 py-0.5 text-[13px] font-medium text-accent shadow-sm hover:bg-accent/15"
                                                        title={chunk?.page_number ? `打开原文 P${chunk.page_number}` : '打开原文，未定位页码'}
                                                        onClick={() => openEvidence(chunkId)}
                                                    >
                                                        {children}
                                                    </button>
                                                )
                                            }
                                            return (
                                                <a href={href} className="text-accent underline underline-offset-2">
                                                    {children}
                                                </a>
                                            )
                                        },
                                    }}
                                >
                                    {readableMarkdown}
                                </ReactMarkdown>
                            </div>
                            <div className="mt-6 flex flex-wrap gap-2 text-xs text-manus-muted">
                                <span>领域：{DOMAIN_LABEL[detail.domain] || detail.domain}</span>
                                <span>状态：{STATUS_LABEL[detail.status] || detail.status}</span>
                                <span>版本：v{detail.version}</span>
                                <span>字数：{detail.char_count}</span>
                                {detail.last_compiled_at && (
                                    <span>
                                        最近编译：
                                        {new Date(detail.last_compiled_at).toLocaleString()}
                                    </span>
                                )}
                            </div>
                            {isAdmin && (
                                <div className="mt-3 flex flex-wrap gap-2">
                                    {['candidate', 'draft', 'deprecated'].includes(detail.status) && (
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={!!updatingStatus}
                                            onClick={() => handleStatusChange('published')}
                                        >
                                            {updatingStatus === 'published' ? (
                                                <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                                            ) : (
                                                <Upload className="h-3.5 w-3.5 mr-1" />
                                            )}
                                            发布
                                        </Button>
                                    )}
                                    {detail.status === 'published' && (
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={!!updatingStatus}
                                            onClick={() => handleStatusChange('verified')}
                                        >
                                            {updatingStatus === 'verified' ? (
                                                <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                                            ) : (
                                                <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                                            )}
                                            标记已验证
                                        </Button>
                                    )}
                                    {!['archived', 'deprecated'].includes(detail.status) && (
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={!!updatingStatus}
                                            onClick={() => handleStatusChange('archived')}
                                        >
                                            {updatingStatus === 'archived' ? (
                                                <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                                            ) : (
                                                <Archive className="h-3.5 w-3.5 mr-1" />
                                            )}
                                            归档
                                        </Button>
                                    )}
                                </div>
                            )}
                        </div>
                    </ScrollArea>
                ) : null}
            </div>

            <aside className="w-80 shrink-0 flex flex-col bg-manus-secondary">
                <ScrollArea className="flex-1">
                    <Section title="反向链接" icon={<Link2 className="h-4 w-4" />}>
                        {(detail?.incoming_links || []).length === 0 ? (
                            <div className="text-xs text-manus-muted">暂无入链</div>
                        ) : (
                            <ul className="space-y-1.5">
                                {detail!.incoming_links.map((link) => (
                                    <LinkRow
                                        key={link.id}
                                        title={link.source_title || link.source_slug}
                                        slug={link.source_slug}
                                        linkType={link.link_type}
                                        status={link.status}
                                        note={link.note}
                                        onSelectSlug={onSelectSlug}
                                    />
                                ))}
                            </ul>
                        )}
                    </Section>

                    <Section title="出链" icon={<Link2 className="h-4 w-4 -scale-x-100" />}>
                        {(detail?.outgoing_links || []).length === 0 ? (
                            <div className="text-xs text-manus-muted">暂无出链</div>
                        ) : (
                            <ul className="space-y-1.5">
                                {detail!.outgoing_links.map((link) => (
                                    <LinkRow
                                        key={link.id}
                                        title={link.target_title || link.target_slug}
                                        slug={link.target_slug}
                                        linkType={link.link_type}
                                        status={link.status}
                                        note={link.note}
                                        onSelectSlug={onSelectSlug}
                                    />
                                ))}
                            </ul>
                        )}
                    </Section>

                    <Section title="候选治理" icon={<ShieldCheck className="h-4 w-4" />}>
                        {!detail?.governance ? (
                            <div className="text-xs text-manus-muted">暂无治理评分</div>
                        ) : (
                            <div className="space-y-2 text-sm">
                                <KeyValue label="评分" value={`${detail.governance.score} / ${detail.governance.priority}`} />
                                <KeyValue
                                    label="可发布"
                                    value={detail.governance.can_publish ? '是' : '否'}
                                    valueClassName={detail.governance.can_publish ? 'text-emerald-600' : 'text-amber-600'}
                                />
                                {detail.governance.publish_blockers.length > 0 && (
                                    <TagList
                                        items={detail.governance.publish_blockers}
                                        labels={BLOCKER_LABEL}
                                        className="bg-rose-500/10 text-rose-600"
                                    />
                                )}
                                {detail.governance.risks.length > 0 && (
                                    <TagList
                                        items={detail.governance.risks}
                                        labels={RISK_LABEL}
                                        className="bg-amber-500/10 text-amber-600"
                                    />
                                )}
                            </div>
                        )}
                    </Section>

                    <Section title="知识范围" icon={<ShieldCheck className="h-4 w-4" />}>
                        {!detail?.source_scope ? (
                            <div className="text-xs text-manus-muted">暂无范围信息</div>
                        ) : (
                            <div className="space-y-2 text-sm">
                                <KeyValue
                                    label="部门"
                                    value={
                                        detail.source_scope.department_ids.length
                                            ? detail.source_scope.department_ids.join(', ')
                                            : '未分类'
                                    }
                                />
                                <KeyValue
                                    label="跨部门"
                                    value={detail.source_scope.is_cross_department ? '是' : '否'}
                                    valueClassName={detail.source_scope.is_cross_department ? 'text-amber-600' : undefined}
                                />
                                <KeyValue
                                    label="过期来源"
                                    value={String(detail.source_scope.expired_source_count || 0)}
                                    valueClassName={detail.source_scope.has_expired_source ? 'text-rose-600' : undefined}
                                />
                            </div>
                        )}
                    </Section>

                    <Section title="出处" icon={<FileText className="h-4 w-4" />}>
                        {(detail?.sources || []).length === 0 ? (
                            <div className="text-xs text-manus-muted">暂无溯源</div>
                        ) : (
                            <ul className="space-y-2">
                                {detail!.sources.map((src) => (
                                    <SourceRow
                                        key={src.id}
                                        source={src}
                                        onOpenEvidence={(chunkId) => openEvidence(chunkId)}
                                    />
                                ))}
                            </ul>
                        )}
                    </Section>

                    <Section title="修订历史" icon={<History className="h-4 w-4" />}>
                        {(detail?.revisions || []).length === 0 ? (
                            <div className="text-xs text-manus-muted">暂无记录</div>
                        ) : (
                            <ul className="space-y-1.5">
                                {detail!.revisions.map((r) => (
                                    <li key={`${r.version}-${r.committed_at || ''}`} className="text-sm">
                                        <div className="text-foreground">
                                            v{r.version} · {r.committed_by}
                                        </div>
                                        <div className="text-xs text-manus-muted">
                                            {r.committed_at ? new Date(r.committed_at).toLocaleString() : '-'}
                                        </div>
                                        {r.commit_message && (
                                            <div className="text-[11px] text-manus-muted/70 truncate">
                                                {r.commit_message}
                                            </div>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </Section>
                </ScrollArea>
            </aside>
            <FilePreviewSheet
                isOpen={!!previewTarget}
                onClose={() => setPreviewTarget(null)}
                fileId={previewTarget?.id || null}
                fileName={previewTarget?.name || null}
                fileType={previewTarget?.type || null}
                initialPage={previewTarget?.page ?? undefined}
                requestKey={previewTarget?.requestKey}
                panelWidth={previewPanelWidth}
                onPanelWidthChange={setPreviewPanelWidth}
                minPanelWidth={460}
                maxPanelWidth={820}
            />
        </div>
    )
}

function LinkRow({
    title,
    slug,
    linkType,
    status,
    note,
    onSelectSlug,
}: {
    title?: string | null
    slug?: string | null
    linkType: string
    status: string
    note?: string | null
    onSelectSlug?: (slug: string) => void
}) {
    return (
        <li>
            <button
                type="button"
                onClick={() => slug && onSelectSlug?.(slug)}
                className="text-left w-full text-sm hover:bg-manus-hover px-2 py-1 rounded"
            >
                <div className="text-foreground flex items-center gap-2">
                    <span className="truncate">{title || slug}</span>
                    {linkType === 'contradicts' && (
                        <span className="text-xs text-error bg-error/10 px-1.5 rounded">
                            冲突
                        </span>
                    )}
                </div>
                <div className="text-xs text-manus-muted">
                    {LINK_TYPE_LABEL[linkType] || linkType}
                    {status !== 'active' ? `（${status}）` : ''}
                </div>
                {note && (
                    <div className="text-[11px] text-manus-muted/70 truncate">
                        {note}
                    </div>
                )}
            </button>
        </li>
    )
}

function SourceRow({
    source,
    onOpenEvidence,
}: {
    source: WikiSourceBrief
    onOpenEvidence: (chunkId?: string | null) => void
}) {
    const chunks = source.evidence_chunks?.length
        ? source.evidence_chunks
        : (source.chunk_ids || []).map((chunkId) => ({
            chunk_id: chunkId,
            file_id: source.file_id,
            file_name: source.file_name,
            page_number: null,
            preview: '',
        }))

    return (
        <li className="rounded-lg border border-manus-border bg-manus px-3 py-2 text-sm">
            <button
                type="button"
                className="w-full text-left"
                onClick={() => onOpenEvidence(chunks[0]?.chunk_id)}
                title="打开原文"
            >
                <div className="font-medium text-foreground truncate">
                    {source.file_name || source.file_id}
                </div>
                <div className="mt-1 text-xs text-manus-muted">
                    引用片段 {chunks.length || source.chunk_ids.length} 条
                </div>
            </button>
            <div className="mt-1.5 text-[11px] leading-5 text-manus-muted/70">
                部门 {source.department_id ?? '未分类'} · {VISIBILITY_LABEL[source.visibility || 'unknown'] || source.visibility || '未知'}
                {source.business_domain ? ` · ${source.business_domain}` : ''}
                {source.document_type ? ` · ${source.document_type}` : ''}
                {source.confidentiality_level ? ` · ${source.confidentiality_level}` : ''}
            </div>
            {chunks.length > 0 && (
                <div className="mt-2 space-y-1.5">
                    {chunks.slice(0, 4).map((chunk) => (
                        <button
                            key={chunk.chunk_id}
                            type="button"
                            className="w-full rounded-md bg-manus-secondary px-2 py-1.5 text-left text-[11px] leading-5 text-manus-muted hover:bg-manus-hover hover:text-manus-text"
                            onClick={() => onOpenEvidence(chunk.chunk_id)}
                            title={`切片 ${shortChunkId(chunk.chunk_id)}${chunk.page_number ? ` · P${chunk.page_number}` : ' · 未定位页码'}`}
                        >
                            <span className="mr-1 font-semibold text-manus-text/75">
                                {chunk.page_number ? `P${chunk.page_number}` : '未定位页码'}
                            </span>
                            {chunk.preview || source.excerpt || '打开原文查看对应位置'}
                        </button>
                    ))}
                    {chunks.length > 4 && (
                        <div className="text-[11px] text-manus-muted/70">
                            另有 {chunks.length - 4} 条片段
                        </div>
                    )}
                </div>
            )}
            {chunks.length === 0 && source.excerpt && (
                <div className="mt-1.5 text-[11px] text-manus-muted/70 line-clamp-2">
                    {source.excerpt}
                </div>
            )}
        </li>
    )
}

function KeyValue({
    label,
    value,
    valueClassName,
}: {
    label: string
    value: string
    valueClassName?: string
}) {
    return (
        <div className="rounded-md border border-manus-border bg-manus px-2.5 py-2">
            <div className="text-[11px] leading-none text-manus-muted">{label}</div>
            <span className={cn('mt-1 block break-words text-sm font-semibold text-manus-text', valueClassName)}>
                {value}
            </span>
        </div>
    )
}

function TagList({
    items,
    labels,
    className,
}: {
    items: string[]
    labels: Record<string, string>
    className: string
}) {
    return (
        <div className="flex flex-wrap gap-1">
            {items.map((item) => (
                <span key={item} className={cn('rounded px-1.5 py-0.5 text-xs', className)}>
                    {labels[item] || item}
                </span>
            ))}
        </div>
    )
}

function collectEvidenceChunks(detail: WikiPageDetail | null): WikiEvidenceChunkBrief[] {
    if (!detail) return []
    return (detail.sources || []).flatMap((source) => {
        if (source.evidence_chunks?.length) return source.evidence_chunks
        return (source.chunk_ids || []).map((chunkId) => ({
            chunk_id: chunkId,
            file_id: source.file_id,
            file_name: source.file_name,
            page_number: null,
            preview: '',
        }))
    })
}

function findEvidenceChunk(
    sources: WikiSourceBrief[],
    chunkId?: string | null,
): WikiEvidenceChunkBrief | null {
    const evidence = sources.flatMap((source) => {
        if (source.evidence_chunks?.length) return source.evidence_chunks
        return (source.chunk_ids || []).map((id) => ({
            chunk_id: id,
            file_id: source.file_id,
            file_name: source.file_name,
            page_number: null,
            preview: '',
        }))
    })
    if (!evidence.length) return null
    if (!chunkId || chunkId === '__first__') return evidence[0]
    return evidence.find((item) => item.chunk_id === chunkId) || evidence[0]
}

function normalizeWikiMarkdown(
    markdown: string,
    title: string,
    evidenceChunks: WikiEvidenceChunkBrief[],
): string {
    const fallbackChunkId = evidenceChunks[0]?.chunk_id || '__first__'
    const lines = String(markdown || '').replace(/\r\n?/g, '\n').split('\n')
    const normalized: string[] = []
    let inRelatedSection = false

    for (const rawLine of lines) {
        const chunkMatch = rawLine.match(CHUNK_ID_PATTERN)
        const chunkId = chunkMatch?.[1] || fallbackChunkId
        let line = rawLine.replace(CHUNK_BLOCK_PATTERN, '').trimEnd()
        const trimmed = line.trim()

        if (!trimmed) {
            normalized.push('')
            continue
        }

        const cleanHeading = trimmed.replace(/^#+\s*/, '')
        if (cleanHeading === title) {
            normalized.push(`## ${cleanHeading}`)
            inRelatedSection = false
            continue
        }
        if (MAJOR_SECTION_TITLES.has(cleanHeading)) {
            normalized.push(`## ${cleanHeading}`)
            inRelatedSection = cleanHeading === '相关实体'
            continue
        }

        const related = trimmed.match(/^\[\[([^|\]]+)(?:\|([^\]]+))?]]\s*[:：]\s*(.*)$/)
        if (related) {
            const targetSlug = related[1].trim()
            const label = (related[2] || targetSlug).trim()
            const desc = related[3]?.trim()
            normalized.push(`- [${label}](wiki://${encodeURIComponent(targetSlug)})${desc ? `：${desc}` : ''}`)
            continue
        }

        if (inRelatedSection && /^\[\[[^\]]+]]/.test(trimmed)) {
            line = line.replace(/\[\[([^|\]]+)(?:\|([^\]]+))?]]/g, (_, targetSlug: string, label?: string) => {
                const cleanSlug = String(targetSlug || '').trim()
                return `[${String(label || cleanSlug).trim()}](wiki://${encodeURIComponent(cleanSlug)})`
            })
        }

        line = line
            .replace(/^(类型|别名|域|领域|目的|主要内容|适用零件)\s*[:：]\s*/g, '**$1**：')
            .replace(SOURCE_REF_PATTERN, (match) => `[${match}](source://${encodeURIComponent(chunkId)})`)

        line = line.replace(
            /^(\*\*(?:类型|域|领域)\*\*：)\s*([a-zA-Z0-9_-]+)\s*$/,
            (_, label: string, value: string) => `${label}${PROPERTY_VALUE_LABEL[value] || value}`,
        )

        if (/^#+\s+/.test(line)) {
            normalized.push(line)
            continue
        }
        if (/^[一二三四五六七八九十\d]+[.、]\s*[^。；;：:]{2,28}$/.test(trimmed)) {
            normalized.push(`### ${trimmed}`)
            continue
        }
        if (
            trimmed.length <= 18
            && !/[。！？；;，,：:]/.test(trimmed)
            && !/^[-*]\s+/.test(trimmed)
        ) {
            normalized.push(`### ${trimmed}`)
            continue
        }

        normalized.push(line)
    }

    return normalized.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

function getFileType(fileName: string): string {
    const ext = fileName.split('.').pop()?.toLowerCase()
    return ext && ext !== fileName.toLowerCase() ? ext : 'unknown'
}

function shortChunkId(chunkId: string): string {
    if (!chunkId) return ''
    return chunkId.length > 10 ? `${chunkId.slice(0, 10)}…` : chunkId
}

function wikiUrlTransform(url: string): string {
    if (/^(https?:|mailto:|#|\/)/i.test(url)) return url
    if (url.startsWith('wiki://') || url.startsWith('source://')) return url
    return ''
}

function Section({
    title,
    icon,
    children,
}: {
    title: string
    icon?: ReactNode
    children: ReactNode
}) {
    return (
        <div className="px-4 py-3 border-b border-manus-border">
            <div className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-manus-muted mb-2">
                {icon}
                <span>{title}</span>
            </div>
            {children}
        </div>
    )
}
