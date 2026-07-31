/**
 * [M3 收尾] Wiki 路由健康度卡片。
 *
 * 调用 `/api/wiki/metrics/route?days=N` 聚合，展示：
 * - 总调用数 / Wiki 命中率 / Gap 率 / 平均时延
 * - 三路径 (rag/wiki/both) 对比小卡
 *
 * 仅管理员在 WikiView 右侧空白态下可见。
 */
import { useEffect, useState } from 'react'
import { Activity, AlertTriangle, Gauge, Loader2, RefreshCw, Timer } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { wikiService } from '@/services/wikiService'
import type { WikiQuotaStatus, WikiRouteSummary } from '@/services/wikiService'

const WINDOW_OPTIONS: { value: number; label: string }[] = [
    { value: 7, label: '近 7 天' },
    { value: 14, label: '近 14 天' },
    { value: 30, label: '近 30 天' },
]

const PATH_LABELS: Record<string, string> = {
    rag: 'RAG',
    wiki: 'Wiki',
    both: 'Both',
}

const PATH_COLORS: Record<string, string> = {
    rag: 'text-blue-500 bg-blue-500/10 border-blue-500/30',
    wiki: 'text-emerald-500 bg-emerald-500/10 border-emerald-500/30',
    both: 'text-violet-500 bg-violet-500/10 border-violet-500/30',
}

function formatPercent(value: number): string {
    if (!Number.isFinite(value)) return '0%'
    return `${(value * 100).toFixed(1)}%`
}

function formatLatency(ms: number): string {
    if (!ms) return '—'
    if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`
    return `${ms} ms`
}

export function WikiRouteHealthCard() {
    const [days, setDays] = useState(7)
    const [summary, setSummary] = useState<WikiRouteSummary | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [quota, setQuota] = useState<WikiQuotaStatus | null>(null)

    const load = async (targetDays: number) => {
        setLoading(true)
        setError(null)
        try {
            const [routeData, quotaData] = await Promise.all([
                wikiService.getRouteMetrics(targetDays),
                wikiService.getQuota().catch(() => null), // 配额查询失败不阻塞主指标
            ])
            setSummary(routeData)
            setQuota(quotaData)
        } catch (err) {
            setError(err instanceof Error ? err.message : '加载失败')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        load(days)
    }, [days])

    const total = summary?.total ?? 0
    const hasData = !!summary && total > 0

    return (
        <div className="w-full max-w-2xl mx-auto rounded-lg border border-manus-border bg-manus-secondary p-4 space-y-3">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <Activity className="h-4 w-4 text-accent" />
                    <span className="text-sm font-semibold text-manus-text">
                        Wiki 路由健康度
                    </span>
                </div>
                <div className="flex items-center gap-1">
                    <div className="flex items-center gap-0.5 rounded-md border border-manus-border p-0.5">
                        {WINDOW_OPTIONS.map((opt) => (
                            <button
                                key={opt.value}
                                type="button"
                                onClick={() => setDays(opt.value)}
                                className={cn(
                                    'px-2 py-0.5 text-[11px] rounded transition-colors',
                                    days === opt.value
                                        ? 'bg-accent/15 text-accent'
                                        : 'text-manus-subtle hover:text-manus-text',
                                )}
                            >
                                {opt.label}
                            </button>
                        ))}
                    </div>
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        disabled={loading}
                        onClick={() => load(days)}
                        title="刷新"
                    >
                        <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
                    </Button>
                </div>
            </div>

            {error ? (
                <div className="flex items-center gap-2 text-xs text-rose-500 py-3">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    {error}
                </div>
            ) : loading && !summary ? (
                <div className="flex items-center gap-2 text-xs text-manus-subtle py-6 justify-center">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    加载中...
                </div>
            ) : !hasData ? (
                <div className="text-xs text-manus-subtle py-6 text-center">
                    {summary ? '该窗口内暂无路由调用记录' : '暂无数据'}
                    <div className="mt-1 text-[11px] text-manus-muted">
                        开启 <code className="px-1 rounded bg-manus-muted/30">wiki.first_enabled</code>{' '}
                        并发起几次知识库问答后即可看到聚合。
                    </div>
                </div>
            ) : (
                <>
                    {/* 汇总一行 */}
                    <div className="grid grid-cols-4 gap-2">
                        <SummaryCell label="总调用" value={total.toString()} />
                        <SummaryCell
                            label="Wiki 命中率"
                            value={formatPercent(summary.wiki_hit_rate)}
                            accent={summary.wiki_hit_rate >= 0.3 ? 'good' : 'warn'}
                        />
                        <SummaryCell
                            label="Gap 率"
                            value={formatPercent(summary.wiki_gap_rate)}
                            accent={summary.wiki_gap_rate <= 0.1 ? 'good' : 'warn'}
                        />
                        <SummaryCell
                            label="平均时延"
                            value={formatLatency(summary.avg_latency_ms)}
                            icon={<Timer className="h-3 w-3" />}
                        />
                    </div>

                    {/* 三路径 by_path */}
                    <div className="grid grid-cols-3 gap-2">
                        {(['rag', 'wiki', 'both'] as const).map((p) => {
                            const bucket = summary.by_path[p] || {
                                count: 0,
                                avg_latency_ms: 0,
                                wiki_gap_count: 0,
                            }
                            const pct = total > 0 ? bucket.count / total : 0
                            return (
                                <div
                                    key={p}
                                    className={cn(
                                        'rounded-md border p-2 space-y-1',
                                        PATH_COLORS[p],
                                    )}
                                >
                                    <div className="flex items-center justify-between text-[11px] font-semibold">
                                        <span>{PATH_LABELS[p]}</span>
                                        <span className="opacity-80">{formatPercent(pct)}</span>
                                    </div>
                                    <div className="text-lg font-bold tabular-nums">
                                        {bucket.count}
                                    </div>
                                    <div className="flex items-center justify-between text-[10px] opacity-70">
                                        <span>{formatLatency(bucket.avg_latency_ms)}</span>
                                        {bucket.wiki_gap_count > 0 && (
                                            <span className="inline-flex items-center gap-0.5">
                                                <AlertTriangle className="h-2.5 w-2.5" />
                                                Gap {bucket.wiki_gap_count}
                                            </span>
                                        )}
                                    </div>
                                </div>
                            )
                        })}
                    </div>

                    <div className="text-[11px] text-manus-muted text-right">
                        回退 RAG {summary.wiki_fallback_to_rag_count || 0} · 误切 SQL {summary.wrong_tool_repair_count || 0} · Wiki 未用 {summary.wiki_hit_but_not_used_count || 0}
                    </div>
                    <div className="text-[11px] text-manus-muted text-right">
                        窗口起点: {new Date(summary.since).toLocaleString()}
                    </div>
                </>
            )}

            {quota && <QuotaSection quota={quota} />}
        </div>
    )
}

function QuotaSection({ quota }: { quota: WikiQuotaStatus }) {
    const pagesPct = Math.min(1, Math.max(0, quota.pages.usage_pct || 0))
    const linksPct =
        quota.links.max_per_page > 0
            ? Math.min(1, quota.links.max_outgoing_observed / quota.links.max_per_page)
            : 0
    return (
        <div className="space-y-2 rounded-md border border-manus-border bg-manus p-2.5">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-manus-text">
                    <Gauge className="h-3 w-3 text-accent" />
                    工作区配额
                </div>
                {quota.near_limit && (
                    <span className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-500">
                        <AlertTriangle className="h-2.5 w-2.5" />
                        接近上限
                    </span>
                )}
            </div>
            <QuotaBar
                label="实体页"
                current={quota.pages.current}
                limit={quota.pages.limit}
                pct={pagesPct}
            />
            <QuotaBar
                label="单页最大出链"
                current={quota.links.max_outgoing_observed}
                limit={quota.links.max_per_page}
                pct={linksPct}
            />
        </div>
    )
}

function QuotaBar({
    label,
    current,
    limit,
    pct,
}: {
    label: string
    current: number
    limit: number
    pct: number
}) {
    const tone =
        pct >= 0.9 ? 'bg-rose-500' : pct >= 0.7 ? 'bg-amber-500' : 'bg-emerald-500'
    return (
        <div className="space-y-1">
            <div className="flex items-center justify-between text-[10px]">
                <span className="text-manus-muted">{label}</span>
                <span className="tabular-nums text-manus-subtle">
                    {current} / {limit}
                </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded bg-manus-muted/30">
                <div
                    className={cn('h-full transition-all', tone)}
                    style={{ width: `${(pct * 100).toFixed(1)}%` }}
                />
            </div>
        </div>
    )
}

function SummaryCell({
    label,
    value,
    accent,
    icon,
}: {
    label: string
    value: string
    accent?: 'good' | 'warn'
    icon?: React.ReactNode
}) {
    return (
        <div className="rounded-md border border-manus-border bg-manus p-2">
            <div className="text-[10px] text-manus-muted uppercase tracking-wide flex items-center gap-1">
                {icon}
                {label}
            </div>
            <div
                className={cn(
                    'text-lg font-bold tabular-nums mt-0.5',
                    accent === 'good' && 'text-emerald-500',
                    accent === 'warn' && 'text-amber-500',
                    !accent && 'text-manus-text',
                )}
            >
                {value}
            </div>
        </div>
    )
}
