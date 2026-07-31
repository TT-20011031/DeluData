/**
 * SQL 示例列表组件
 * 
 * [优化] 卡片式布局，SQL 默认折叠，点击展开
 */
import { useState } from 'react'
import { Edit, Trash2, CheckSquare, Square, FileQuestion, ChevronDown, ChevronRight, Code2, Database } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import type { SqlExample } from '@/types/extendConfig'

interface SqlExampleListProps {
    /** 示例列表 */
    examples: SqlExample[]
    /** 选中的 ID 集合 */
    selectedIds: Set<number>
    /** 切换选中回调 */
    onToggleSelect: (id: number) => void
    /** 编辑回调 */
    onEdit: (example: SqlExample) => void
    /** 删除回调 */
    onDelete: (id: number) => void
    /** 切换启用状态回调 */
    onToggleActive: (example: SqlExample) => void
    /** 是否按分组筛选 */
    isFiltered: boolean
}

export function SqlExampleList({
    examples,
    selectedIds,
    onToggleSelect,
    onEdit,
    onDelete,
    onToggleActive,
    isFiltered,
}: SqlExampleListProps) {
    // 展开状态管理
    const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())

    const toggleExpand = (id: number) => {
        setExpandedIds(prev => {
            const next = new Set(prev)
            if (next.has(id)) {
                next.delete(id)
            } else {
                next.add(id)
            }
            return next
        })
    }

    // 全部展开/折叠
    const expandAll = () => setExpandedIds(new Set(examples.map(e => e.id)))
    const collapseAll = () => setExpandedIds(new Set())

    if (examples.length === 0) {
        return (
            <div className="text-center py-12 text-manus-muted">
                <FileQuestion className="h-12 w-12 mx-auto mb-4 opacity-50" />
                <p>{isFiltered ? '该分组暂无示例' : '暂无 SQL 示例'}</p>
            </div>
        )
    }

    return (
        <div className="flex flex-col gap-3 max-h-[600px]">
            {/* 展开/折叠控制 */}
            <div className="flex items-center justify-end gap-2 text-xs shrink-0">
                <button 
                    onClick={expandAll}
                    className="text-manus-muted hover:text-accent transition-colors"
                >
                    全部展开
                </button>
                <span className="text-manus-border">|</span>
                <button 
                    onClick={collapseAll}
                    className="text-manus-muted hover:text-accent transition-colors"
                >
                    全部折叠
                </button>
            </div>

            {/* 可滚动列表 */}
            <div className="overflow-y-auto pr-2 -mr-2 space-y-3">
                {examples.map((example) => {
                    const isExpanded = expandedIds.has(example.id)
                    const isSelected = selectedIds.has(example.id)
                    
                    return (
                        <div
                            key={example.id}
                            className={cn(
                                "rounded-xl border transition-all duration-200",
                                isSelected
                                    ? "bg-accent/5 border-accent shadow-sm shadow-accent/10"
                                    : example.is_active
                                        ? "bg-manus-tertiary border-manus-border hover:border-manus-muted"
                                        : "bg-manus border-manus-border/50 opacity-50"
                            )}
                        >
                            {/* 卡片头部 - 始终可见 */}
                            <div className="flex items-center gap-3 p-3">
                                {/* 复选框 */}
                                <button
                                    onClick={(e) => {
                                        e.stopPropagation()
                                        onToggleSelect(example.id)
                                    }}
                                    className="shrink-0"
                                >
                                    {isSelected ? (
                                        <CheckSquare className="h-5 w-5 text-accent" />
                                    ) : (
                                        <Square className="h-5 w-5 text-manus-muted hover:text-manus-text transition-colors" />
                                    )}
                                </button>

                                {/* 问题标题 - 点击展开 SQL */}
                                <button
                                    onClick={() => toggleExpand(example.id)}
                                    className="flex-1 flex items-center gap-2 text-left group min-w-0"
                                >
                                    {isExpanded ? (
                                        <ChevronDown className="h-4 w-4 text-accent shrink-0" />
                                    ) : (
                                        <ChevronRight className="h-4 w-4 text-manus-muted group-hover:text-accent shrink-0 transition-colors" />
                                    )}
                                    <Database className="h-4 w-4 text-blue-400 shrink-0" />
                                    <span className={cn(
                                        "font-medium truncate transition-colors",
                                        isExpanded ? "text-accent" : "text-manus-text group-hover:text-accent"
                                    )}>
                                        {example.question}
                                    </span>
                                </button>

                                {/* 操作按钮 */}
                                <div className="flex items-center gap-1 shrink-0">
                                    <Switch
                                        checked={example.is_active}
                                        onCheckedChange={() => onToggleActive(example)}
                                        className="scale-90"
                                    />
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        onClick={() => onEdit(example)}
                                        className="h-8 w-8 text-manus-muted hover:text-accent hover:bg-accent/10"
                                    >
                                        <Edit className="h-4 w-4" />
                                    </Button>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        onClick={() => onDelete(example.id)}
                                        className="h-8 w-8 text-manus-muted hover:text-red-500 hover:bg-red-500/10"
                                    >
                                        <Trash2 className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>

                            {/* SQL 代码块 - 可折叠 */}
                            {isExpanded && (
                                <div className="px-3 pb-3 pt-0">
                                    <div className="bg-[#1a1a2e] rounded-lg border border-manus-border overflow-hidden">
                                        <div className="flex items-center justify-between px-3 py-1.5 bg-manus-border/30 border-b border-manus-border">
                                            <span className="text-xs text-manus-muted flex items-center gap-1.5">
                                                <Code2 className="h-3 w-3" />
                                                SQL
                                            </span>
                                            {example.tables && (
                                                <span className="text-xs text-blue-400/70">
                                                    表: {example.tables}
                                                </span>
                                            )}
                                        </div>
                                        <pre className="p-3 text-sm text-green-400/90 overflow-x-auto font-mono leading-relaxed">
                                            <code>{example.sql}</code>
                                        </pre>
                                    </div>
                                    {example.description && (
                                        <p className="mt-2 text-xs text-manus-muted pl-1">
                                            💡 {example.description}
                                        </p>
                                    )}
                                </div>
                            )}
                        </div>
                    )
                })}
            </div>
        </div>
    )
}
