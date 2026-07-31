/**
 * 候选字段列表组件
 * 
 * 用于展示空白检测结果，支持批量确认、改名和删除
 */
import { useState, useMemo } from 'react'
import { Check, X, Edit2, Sparkles, AlertCircle, Wand2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from '@/components/ui/tooltip'
import type { CandidateField, VariableItem } from '@/types/extendConfig'

interface CandidateFieldListProps {
    candidates: CandidateField[]
    onConfirm: (variables: VariableItem[]) => void
    onCancel: () => void
    isLoading?: boolean
    /** AI 优化回调 */
    onOptimize?: () => void
    /** 是否正在优化 */
    isOptimizing?: boolean
    /** 悬停高亮回调，传入 mapping_id 或 null 取消高亮 */
    onHighlight?: (mappingId: string | null) => void
}

interface EditableCandidate extends CandidateField {
    isSelected: boolean
    editedLabel: string
}

export function CandidateFieldList({
    candidates,
    onConfirm,
    onCancel,
    isLoading = false,
    onOptimize,
    isOptimizing = false,
    onHighlight
}: CandidateFieldListProps) {
    // 初始化可编辑候选列表：高置信度默认选中
    const [editableCandidates, setEditableCandidates] = useState<EditableCandidate[]>(() =>
        candidates.map(c => ({
            ...c,
            isSelected: c.confidence >= 0.8,
            editedLabel: c.label
        }))
    )

    const [editingKey, setEditingKey] = useState<string | null>(null)

    // 统计信息
    const stats = useMemo(() => {
        const selected = editableCandidates.filter(c => c.isSelected)
        return {
            total: editableCandidates.length,
            selectedCount: selected.length,
            highConfidenceCount: editableCandidates.filter(c => c.confidence >= 0.8).length
        }
    }, [editableCandidates])

    // 全选/取消全选
    const handleSelectAll = (checked: boolean | "indeterminate") => {
        if (checked === "indeterminate") return
        setEditableCandidates(prev =>
            prev.map(c => ({ ...c, isSelected: checked }))
        )
    }

    // 单个选择
    const handleSelect = (key: string, checked: boolean | "indeterminate") => {
        if (checked === "indeterminate") return
        setEditableCandidates(prev =>
            prev.map(c => c.key === key ? { ...c, isSelected: checked } : c)
        )
    }

    // 编辑标签
    const handleLabelChange = (key: string, newLabel: string) => {
        setEditableCandidates(prev =>
            prev.map(c => c.key === key ? { ...c, editedLabel: newLabel } : c)
        )
    }

    // 确认生成变量
    const handleConfirm = () => {
        const selectedCandidates = editableCandidates.filter(c => c.isSelected)
        const variables: VariableItem[] = selectedCandidates.map(c => ({
            name: c.editedLabel || c.label,
            type: c.type,
            desc: c.context || '',
            exampleValue: '',
            location: c.location
        }))
        onConfirm(variables)
    }

    // 置信度徽章颜色
    const getConfidenceBadge = (confidence: number) => {
        if (confidence >= 0.9) {
            return <Badge variant="default" className="bg-emerald-500">高置信</Badge>
        } else if (confidence >= 0.8) {
            return <Badge variant="default" className="bg-blue-500">可信</Badge>
        } else if (confidence >= 0.6) {
            return <Badge variant="secondary">中等</Badge>
        } else {
            return <Badge variant="outline" className="text-amber-600">低置信</Badge>
        }
    }

    // 来源标签
    const getSourceLabel = (source: string) => {
        const labels: Record<string, string> = {
            'underline_chars': '下划线',
            'bracket_blank': '括号',
            'run_underline': '下划线样式',
            'paragraph_border': '边框',
            'empty_cell_with_label': '空单元格+标签',
            'empty_cell': '空单元格',
        }
        return labels[source] || source
    }

    return (
        <div
            className="flex flex-col h-full bg-white text-gray-900"
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
            onMouseUp={(e) => e.stopPropagation()}
        >
            {/* 头部统计 */}
            <div className="flex items-center justify-between p-4 border-b border-gray-200 bg-gray-50">
                <div className="flex items-center gap-3">
                    <Sparkles className="h-5 w-5 text-primary" />
                    <div>
                        <h3 className="font-semibold text-gray-900">检测到 {stats.total} 个候选字段</h3>
                        <p className="text-sm text-gray-500">
                            已选中 {stats.selectedCount} 个，高置信度 {stats.highConfidenceCount} 个
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <Checkbox
                        id="select-all"
                        checked={stats.selectedCount === stats.total && stats.total > 0}
                        onCheckedChange={(checked) => handleSelectAll(checked === true)}
                    />
                    <label htmlFor="select-all" className="text-sm cursor-pointer text-gray-700">
                        全选
                    </label>
                </div>
            </div>

            {/* 候选列表 - 双列网格布局 */}
            <ScrollArea className="flex-1 px-4">
                <div className="py-2 grid grid-cols-2 gap-3">
                    {editableCandidates.map((candidate) => (
                        <div
                            key={candidate.key}
                            className={`
                                flex items-start gap-3 p-3 rounded-lg border transition-all cursor-pointer
                                ${candidate.isSelected
                                    ? 'bg-blue-50 border-blue-200 shadow-sm'
                                    : 'bg-white border-gray-200 hover:bg-gray-50 hover:border-gray-300'}
                            `}
                            onMouseEnter={() => onHighlight?.(candidate.location.mapping_id ?? null)}
                            onMouseLeave={() => onHighlight?.(null)}
                            onClick={() => {
                                // 点击定位：触发高亮并保持
                                onHighlight?.(candidate.location.mapping_id ?? null)
                            }}
                        >
                            {/* 选择框 */}
                            <Checkbox
                                checked={candidate.isSelected}
                                onCheckedChange={(checked) =>
                                    handleSelect(candidate.key, checked === true)
                                }
                                onClick={(e) => e.stopPropagation()}
                            />

                            {/* 标签编辑 */}
                            <div className="flex-1 min-w-0">
                                {editingKey === candidate.key ? (
                                    <Input
                                        value={candidate.editedLabel}
                                        onChange={(e) =>
                                            handleLabelChange(candidate.key, e.target.value)
                                        }
                                        onBlur={() => setEditingKey(null)}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter') setEditingKey(null)
                                        }}
                                        autoFocus
                                        className="h-8"
                                        onClick={(e) => e.stopPropagation()}
                                    />
                                ) : (
                                    <div className="flex items-center gap-2">
                                        <span className="font-medium truncate text-gray-900 text-sm">
                                            {candidate.editedLabel || candidate.label}
                                        </span>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            className="h-5 w-5 shrink-0"
                                            onClick={(e) => {
                                                e.stopPropagation()
                                                setEditingKey(candidate.key)
                                            }}
                                        >
                                            <Edit2 className="h-3 w-3" />
                                        </Button>
                                    </div>
                                )}

                                {/* 上下文提示 */}
                                {candidate.context && (
                                    <TooltipProvider>
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <p className="text-xs text-gray-500 truncate mt-1 cursor-help">
                                                    {candidate.context}
                                                </p>
                                            </TooltipTrigger>
                                            <TooltipContent>
                                                <p className="max-w-xs">{candidate.context}</p>
                                            </TooltipContent>
                                        </Tooltip>
                                    </TooltipProvider>
                                )}

                                {/* 标签 - 移到下方 */}
                                <div className="flex items-center gap-1 mt-2">
                                    <Badge variant="outline" className="text-xs py-0">
                                        {getSourceLabel(candidate.source)}
                                    </Badge>
                                    {getConfidenceBadge(candidate.confidence)}
                                </div>
                            </div>
                        </div>
                    ))}

                    {editableCandidates.length === 0 && (
                        <div className="col-span-2 flex flex-col items-center justify-center py-12 text-gray-400">
                            <AlertCircle className="h-12 w-12 mb-4 opacity-50" />
                            <p>未检测到空白字段</p>
                            <p className="text-sm">请确认模板包含下划线、空表格单元格或括号空白</p>
                        </div>
                    )}
                </div>
            </ScrollArea>

            {/* 底部操作 */}
            <div className="flex items-center justify-between p-4 border-t border-gray-200 bg-gray-50">
                <div>
                    {onOptimize && (
                        <Button
                            variant="outline"
                            onClick={onOptimize}
                            disabled={isOptimizing || isLoading || stats.total === 0}
                            className="text-primary border-primary/30 hover:bg-primary/10"
                        >
                            <Wand2 className={`h-4 w-4 mr-2 ${isOptimizing ? 'animate-spin' : ''}`} />
                            {isOptimizing ? 'AI 优化中...' : 'AI 优化命名'}
                        </Button>
                    )}
                </div>
                <div className="flex items-center gap-3">
                    <Button variant="outline" onClick={onCancel} disabled={isLoading}>
                        <X className="h-4 w-4 mr-2" />
                        取消
                    </Button>
                    <Button
                        onClick={handleConfirm}
                        disabled={stats.selectedCount === 0 || isLoading}
                    >
                        <Check className="h-4 w-4 mr-2" />
                        确认添加 {stats.selectedCount} 个变量
                    </Button>
                </div>
            </div>
        </div>
    )
}
