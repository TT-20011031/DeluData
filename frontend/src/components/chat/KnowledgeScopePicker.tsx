import { useEffect, useMemo, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, Database, FileText, Folder, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { Checkbox } from '@/components/ui/checkbox'
import { knowledgeService } from '@/services/knowledgeService'
import type { DepartmentOption, FolderNode } from '@/types/knowledge'
import type { DocScope } from '@/types/docScope'
import { compactDocScope, normalizeDocScope, summarizeDocScope } from '@/types/docScope'
import { cn } from '@/lib/utils'

interface KnowledgeScopePickerProps {
    value: DocScope | null
    onChange: (scope: DocScope | null) => void
    disabled?: boolean
    modal?: boolean
}

type ScopeVisibility = 'public' | 'private' | 'dept'

const VISIBILITY_OPTIONS: Array<{ key: ScopeVisibility; label: string }> = [
    { key: 'public', label: '全局' },
    { key: 'private', label: '个人' },
    { key: 'dept', label: '部门' },
]

const DEFAULT_VISIBILITY_STATE: Record<ScopeVisibility, boolean> = {
    public: true,
    private: true,
    dept: true,
}

interface PathItem {
    id: string
    name: string
}

function visibilityStateFromScope(scope: DocScope): Record<ScopeVisibility, boolean> {
    if (!scope.visibilities.length) return DEFAULT_VISIBILITY_STATE
    return {
        public: scope.visibilities.includes('public'),
        private: scope.visibilities.includes('private'),
        dept: scope.visibilities.includes('dept'),
    }
}

function deptIdsFromScope(scope: DocScope): number[] {
    return scope.dept_ids
        .map((id) => Number(id))
        .filter((id) => Number.isFinite(id) && id > 0)
}

export function KnowledgeScopePicker({
    value,
    onChange,
    disabled = false,
    modal = true,
}: KnowledgeScopePickerProps) {
    const [open, setOpen] = useState(false)
    const [loading, setLoading] = useState(false)
    const [tree, setTree] = useState<FolderNode[]>([])
    const [departments, setDepartments] = useState<DepartmentOption[]>([])
    const [departmentsLoading, setDepartmentsLoading] = useState(false)
    const [visibilityState, setVisibilityState] = useState<Record<ScopeVisibility, boolean>>(DEFAULT_VISIBILITY_STATE)
    const [deptIds, setDeptIds] = useState<number[]>([])
    const [path, setPath] = useState<PathItem[]>([])
    const [loadError, setLoadError] = useState<string | null>(null)
    const [draft, setDraft] = useState<DocScope>(normalizeDocScope(value))

    useEffect(() => {
        if (!open) return
        const normalized = normalizeDocScope(value)
        setDraft(normalized)
        setVisibilityState(visibilityStateFromScope(normalized))
        setDeptIds(deptIdsFromScope(normalized))
        setPath([])
    }, [open, value])

    useEffect(() => {
        if (!open) return
        let mounted = true
        const loadDepartments = async () => {
            setDepartmentsLoading(true)
            try {
                const list = await knowledgeService.fetchDepartments()
                if (mounted) setDepartments(list)
            } catch {
                if (mounted) setDepartments([])
            } finally {
                if (mounted) setDepartmentsLoading(false)
            }
        }
        loadDepartments()
        return () => {
            mounted = false
        }
    }, [open])

    const selectedVisibilities = useMemo(
        () => VISIBILITY_OPTIONS.filter((item) => visibilityState[item.key]).map((item) => item.key),
        [visibilityState]
    )
    const selectedVisibilityKey = useMemo(() => selectedVisibilities.join(','), [selectedVisibilities])
    const selectedDeptKey = useMemo(() => [...deptIds].sort((a, b) => a - b).join(','), [deptIds])
    const summary = useMemo(() => summarizeDocScope(value), [value])

    useEffect(() => {
        if (!open) return
        let mounted = true
        const load = async () => {
            if (selectedVisibilities.length === 0) {
                setTree([])
                setLoadError(null)
                return
            }
            setLoading(true)
            setLoadError(null)
            try {
                const payload: { visibilities?: string[]; dept_ids?: number[] } = {
                    visibilities: selectedVisibilities,
                }
                if (visibilityState.dept && deptIds.length > 0) {
                    payload.dept_ids = deptIds
                }
                const data = await knowledgeService.fetchVisibleStructure(payload)
                if (mounted) setTree(Array.isArray(data) ? data : [])
            } catch {
                if (mounted) {
                    setTree([])
                    setLoadError('加载范围目录失败，请稍后重试。')
                }
            } finally {
                if (mounted) setLoading(false)
            }
        }
        load()
        return () => {
            mounted = false
        }
    }, [open, selectedVisibilityKey, selectedDeptKey, visibilityState.dept, selectedVisibilities, deptIds])

    useEffect(() => {
        if (!open) return
        setPath((prev) => {
            let nodes = tree
            const next: PathItem[] = []
            for (const item of prev) {
                const match = nodes.find((node) => node.type === 'folder' && node.id === item.id)
                if (!match) break
                next.push({ id: match.id, name: match.name })
                nodes = Array.isArray(match.children) ? match.children : []
            }
            return next
        })
    }, [open, tree])

    const toggleItem = (id: string, type: 'folder' | 'file') => {
        setDraft((prev) => {
            const next = normalizeDocScope(prev)
            const target = type === 'folder' ? next.folder_ids : next.file_ids
            const idx = target.indexOf(id)
            if (idx >= 0) target.splice(idx, 1)
            else target.push(id)
            return next
        })
    }

    const toggleVisibility = (key: ScopeVisibility) => {
        setVisibilityState((prev) => ({ ...prev, [key]: !prev[key] }))
    }

    const toggleDept = (deptId: number) => {
        setDeptIds((prev) => (
            prev.includes(deptId) ? prev.filter((id) => id !== deptId) : [...prev, deptId]
        ))
    }

    const currentNodes = useMemo(() => {
        let nodes = tree
        for (const item of path) {
            const folderNode = nodes.find((node) => node.type === 'folder' && node.id === item.id)
            if (!folderNode) return []
            nodes = Array.isArray(folderNode.children) ? folderNode.children : []
        }
        return nodes
    }, [tree, path])

    const folderNodes = useMemo(
        () => currentNodes.filter((node) => node.type === 'folder').sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')),
        [currentNodes]
    )

    const fileNodes = useMemo(
        () => currentNodes.filter((node) => node.type === 'file').sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')),
        [currentNodes]
    )

    const enterFolder = (node: FolderNode) => {
        if (node.type !== 'folder') return
        setPath((prev) => [...prev, { id: node.id, name: node.name }])
    }

    const goBack = () => {
        setPath((prev) => prev.slice(0, -1))
    }

    const jumpToPath = (index: number) => {
        setPath((prev) => prev.slice(0, index + 1))
    }

    const applyDraft = () => {
        const next = normalizeDocScope(draft)
        next.visibilities = selectedVisibilities.length === VISIBILITY_OPTIONS.length ? [] : selectedVisibilities
        next.dept_ids = visibilityState.dept ? deptIds.map(String) : []
        next.domains = []
        next.wiki_slugs = []
        onChange(compactDocScope(next))
        setOpen(false)
    }

    const clearDraft = () => {
        setDraft(normalizeDocScope(null))
        setVisibilityState(DEFAULT_VISIBILITY_STATE)
        setDeptIds([])
    }

    const currentDirectorySelectableCount = folderNodes.length + fileNodes.length
    const isCurrentDirectoryAllSelected = useMemo(() => {
        if (currentDirectorySelectableCount === 0) return false
        return folderNodes.every((node) => draft.folder_ids.includes(node.id))
            && fileNodes.every((node) => draft.file_ids.includes(node.id))
    }, [currentDirectorySelectableCount, folderNodes, fileNodes, draft.folder_ids, draft.file_ids])

    const selectAllCurrentDirectory = () => {
        setDraft((prev) => {
            const next = normalizeDocScope(prev)
            next.folder_ids = Array.from(new Set([...next.folder_ids, ...folderNodes.map((node) => node.id)]))
            next.file_ids = Array.from(new Set([...next.file_ids, ...fileNodes.map((node) => node.id)]))
            return next
        })
    }

    const renderNodeRow = (node: FolderNode, nodeType: 'folder' | 'file') => {
        const checked = nodeType === 'folder'
            ? draft.folder_ids.includes(node.id)
            : draft.file_ids.includes(node.id)
        const canEnter = nodeType === 'folder'

        return (
            <div
                key={node.id}
                className={cn(
                    'group flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm transition-colors',
                    checked
                        ? 'border-sky-400/45 bg-sky-500/10'
                        : 'border-transparent hover:border-manus-border hover:bg-manus-hover/70'
                )}
            >
                <Checkbox
                    checked={checked}
                    onCheckedChange={() => toggleItem(node.id, nodeType)}
                    aria-label={`选择${nodeType === 'folder' ? '文件夹' : '文件'} ${node.name}`}
                    className="border-manus-muted data-[state=checked]:border-sky-500 data-[state=checked]:bg-sky-500/15 data-[state=checked]:text-sky-700 dark:data-[state=checked]:text-sky-300"
                />
                {nodeType === 'folder' ? (
                    <Folder className="h-4 w-4 text-yellow-500 shrink-0" />
                ) : (
                    <FileText className="h-4 w-4 text-blue-400 shrink-0" />
                )}
                <button
                    type="button"
                    className={cn(
                        'flex-1 min-w-0 text-left',
                        checked ? 'text-sky-700 dark:text-sky-300' : 'text-manus-text hover:text-manus-text'
                    )}
                    onClick={() => (canEnter ? enterFolder(node) : toggleItem(node.id, nodeType))}
                >
                    <span className="truncate block">{node.name}</span>
                </button>
                {canEnter && (
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 text-manus-subtle hover:text-manus-text"
                        onClick={() => enterFolder(node)}
                        title="进入文件夹"
                    >
                        <ChevronRight className="h-4 w-4" />
                    </Button>
                )}
            </div>
        )
    }

    return (
        <>
            <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={disabled}
                onClick={() => setOpen(true)}
                className="h-7 px-2 rounded-md text-manus-subtle hover:text-manus-text hover:bg-manus-hover"
                title={summary}
            >
                <Database className="h-3.5 w-3.5 mr-1.5" />
                <span className="text-xs truncate max-w-[180px]">{summary}</span>
            </Button>

            <Dialog open={open} onOpenChange={setOpen} modal={modal}>
                <DialogContent className="bg-manus-secondary border-manus-border max-w-3xl">
                    <DialogHeader>
                        <DialogTitle className="text-manus-text">知识库范围</DialogTitle>
                    </DialogHeader>

                    <div className="space-y-3">
                        <div className="rounded-md border border-manus-border p-3 space-y-3">
                            <div className="text-xs text-manus-subtle">可见范围（同时作用于 RAG 与 Wiki）</div>
                            <div className="flex flex-wrap gap-2">
                                {VISIBILITY_OPTIONS.map((item) => {
                                    const active = visibilityState[item.key]
                                    return (
                                        <button
                                            key={item.key}
                                            type="button"
                                            className={cn(
                                                'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-xs transition-colors',
                                                active
                                                    ? 'border-accent text-accent bg-accent/10'
                                                    : 'border-manus-border text-manus-subtle hover:text-manus-text hover:bg-manus-hover'
                                            )}
                                            onClick={() => toggleVisibility(item.key)}
                                        >
                                            <span
                                                className={cn(
                                                    'h-3.5 w-3.5 rounded border flex items-center justify-center',
                                                    active ? 'border-accent bg-accent text-white' : 'border-manus-muted'
                                                )}
                                            >
                                                {active && <Check className="h-2.5 w-2.5" />}
                                            </span>
                                            {item.label}
                                        </button>
                                    )
                                })}
                            </div>

                            {visibilityState.dept && (
                                <div className="space-y-2">
                                    <span className="text-xs text-manus-subtle">部门筛选（可多选）</span>
                                    {departmentsLoading ? (
                                        <div className="text-xs text-manus-subtle flex items-center gap-1.5">
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                            加载部门中...
                                        </div>
                                    ) : departments.length === 0 ? (
                                        <div className="text-xs text-manus-subtle">未获取到可选部门</div>
                                    ) : (
                                        <div className="flex flex-wrap gap-2 max-h-[96px] overflow-y-auto pr-1">
                                            {departments.map((dept) => {
                                                const active = deptIds.includes(dept.id)
                                                return (
                                                    <button
                                                        key={dept.id}
                                                        type="button"
                                                        className={cn(
                                                            'px-2.5 py-1 rounded-md border text-xs transition-colors',
                                                            active
                                                                ? 'border-accent text-accent bg-accent/10'
                                                                : 'border-manus-border text-manus-subtle hover:text-manus-text hover:bg-manus-hover'
                                                        )}
                                                        onClick={() => toggleDept(dept.id)}
                                                        title={dept.name}
                                                    >
                                                        {dept.name}
                                                    </button>
                                                )
                                            })}
                                        </div>
                                    )}
                                    <div className="text-[11px] text-manus-subtle">
                                        未选部门时，默认包含你有权限访问的全部部门内容。
                                    </div>
                                </div>
                            )}
                        </div>

                        <div className="flex items-center justify-between rounded-md border border-manus-border px-3 py-2">
                            <span className="text-sm text-manus-text">包含子文件夹</span>
                            <Switch
                                checked={draft.include_subfolders}
                                onCheckedChange={(checked) => {
                                    setDraft((prev) => ({ ...prev, include_subfolders: checked }))
                                }}
                            />
                        </div>
                    </div>

                    <ScrollArea className="h-[360px] rounded-md border border-manus-border bg-manus">
                        <div className="p-2 space-y-3">
                            <div className="flex items-center gap-1 text-xs text-manus-subtle">
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6"
                                    disabled={path.length === 0}
                                    onClick={goBack}
                                    title="返回上一级"
                                >
                                    <ChevronLeft className="h-4 w-4" />
                                </Button>
                                <button
                                    type="button"
                                    className={cn(
                                        'px-1.5 py-0.5 rounded hover:bg-manus-hover',
                                        path.length === 0 ? 'text-manus-text' : 'text-manus-subtle'
                                    )}
                                    onClick={() => setPath([])}
                                >
                                    根目录
                                </button>
                                {path.map((item, index) => (
                                    <div key={item.id} className="flex items-center gap-1">
                                        <ChevronRight className="h-3 w-3 text-manus-muted" />
                                        <button
                                            type="button"
                                            className={cn(
                                                'px-1.5 py-0.5 rounded max-w-[180px] truncate hover:bg-manus-hover',
                                                index === path.length - 1 ? 'text-manus-text' : 'text-manus-subtle'
                                            )}
                                            onClick={() => jumpToPath(index)}
                                            title={item.name}
                                        >
                                            {item.name}
                                        </button>
                                    </div>
                                ))}
                            </div>

                            {loading ? (
                                <div className="h-40 flex items-center justify-center text-sm text-manus-subtle">
                                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                    加载目录中...
                                </div>
                            ) : loadError ? (
                                <div className="h-40 flex items-center justify-center text-sm text-manus-subtle">
                                    {loadError}
                                </div>
                            ) : selectedVisibilities.length === 0 ? (
                                <div className="h-40 flex items-center justify-center text-sm text-manus-subtle">
                                    请先勾选至少一个可见范围
                                </div>
                            ) : currentNodes.length === 0 ? (
                                <div className="h-40 flex items-center justify-center text-sm text-manus-subtle">
                                    当前目录暂无内容
                                </div>
                            ) : (
                                <div className="space-y-3">
                                    <div className="space-y-1">
                                        <div className="px-1 text-xs text-manus-subtle">
                                            文件夹 ({folderNodes.length})
                                        </div>
                                        {folderNodes.length === 0 ? (
                                            <div className="px-2 py-2 text-xs text-manus-subtle">暂无文件夹</div>
                                        ) : (
                                            folderNodes.map((node) => renderNodeRow(node, 'folder'))
                                        )}
                                    </div>

                                    <div className="space-y-1">
                                        <div className="px-1 text-xs text-manus-subtle">
                                            文件 ({fileNodes.length})
                                        </div>
                                        {fileNodes.length === 0 ? (
                                            <div className="px-2 py-2 text-xs text-manus-subtle">暂无文件</div>
                                        ) : (
                                            fileNodes.map((node) => renderNodeRow(node, 'file'))
                                        )}
                                    </div>
                                </div>
                            )}
                        </div>
                    </ScrollArea>

                    <div className="text-xs text-manus-subtle">
                        当前选择: {summarizeDocScope({
                            ...draft,
                            visibilities: selectedVisibilities.length === VISIBILITY_OPTIONS.length ? [] : selectedVisibilities,
                            dept_ids: visibilityState.dept ? deptIds.map(String) : [],
                            domains: [],
                            wiki_slugs: [],
                        })}
                    </div>

                    <DialogFooter className="!mt-1 !flex-row !items-center !justify-between !space-x-0 border-t border-manus-border pt-3">
                        <div className="flex items-center gap-2">
                            <Button
                                type="button"
                                variant="outline"
                                onClick={selectAllCurrentDirectory}
                                disabled={currentDirectorySelectableCount === 0 || isCurrentDirectoryAllSelected}
                                title="全选当前目录内的文件夹和文件"
                            >
                                全选
                            </Button>
                            <Button type="button" variant="ghost" onClick={clearDraft}>
                                清空
                            </Button>
                        </div>
                        <div className="flex items-center gap-2">
                            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                                取消
                            </Button>
                            <Button type="button" onClick={applyDraft} disabled={selectedVisibilities.length === 0}>
                                应用
                            </Button>
                        </div>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    )
}
