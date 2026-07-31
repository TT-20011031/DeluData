/**
 * Wiki INDEX 视图：按 domain 分组的实体页清单 + 关键词搜索 + 编译/巡检按钮。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
    BookOpen,
    Loader2,
    Plus,
    RefreshCw,
    Search,
    ShieldAlert,
    Sparkles,
    XCircle,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/ui/toast'
import { wikiService } from '@/services/wikiService'
import type { WikiCompileTaskStatus, WikiPageSummary } from '@/types/wiki'

const COMPILE_POLL_INTERVAL_MS = 2000
const COMPILE_POLL_TIMEOUT_MS = 30 * 60 * 1000 // 30 分钟安全兑
const STAGE_LABELS: Record<string, string> = {
    queued: '已入队，等待 worker',
    extracting: '正在抽取候选实体',
    compiling: '编译中（LLM 调用可能耗时数分钟）',
    completed: '已完成',
    cancelled: '已取消',
    failed: '失败',
}

interface WikiIndexViewProps {
    selectedSlug: string | null
    onSelectSlug: (slug: string) => void
    isAdmin?: boolean
    scope?: string
    departmentId?: number
    businessDomain?: string
    documentType?: string
    confidentialityLevel?: string
}

const DOMAIN_LABELS: Record<string, string> = {
    general: '通用',
    policy: '制度政策',
    product: '产品手册',
    customer: '客户档案',
    term: '术语',
    decision: '决策记录',
}

export function WikiIndexView({
    selectedSlug,
    onSelectSlug,
    isAdmin = false,
    scope,
    departmentId,
    businessDomain,
    documentType,
    confidentialityLevel,
}: WikiIndexViewProps) {
    const [pages, setPages] = useState<WikiPageSummary[]>([])
    const [total, setTotal] = useState(0)
    const [keyword, setKeyword] = useState('')
    const [loading, setLoading] = useState(false)
    const [compiling, setCompiling] = useState(false)
    const [compileTask, setCompileTask] = useState<WikiCompileTaskStatus | null>(null)
    const [linting, setLinting] = useState(false)
    const { toast } = useToast()
    const pollTimerRef = useRef<number | null>(null)

    const stopPolling = () => {
        if (pollTimerRef.current !== null) {
            window.clearInterval(pollTimerRef.current)
            pollTimerRef.current = null
        }
    }

    useEffect(() => {
        return () => stopPolling()
    }, [])

    const load = async () => {
        setLoading(true)
        try {
            const data = await wikiService.listPages({
                keyword: keyword.trim() || undefined,
                status: 'published,verified',
                scope,
                department_id: departmentId,
                business_domain: businessDomain || undefined,
                document_type: documentType || undefined,
                confidentiality_level: confidentialityLevel || undefined,
                limit: 200,
            })
            setPages(data.items)
            setTotal(data.total)
        } catch (err) {
            console.error('[Wiki] 列表加载失败', err)
            toast({
                type: 'error',
                title: 'Wiki 列表加载失败',
                description: err instanceof Error ? err.message : '未知错误',
            })
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        load()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope, departmentId, businessDomain, documentType, confidentialityLevel])

    const grouped = useMemo(() => {
        const map = new Map<string, WikiPageSummary[]>()
        for (const p of pages) {
            const key = p.domain || 'general'
            if (!map.has(key)) map.set(key, [])
            map.get(key)!.push(p)
        }
        return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b))
    }, [pages])

    /**
     * 全量编译：改为异步入队 + 轮询进度。
     * 避免 HTTP 长连接超时、按钮死转、不知中间状态。
     */
    const handleCompile = async () => {
        if (compiling) return
        stopPolling()
        setCompiling(true)
        setCompileTask(null)

        let taskId: string
        try {
            const accepted = await wikiService.compileAsync({ scope: 'workspace' })
            taskId = accepted.task_id
            if (accepted.deduplicated) {
                toast({
                    type: 'info',
                    title: '复用已有任务',
                    description: '已有一个进行中的全量编译任务，此次不重复起。',
                })
            } else {
                toast({
                    type: 'info',
                    title: '编译已入队',
                    description: '后台 worker 正在处理，可继续浏览。',
                    duration: 3000,
                })
            }
        } catch (err) {
            toast({
                type: 'error',
                title: '入队失败',
                description: err instanceof Error ? err.message : '未知错误',
            })
            setCompiling(false)
            return
        }

        // 开始轮询
        const startedAt = Date.now()
        const tick = async () => {
            try {
                const status = await wikiService.getCompileTask(taskId)
                setCompileTask(status)

                if (status.status === 'succeeded') {
                    stopPolling()
                    setCompiling(false)
                    const r = status.result || {}
                    toast({
                        type: 'success',
                        title: '编译完成',
                        description: `新建 ${r.created ?? 0} / 更新 ${r.updated ?? 0}，共 ${r.files ?? 0} 个文件（${(r.elapsed_seconds ?? 0).toFixed(1)}s）`,
                    })
                    await load()
                    return
                }
                if (status.status === 'cancelled') {
                    stopPolling()
                    setCompiling(false)
                    const r = status.result || {}
                    const kept = (r.created ?? 0) + (r.updated ?? 0)
                    toast({
                        type: 'info',
                        title: '编译已取消',
                        description: kept > 0
                            ? `已保留 ${kept} 页（新建 ${r.created ?? 0} / 更新 ${r.updated ?? 0}）。`
                            : '尚无页面落库，本次编译已全部中止。',
                    })
                    await load()
                    return
                }
                if (status.status === 'failed') {
                    stopPolling()
                    setCompiling(false)
                    toast({
                        type: 'error',
                        title: '编译失败',
                        description: status.error_message || '后台任务失败',
                    })
                    return
                }
                if (Date.now() - startedAt > COMPILE_POLL_TIMEOUT_MS) {
                    stopPolling()
                    setCompiling(false)
                    toast({
                        type: 'warning',
                        title: '轮询超时',
                        description: '任务仍可能在后台运行，请手动刷新列表查看。',
                    })
                }
            } catch (err) {
                console.error('[Wiki] 轮询任务失败', err)
                // 单次轮询失败不中断，下一轮重试
            }
        }
        // 立即 tick 一次，然后间隔轮询
        await tick()
        if (pollTimerRef.current === null) {
            pollTimerRef.current = window.setInterval(tick, COMPILE_POLL_INTERVAL_MS)
        }
    }

    /** 取消当前编译任务（软取消）。 */
    const handleCancelCompile = async () => {
        const taskId = compileTask?.task_id
        if (!taskId) return
        try {
            const res = await wikiService.cancelCompileTask(taskId)
            if (res.outcome === 'cancelled_immediately') {
                toast({
                    type: 'info',
                    title: '已取消',
                    description: '任务尚未开始，已直接中止。',
                })
                // 轮询会在下一次 tick 读到 cancelled 终态并清理 UI
            } else if (res.outcome === 'cancel_requested') {
                toast({
                    type: 'info',
                    title: '取消请求已发送',
                    description: 'worker 会在当前批次完成后停止；已成功的页会被保留。',
                })
            } else if (res.outcome === 'already_cancelling') {
                toast({
                    type: 'info',
                    title: '正在取消',
                    description: '前一次取消请求仍在处理中，请稍等。',
                })
            } else if (res.outcome === 'already_terminal') {
                toast({
                    type: 'warning',
                    title: '任务已终止',
                    description: '任务已经结束，无法再取消。',
                })
            }
        } catch (err) {
            toast({
                type: 'error',
                title: '取消失败',
                description: err instanceof Error ? err.message : '未知错误',
            })
        }
    }

    const handleLint = async () => {
        setLinting(true)
        try {
            const report = await wikiService.lint()
            toast({
                type: report.issues.length ? 'warning' : 'success',
                title: '健康度巡检完成',
                description: `共 ${report.issues.length} 项待处理（页 ${report.page_count} / 链 ${report.link_count} / 冲突 ${report.open_conflicts}）`,
                duration: 5000,
            })
        } catch (err) {
            toast({
                type: 'error',
                title: '巡检失败',
                description: err instanceof Error ? err.message : '未知错误',
            })
        } finally {
            setLinting(false)
        }
    }

    return (
        <div className="flex flex-col h-full bg-manus-secondary border-r border-manus-border">
            <div className="px-4 py-3 border-b border-manus-border space-y-2">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <BookOpen className="h-4 w-4 text-accent" />
                        <span className="text-sm font-semibold">实体 Wiki</span>
                        <span className="text-xs text-manus-muted">{total} 页</span>
                    </div>
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => load()}
                        disabled={loading}
                    >
                        <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
                    </Button>
                </div>
                <div className="relative">
                    <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-4 w-4 text-manus-muted" />
                    <Input
                        placeholder="搜索 slug / 标题 / 摘要"
                        value={keyword}
                        onChange={(e) => setKeyword(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter') load()
                        }}
                        className="pl-8 h-8 text-sm"
                    />
                </div>
                {isAdmin && (
                    <div className="flex gap-2">
                        {compiling ? (
                            // 编译中：按钮变为"取消编译"，允许用户软取消。
                            // cancel_requested 状态下按钮 disabled，避免重复点击。
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={compileTask?.status === 'cancel_requested'}
                                onClick={handleCancelCompile}
                                className="flex-1 text-red-600 hover:bg-red-50 hover:text-red-700 border-red-200"
                                title={
                                    compileTask?.status === 'cancel_requested'
                                        ? '正在取消，等待当前批次结束'
                                        : '取消编译（软取消）：已成功的页会被保留'
                                }
                            >
                                {compileTask?.status === 'cancel_requested' ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                                ) : (
                                    <XCircle className="h-3.5 w-3.5 mr-1" />
                                )}
                                {compileTask?.status === 'cancel_requested'
                                    ? '取消中...'
                                    : `取消编译 ${compileTask?.progress || 0}%`}
                            </Button>
                        ) : (
                            <Button
                                size="sm"
                                variant="outline"
                                onClick={handleCompile}
                                className="flex-1"
                            >
                                <Sparkles className="h-3.5 w-3.5 mr-1" />
                                生成候选
                            </Button>
                        )}
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={linting || compiling}
                            onClick={handleLint}
                            className="flex-1"
                        >
                            {linting ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
                            ) : (
                                <ShieldAlert className="h-3.5 w-3.5 mr-1" />
                            )}
                            健康巡检
                        </Button>
                    </div>
                )}
                {compiling && compileTask && (
                    <div className="text-[11px] text-manus-muted leading-relaxed">
                        {compileTask.status === 'cancel_requested'
                            ? '正在取消，等待当前批次结束…'
                            : STAGE_LABELS[compileTask.stage || ''] || compileTask.stage || compileTask.status}
                        {compileTask.started_at
                            ? ` · 已运行 ${Math.max(0, Math.round((Date.now() - new Date(compileTask.started_at).getTime()) / 1000))}s`
                            : ''}
                    </div>
                )}
            </div>

            <ScrollArea className="flex-1">
                {loading ? (
                    <div className="flex items-center justify-center py-10 text-manus-muted text-sm">
                        <Loader2 className="h-4 w-4 animate-spin mr-2" /> 加载中...
                    </div>
                ) : grouped.length === 0 ? (
                    <EmptyState onCompile={isAdmin ? handleCompile : undefined} />
                ) : (
                    <div className="px-2 py-2">
                        {grouped.map(([domain, items]) => (
                            <div key={domain} className="mb-3">
                                <div className="px-2 py-1 text-xs uppercase tracking-wide text-manus-muted">
                                    {DOMAIN_LABELS[domain] || domain}（{items.length}）
                                </div>
                                <ul className="space-y-0.5">
                                    {items.map((p) => (
                                        <li key={p.id}>
                                            <button
                                                type="button"
                                                onClick={() => onSelectSlug(p.slug)}
                                                className={cn(
                                                    'w-full text-left px-2 py-1.5 rounded text-sm transition-colors',
                                                    selectedSlug === p.slug
                                                        ? 'bg-accent/10 text-accent'
                                                        : 'text-manus-text hover:bg-manus-hover',
                                                )}
                                            >
                                                <div className="font-medium truncate">{p.title}</div>
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ))}
                    </div>
                )}
            </ScrollArea>
        </div>
    )
}

function EmptyState({ onCompile }: { onCompile?: () => void }) {
    return (
        <div className="px-6 py-10 text-center text-sm text-manus-muted space-y-3">
            <BookOpen className="mx-auto h-8 w-8 opacity-40" />
            <div>暂无实体页</div>
            <div className="text-[11px] leading-relaxed">
                上传文档只进入 RAG 原文证据层。
                <br />
                高频或重点知识可先生成候选页，再发布为正式 Wiki。
            </div>
            {onCompile && (
                <Button size="sm" variant="outline" onClick={onCompile}>
                    <Plus className="h-3.5 w-3.5 mr-1" /> 生成候选
                </Button>
            )}
        </div>
    )
}
