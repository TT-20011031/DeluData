/**
 * 变量可视化编辑器 (Shadcn UI Style Refactor)
 * 
 * 采用可折叠卡片设计，提升视觉质感和空间利用率
 */
import { useState } from 'react'
import { Plus, Trash2, ChevronDown, ChevronRight, Type, Hash, Calendar, Image as ImageIcon, Table as TableIcon, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { VARIABLE_TYPES, type VariableItem } from '@/types/extendConfig'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from "@/components/ui/collapsible"

interface VariableEditorProps {
    value: VariableItem[]
    onChange: (variables: VariableItem[]) => void
    /** 模板 ID，用于调用检测 API */
    templateId?: number | null
    /** 是否支持智能识别（仅 docx 模板） */
    enableDetection?: boolean
    /** 触发智能识别 */
    onDetectRequest?: () => void
    /** 是否正在检测 */
    isDetecting?: boolean
}

const TYPE_ICONS: Record<string, any> = {
    text: Type,
    number: Hash,
    date: Calendar,
    image: ImageIcon,
    table: TableIcon,
}

export function VariableEditor({
    value,
    onChange,
    templateId,
    enableDetection = false,
    onDetectRequest,
    isDetecting = false
}: VariableEditorProps) {
    const [openStates, setOpenStates] = useState<Record<number, boolean>>({})

    const toggleOpen = (index: number) => {
        setOpenStates(prev => ({ ...prev, [index]: !prev[index] }))
    }

    const handleAdd = () => {
        const newItem = { name: '', type: 'text', desc: '', exampleValue: '' }
        const newIndex = value.length
        onChange([...value, newItem])
        // Auto open the new item
        setOpenStates(prev => ({ ...prev, [newIndex]: true }))
    }

    const handleUpdate = (index: number, field: keyof VariableItem, fieldValue: string) => {
        const newVars = [...value]
        newVars[index] = { ...newVars[index], [field]: fieldValue }
        onChange(newVars)
    }

    const handleRemove = (index: number, e: React.MouseEvent) => {
        e.stopPropagation()
        onChange(value.filter((_, i) => i !== index))
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between px-1">
                <div className="flex items-center gap-2">
                    <Label className="text-sm font-semibold text-manus-text">模板变量</Label>
                    <Badge variant="secondary" className="h-5 px-1.5 text-[10px] font-normal text-manus-muted">
                        {value.length}
                    </Badge>
                </div>
                <Button
                    onClick={handleAdd}
                    size="sm"
                    className="h-7 text-xs bg-manus-text text-manus hover:bg-manus-text/90 shadow-sm"
                >
                    <Plus className="h-3 w-3 mr-1" />
                    添加变量
                </Button>
                {enableDetection && templateId && (
                    <Button
                        onClick={onDetectRequest}
                        size="sm"
                        variant="outline"
                        disabled={isDetecting}
                        className="h-7 text-xs"
                    >
                        <Sparkles className={cn(
                            "h-3 w-3 mr-1",
                            isDetecting && "animate-pulse"
                        )} />
                        {isDetecting ? '识别中...' : '智能识别'}
                    </Button>
                )}
            </div>

            {value.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-10 px-4 border-2 border-dashed border-manus-border rounded-xl bg-manus-tertiary/20 text-center space-y-3">
                    <div className="w-10 h-10 rounded-full bg-manus-tertiary flex items-center justify-center">
                        <Type className="h-5 w-5 text-manus-muted" />
                    </div>
                    <div>
                        <p className="text-sm font-medium text-manus-text">暂无变量</p>
                        <p className="text-xs text-manus-muted mt-1 max-w-[200px] mx-auto">
                            点击右上角添加变量，或在左侧预览区选中文字自动绑定
                        </p>
                    </div>
                    <Button variant="outline" size="sm" onClick={handleAdd} className="mt-2 h-8 text-xs">
                        创建第一个变量
                    </Button>
                </div>
            ) : (
                <div className="space-y-3 max-h-[calc(100vh-280px)] overflow-y-auto custom-scrollbar pr-1 pb-10">
                    {value.map((variable, index) => {
                        const Icon = TYPE_ICONS[variable.type] || Type
                        const isOpen = openStates[index]

                        return (
                            <Collapsible
                                key={index}
                                open={isOpen}
                                onOpenChange={() => toggleOpen(index)}
                                className={cn(
                                    "group rounded-lg border border-manus-border bg-manus-elevated transition-all duration-200",
                                    isOpen ? "shadow-md ring-1 ring-manus-border" : "hover:border-accent/30 hover:shadow-sm"
                                )}
                            >
                                <div className="flex items-center gap-3 p-3">
                                    <CollapsibleTrigger asChild>
                                        <Button variant="ghost" size="icon" className="h-6 w-6 p-0 text-manus-muted hover:text-manus-text shrink-0">
                                            {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                                        </Button>
                                    </CollapsibleTrigger>

                                    <div className="flex-1 min-w-0 flex flex-col gap-0.5" onClick={() => !isOpen && toggleOpen(index)}>
                                        {isOpen ? (
                                            <Input
                                                value={variable.name}
                                                onChange={(e) => handleUpdate(index, 'name', e.target.value)}
                                                placeholder="输入变量名..."
                                                className="h-7 text-sm font-semibold bg-transparent border-transparent px-0 focus-visible:ring-0 focus-visible:border-accent/50 rounded-none border-b border-dashed border-manus-border/50 hover:border-manus-border"
                                                autoFocus
                                            />
                                        ) : (
                                            <div className="flex items-center gap-2 cursor-pointer">
                                                <span className={cn("text-sm font-medium truncate", !variable.name && "text-manus-muted italic")}>
                                                    {variable.name || "未命名变量"}
                                                </span>
                                                {variable.location && (
                                                    <span className="flex h-1.5 w-1.5 rounded-full bg-green-500 shadow-[0_0_0_2px_rgba(34,197,94,0.2)]" />
                                                )}
                                            </div>
                                        )}
                                        {!isOpen && (
                                            <div className="text-[10px] text-manus-muted truncate flex items-center gap-1.5">
                                                <Badge variant="outline" className="h-4 px-1 text-[9px] border-manus-border text-manus-muted font-normal bg-manus-tertiary/50">
                                                    {VARIABLE_TYPES.find(t => t.value === variable.type)?.label}
                                                </Badge>
                                                {variable.desc || "无描述"}
                                            </div>
                                        )}
                                    </div>

                                    <div className="flex items-center gap-1">
                                        {/* Type Selector (Miniature when closed, removed when open to avoid clutter in header) */}
                                        {!isOpen && (
                                            <Icon className="h-4 w-4 text-manus-muted opacity-50 mr-2" />
                                        )}

                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            onClick={(e) => handleRemove(index, e)}
                                            className="h-7 w-7 text-manus-muted hover:text-red-500 hover:bg-red-500/10 opacity-0 group-hover:opacity-100 transition-opacity"
                                        >
                                            <Trash2 className="h-3.5 w-3.5" />
                                        </Button>
                                    </div>
                                </div>

                                <CollapsibleContent className="px-3 pb-3 pt-0 space-y-3 animate-slide-down">
                                    <div className="grid grid-cols-2 gap-3 pt-2 border-t border-manus-border/50">
                                        <div className="space-y-1.5 col-span-2">
                                            <Label className="text-[10px] text-manus-muted uppercase font-bold tracking-wider">Type</Label>
                                            <Select
                                                value={variable.type}
                                                onValueChange={(val) => handleUpdate(index, 'type', val)}
                                            >
                                                <SelectTrigger className="h-8 bg-manus-tertiary/30 border-manus-border text-xs">
                                                    <div className="flex items-center gap-2">
                                                        <Icon className="h-3.5 w-3.5" />
                                                        <SelectValue />
                                                    </div>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {VARIABLE_TYPES.map(t => {
                                                        const TIcon = TYPE_ICONS[t.value] || Type
                                                        return (
                                                            <SelectItem key={t.value} value={t.value}>
                                                                <div className="flex items-center gap-2 text-xs">
                                                                    <TIcon className="h-3.5 w-3.5 opacity-70" />
                                                                    {t.label}
                                                                </div>
                                                            </SelectItem>
                                                        )
                                                    })}
                                                </SelectContent>
                                            </Select>
                                        </div>

                                        <div className="space-y-1.5 col-span-2">
                                            <Label className="text-[10px] text-manus-muted uppercase font-bold tracking-wider">Description</Label>
                                            <Input
                                                value={variable.desc}
                                                onChange={(e) => handleUpdate(index, 'desc', e.target.value)}
                                                placeholder="描述变量用途..."
                                                className="h-8 bg-manus-tertiary/30 border-manus-border text-xs focus:bg-manus"
                                            />
                                        </div>

                                        <div className="space-y-1.5 col-span-2">
                                            <Label className="text-[10px] text-manus-muted uppercase font-bold tracking-wider">Mock Value</Label>
                                            <div className="relative">
                                                <Input
                                                    value={variable.exampleValue}
                                                    onChange={(e) => handleUpdate(index, 'exampleValue', e.target.value)}
                                                    placeholder="用于预览的测试值..."
                                                    className="h-8 bg-manus-tertiary/30 border-manus-border text-xs focus:bg-manus pr-8 font-mono text-manus-muted"
                                                />
                                                <div className="absolute right-2 top-2 text-[10px] text-manus-muted/50 font-mono">
                                                    Az
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </CollapsibleContent>
                            </Collapsible>
                        )
                    })}
                </div>
            )}
        </div>
    )
}
