/**
 * [M3.5] WikiScopePanel — KnowledgeScopePicker 的 Wiki 范围 Tab。
 *
 * 提供两层选择：
 * - 上：Wiki 域 chips（多选 / 全选 / 清空）
 * - 下：Wiki 实体页（按关键字 / 当前选中域过滤后的列表多选）
 *
 * 通过受控 props 与父组件 draft 同步：父侧负责 apply 与 reset。
 */
import { useEffect, useMemo, useState } from 'react'
import { Check, FileText, Loader2, Search, Tag } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Input } from '@/components/ui/input'
import { wikiService } from '@/services/wikiService'
import type { WikiDomainBucket } from '@/services/wikiService'
import type { WikiPageSummary } from '@/types/wiki'
import { cn } from '@/lib/utils'

interface WikiScopePanelProps {
    domains: string[]
    wikiSlugs: string[]
    onChangeDomains: (domains: string[]) => void
    onChangeWikiSlugs: (slugs: string[]) => void
    /** 触发时机：父 Dialog open 切换为 true 时进入 visible，false 时卸载请求。 */
    visible: boolean
}

const SEARCH_DEBOUNCE_MS = 250
const MAX_PAGES = 200

export function WikiScopePanel({
    domains,
    wikiSlugs,
    onChangeDomains,
    onChangeWikiSlugs,
    visible,
}: WikiScopePanelProps) {
    const [domainBuckets, setDomainBuckets] = useState<WikiDomainBucket[]>([])
    const [domainsLoading, setDomainsLoading] = useState(false)
    const [pageList, setPageList] = useState<WikiPageSummary[]>([])
    const [pagesLoading, setPagesLoading] = useState(false)
    const [pageError, setPageError] = useState<string | null>(null)
    const [keyword, setKeyword] = useState('')
    const [debouncedKeyword, setDebouncedKeyword] = useState('')

    // ===== 加载 Wiki 域 =====
    useEffect(() => {
        if (!visible) return
        let mounted = true
        setDomainsLoading(true)
        wikiService.listDomains(3)
            .then((resp) => {
                if (mounted) setDomainBuckets(resp.items || [])
            })
            .catch(() => {
                if (mounted) setDomainBuckets([])
            })
            .finally(() => {
                if (mounted) setDomainsLoading(false)
            })
        return () => {
            mounted = false
        }
    }, [visible])

    // ===== keyword 防抖 =====
    useEffect(() => {
        const timer = window.setTimeout(() => {
            setDebouncedKeyword(keyword.trim())
        }, SEARCH_DEBOUNCE_MS)
        return () => window.clearTimeout(timer)
    }, [keyword])

    // ===== 加载实体页（按选中域 / keyword 过滤）=====
    const selectedDomainKey = useMemo(
        () => [...domains].sort().join(','),
        [domains],
    )

    useEffect(() => {
        if (!visible) return
        let mounted = true
        const load = async () => {
            setPagesLoading(true)
            setPageError(null)
            try {
                // 仅传一个 domain（后端单参数）；多选时取首个；其他域结果在前端拼接
                if (domains.length === 0) {
                    const resp = await wikiService.listPages({
                        keyword: debouncedKeyword || undefined,
                        limit: MAX_PAGES,
                    })
                    if (mounted) setPageList(resp.items || [])
                } else {
                    const all: WikiPageSummary[] = []
                    const seen = new Set<string>()
                    for (const d of domains) {
                        const resp = await wikiService.listPages({
                            domain: d,
                            keyword: debouncedKeyword || undefined,
                            limit: MAX_PAGES,
                        })
                        for (const item of resp.items || []) {
                            if (seen.has(item.slug)) continue
                            seen.add(item.slug)
                            all.push(item)
                        }
                    }
                    if (mounted) setPageList(all)
                }
            } catch {
                if (mounted) {
                    setPageList([])
                    setPageError('加载实体页失败，请稍后重试。')
                }
            } finally {
                if (mounted) setPagesLoading(false)
            }
        }
        load()
        return () => {
            mounted = false
        }
    }, [visible, debouncedKeyword, selectedDomainKey, domains])

    const toggleDomain = (domain: string) => {
        if (domains.includes(domain)) {
            onChangeDomains(domains.filter((d) => d !== domain))
        } else {
            onChangeDomains([...domains, domain])
        }
    }

    const toggleWikiSlug = (slug: string) => {
        if (wikiSlugs.includes(slug)) {
            onChangeWikiSlugs(wikiSlugs.filter((s) => s !== slug))
        } else {
            onChangeWikiSlugs([...wikiSlugs, slug])
        }
    }

    const selectAllDomains = () => {
        onChangeDomains(domainBuckets.map((b) => b.domain))
    }

    const clearAll = () => {
        onChangeDomains([])
        onChangeWikiSlugs([])
    }

    return (
        <div className="space-y-3">
            <div className="rounded-md border border-manus-border p-3 space-y-2">
                <div className="flex items-center justify-between">
                    <div className="text-xs text-manus-subtle flex items-center gap-1">
                        <Tag className="h-3.5 w-3.5" />
                        Wiki 域（可多选）
                    </div>
                    <div className="flex items-center gap-1">
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-6 px-2 text-xs"
                            onClick={selectAllDomains}
                            disabled={domainBuckets.length === 0}
                        >
                            全选
                        </Button>
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-6 px-2 text-xs"
                            onClick={clearAll}
                            disabled={domains.length === 0 && wikiSlugs.length === 0}
                        >
                            清空
                        </Button>
                    </div>
                </div>

                {domainsLoading ? (
                    <div className="text-xs text-manus-subtle flex items-center gap-1.5">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        加载 Wiki 域中...
                    </div>
                ) : domainBuckets.length === 0 ? (
                    <div className="text-xs text-manus-subtle">
                        当前工作区暂无 Wiki 实体页。可在管理端"知识库 → Wiki 视图"触发编译。
                    </div>
                ) : (
                    <div className="flex flex-wrap gap-2">
                        {domainBuckets.map((bucket) => {
                            const active = domains.includes(bucket.domain)
                            const tooltip = bucket.sample_titles.length
                                ? bucket.sample_titles.slice(0, 3).join(' / ')
                                : undefined
                            return (
                                <button
                                    key={bucket.domain}
                                    type="button"
                                    title={tooltip}
                                    onClick={() => toggleDomain(bucket.domain)}
                                    className={cn(
                                        'inline-flex items-center gap-1 px-2.5 py-1 rounded-md border text-xs transition-colors',
                                        active
                                            ? 'border-accent text-accent bg-accent/10'
                                            : 'border-manus-border text-manus-subtle hover:text-manus-text hover:bg-manus-hover',
                                    )}
                                >
                                    <span
                                        className={cn(
                                            'h-3.5 w-3.5 rounded border flex items-center justify-center',
                                            active ? 'border-accent bg-accent text-white' : 'border-manus-muted',
                                        )}
                                    >
                                        {active && <Check className="h-2.5 w-2.5" />}
                                    </span>
                                    <span>{bucket.domain}</span>
                                    <span className="text-[10px] text-manus-muted">
                                        ({bucket.page_count})
                                    </span>
                                </button>
                            )
                        })}
                    </div>
                )}
            </div>

            <div className="rounded-md border border-manus-border bg-manus">
                <div className="px-3 pt-2 pb-1 flex items-center justify-between">
                    <div className="text-xs text-manus-subtle flex items-center gap-1">
                        <FileText className="h-3.5 w-3.5" />
                        Wiki 实体页（已选 {wikiSlugs.length}）
                    </div>
                </div>

                <div className="px-3 pb-2">
                    <div className="relative">
                        <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-manus-muted" />
                        <Input
                            value={keyword}
                            onChange={(e) => setKeyword(e.target.value)}
                            placeholder="搜索标题 / 摘要 / slug..."
                            className="h-8 pl-7 text-xs"
                        />
                    </div>
                </div>

                <ScrollArea className="h-[260px] rounded-b-md">
                    <div className="px-2 pb-2 space-y-1">
                        {pagesLoading ? (
                            <div className="h-32 flex items-center justify-center text-xs text-manus-subtle">
                                <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
                                加载实体页中...
                            </div>
                        ) : pageError ? (
                            <div className="h-32 flex items-center justify-center text-xs text-manus-subtle">
                                {pageError}
                            </div>
                        ) : pageList.length === 0 ? (
                            <div className="h-32 flex items-center justify-center text-xs text-manus-subtle">
                                暂无匹配实体页
                            </div>
                        ) : (
                            pageList.map((page) => {
                                const checked = wikiSlugs.includes(page.slug)
                                return (
                                    <label
                                        key={page.id}
                                        className={cn(
                                            'flex items-start gap-2 rounded-md border px-2 py-1.5 text-xs cursor-pointer transition-colors',
                                            checked
                                                ? 'border-sky-400/45 bg-sky-500/10'
                                                : 'border-transparent hover:border-manus-border hover:bg-manus-hover/70',
                                        )}
                                    >
                                        <Checkbox
                                            checked={checked}
                                            onCheckedChange={() => toggleWikiSlug(page.slug)}
                                            className="mt-0.5 border-manus-muted data-[state=checked]:border-sky-500 data-[state=checked]:bg-sky-500/15 data-[state=checked]:text-sky-700"
                                        />
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-1">
                                                <span className={cn(
                                                    'truncate text-sm',
                                                    checked ? 'text-sky-700 dark:text-sky-300' : 'text-manus-text',
                                                )}>
                                                    {page.title}
                                                </span>
                                                <span className="text-[10px] text-manus-muted shrink-0">
                                                    [{page.domain}]
                                                </span>
                                            </div>
                                            {page.summary && (
                                                <div className="text-[11px] text-manus-subtle line-clamp-1">
                                                    {page.summary}
                                                </div>
                                            )}
                                        </div>
                                    </label>
                                )
                            })
                        )}
                    </div>
                </ScrollArea>
            </div>
        </div>
    )
}
