/**
 * Skill 编辑器面板 (Immersive Timeline Design)
 * 遵循 "Flow-based" 交互理念设计
 */
import { useEffect, useState, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import {
    Dialog, DialogContent,
} from '@/components/ui/dialog'
import {
    Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger,
} from '@/components/ui/sheet'
import {
    Loader2, Plus, Trash2, FileText,
    Database, FileLineChart, FileSpreadsheet, Settings2,
    ChevronDown, ChevronRight, AlertTriangle
} from 'lucide-react'
import type { SkillCreateRequest, SkillStep } from '@/services/skillService'
import { extendConfigService } from '@/services/extendConfigService'
import type { Template } from '@/types/extendConfig'
import { cn } from '@/lib/utils'
import { useWorkspaceReadiness } from '@/hooks/chat/useWorkspaceReadiness'
import { KnowledgeScopePicker } from '@/components/chat/KnowledgeScopePicker'
import type { DocScope } from '@/types/docScope'
import { normalizeDocScope, compactDocScope } from '@/types/docScope'

interface SkillEditorProps {
    open: boolean
    onClose: () => void
    form: SkillCreateRequest
    onFormChange: (form: SkillCreateRequest) => void
    onSave: () => void
    isSaving: boolean
    isEditing: boolean
}

/** 工具元数据定义 */
const TOOL_CONFIG: Record<string, { label: string, icon: React.ElementType, color: string, bg: string, border: string }> = {
    'none': { label: '无工具', icon: Settings2, color: 'text-manus-muted', bg: 'bg-manus-tertiary', border: 'border-manus-border' },
    'sql_worker': { label: 'SQL 查询', icon: Database, color: 'text-blue-400', bg: 'bg-blue-500/5', border: 'border-blue-500/20' },
    'doc_worker': { label: '知识库检索', icon: FileText, color: 'text-green-400', bg: 'bg-green-500/5', border: 'border-green-500/20' },
    'chart_worker': { label: '图表生成', icon: FileLineChart, color: 'text-purple-400', bg: 'bg-purple-500/5', border: 'border-purple-500/20' },
    'office_worker': { label: '文档处理', icon: FileSpreadsheet, color: 'text-orange-400', bg: 'bg-orange-500/5', border: 'border-orange-500/20' },
}

const TOOL_OPTIONS = Object.entries(TOOL_CONFIG).map(([value, conf]) => ({
    value,
    ...conf
}))


export function SkillEditorModal({
    open,
    onClose,
    form,
    onFormChange,
    onSave,
    isSaving,
    isEditing,
}: SkillEditorProps) {
    const { hasDb, hasKnowledge } = useWorkspaceReadiness()
    const [templates, setTemplates] = useState<Template[]>([])

    // 聚焦状态管理
    const [focusedStepIndex, setFocusedStepIndex] = useState<number | null>(null)
    // 步骤折叠状态 (默认只有最后一个展开，或者全部展开，取决于 UX 偏好，这里默认全展开但可折叠)
    const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({})

    const stepsEndRef = useRef<HTMLDivElement>(null)

    // 初始化：如果新建且无步骤，添加一个默认步骤
    useEffect(() => {
        if (open && form.steps.length === 0) {
            const timer = setTimeout(() => {
                onFormChange({ ...form, steps: [{ step: 1, action: '', tool: null }] })
                setExpandedSteps({ 0: true })
            }, 0)
            return () => clearTimeout(timer)
        }
    }, [open, form.steps.length, onFormChange])

    useEffect(() => {
        if (!open) return
        let mounted = true
        const loadTemplates = async () => {
            try {
                const data = await extendConfigService.getTemplates()
                if (mounted) setTemplates(data)
            } catch {
                if (mounted) setTemplates([])
            }
        }
        loadTemplates()
        return () => { mounted = false }
    }, [open])


    const updateStep = (index: number, field: keyof SkillStep, value: any) => {
        const newSteps = [...form.steps]
        newSteps[index] = { ...newSteps[index], [field]: value }
        onFormChange({ ...form, steps: newSteps })
    }

    const addStep = () => {
        const newStep: SkillStep = {
            step: form.steps.length + 1,
            action: '',
            tool: null,
        }
        const newIndex = form.steps.length
        onFormChange({ ...form, steps: [...form.steps, newStep] })
        setExpandedSteps(prev => ({ ...prev, [newIndex]: true }))
        setFocusedStepIndex(newIndex)
        setTimeout(() => stepsEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 100)
    }

    const removeStep = (index: number) => {
        const newSteps = form.steps.filter((_, i) => i !== index)
        newSteps.forEach((step, i) => {
            step.step = i + 1
        })
        onFormChange({ ...form, steps: newSteps })
        if (focusedStepIndex === index) setFocusedStepIndex(null)
    }

    const toggleStepExpand = (index: number) => {
        setExpandedSteps(prev => ({ ...prev, [index]: !prev[index] }))
    }

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent
                className="w-screen h-screen max-w-none m-0 p-0 rounded-none border-0 bg-manus flex flex-col focus:outline-none"
                hideCloseButton
            >
                {/* 1. Global Header */}
                <header className="flex-none h-14 border-b border-manus-border bg-manus/95 backdrop-blur z-20 px-6 flex items-center justify-between">
                    <div className="flex items-center gap-4">
                        <Button variant="ghost" size="icon" onClick={onClose} className="-ml-2 text-manus-muted hover:text-manus-text">
                            <ChevronRight className="h-5 w-5 rotate-180" /> {/* Back Icon look */}
                        </Button>
                        <div className="flex items-center gap-2">
                            <div className="font-medium text-manus-text">
                                {isEditing ? '编辑 Flow' : '新建 Flow'}
                            </div>
                            {isSaving && <Loader2 className="h-3 w-3 animate-spin text-manus-muted" />}
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <Sheet>
                            <SheetTrigger asChild>
                                <Button variant="ghost" size="sm" className="text-manus-muted hover:text-manus-text gap-2">
                                    <Settings2 className="h-4 w-4" />
                                    设置
                                </Button>
                            </SheetTrigger>
                            <SheetContent className="bg-manus-secondary border-l border-manus-border w-[400px] sm:w-[400px]">
                                <SheetHeader>
                                    <SheetTitle>Flow 设置</SheetTitle>
                                    <SheetDescription>配置元数据、权限及唤醒词</SheetDescription>
                                </SheetHeader>
                                <div className="py-6 space-y-6">
                                    {/* Sidebar Form Content */}
                                    <div className="space-y-2">
                                        <Label>分类标签</Label>
                                        <div className="bg-manus-tertiary p-2 rounded-md border border-transparent focus-within:border-accent/50">
                                            <div className="flex flex-wrap gap-1 mb-2">
                                                {(form.tags || []).map(t => <Badge key={t} variant="secondary" className="text-xs">{t}</Badge>)}
                                            </div>
                                            <Input
                                                className="border-0 p-0 h-auto bg-transparent focus-visible:ring-0 text-sm"
                                                placeholder="输入标签，逗号分隔..."
                                                value={(form.tags || []).join(', ')}
                                                onChange={e => onFormChange({ ...form, tags: e.target.value.split(/[,，]/).map(s => s.trim()).filter(Boolean) })}
                                            />
                                        </div>
                                    </div>

                                    <div className="space-y-2">
                                        <Label>唤醒示例 (Few-Shot)</Label>
                                        <Textarea
                                            className="bg-manus-tertiary border-transparent focus:border-accent/50 font-mono text-sm"
                                            rows={8}
                                            placeholder="帮我查询上个月的销售报表..."
                                            value={(form.example_queries || []).join('\n')}
                                            onChange={e => onFormChange({ ...form, example_queries: e.target.value.split('\n').filter(Boolean) })}
                                        />
                                        <p className="text-xs text-manus-muted">每行一例，越多越准。</p>
                                    </div>

                                    <div className="space-y-2">
                                        <Label>可见范围</Label>
                                        <Select
                                            value={form.visibility || 'workspace'}
                                            onValueChange={(v) => onFormChange({ ...form, visibility: v as any })}
                                        >
                                            <SelectTrigger className="bg-manus-tertiary border-0">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="workspace">全工作区可见</SelectItem>
                                                <SelectItem value="private" disabled>仅自己可见(开发中)</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </div>
                            </SheetContent>
                        </Sheet>

                        <div className="h-4 w-px bg-manus-border mx-1" />

                        <Button onClick={onSave} disabled={isSaving} className="bg-accent hover:bg-accent/90 text-white min-w-[80px]">
                            {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : '发布'}
                        </Button>
                    </div>
                </header>

                {/* 2. Main Scrollable Document Area */}
                <div className="flex-1 overflow-y-auto custom-scrollbar">
                    <div className="max-w-3xl mx-auto py-12 px-6">

                        {/* Title & Description Block */}
                        <div className="space-y-4 mb-12">
                            {/* 标题输入 */}
                            <div className="relative group">
                                <div className="absolute -inset-0.5 bg-gradient-to-r from-accent/20 via-transparent to-accent/20 rounded-2xl opacity-0 group-focus-within:opacity-100 transition-opacity duration-300 blur-sm" />
                                <Input
                                    value={form.title}
                                    onChange={(e) => onFormChange({ ...form, title: e.target.value })}
                                    placeholder="输入 Flow 名称 *"
                                    className="relative text-2xl font-semibold bg-manus-secondary/60 backdrop-blur border-2 border-manus-border/60 rounded-2xl px-5 py-4 h-auto placeholder:text-manus-muted/60 focus-visible:ring-0 focus-visible:border-accent/60 transition-all duration-300 hover:border-manus-border shadow-sm"
                                />
                            </div>

                            {/* 描述输入 */}
                            <div className="relative group">
                                <div className="absolute -inset-0.5 bg-gradient-to-r from-accent/10 via-transparent to-accent/10 rounded-xl opacity-0 group-focus-within:opacity-100 transition-opacity duration-300 blur-sm" />
                                <Textarea
                                    value={form.description}
                                    onChange={(e) => onFormChange({ ...form, description: e.target.value })}
                                    placeholder="添加描述，简述这个 Flow 的功能 *"
                                    className="relative text-base text-manus-text/90 bg-manus-secondary/40 backdrop-blur border border-manus-border/40 rounded-xl px-5 py-4 resize-none focus-visible:ring-0 focus-visible:border-accent/50 min-h-[100px] transition-all duration-300 hover:border-manus-border/70 placeholder:text-manus-muted/50"
                                />
                            </div>
                        </div>

                        {/* Timeline Stream */}
                        <div className="relative pl-8 border-l-2 border-manus-border space-y-10 ml-4">
                            {form.steps.map((step, index) => {
                                const toolConf = TOOL_CONFIG[step.tool || 'none'] || TOOL_CONFIG['none']
                                const ToolIcon = toolConf.icon
                                const isExpanded = expandedSteps[index] ?? true // default expanded for demo? or use actual state
                                const isFocused = focusedStepIndex === index

                                return (
                                    <div
                                        key={index}
                                        className={cn(
                                            "relative transition-all duration-300",
                                            isFocused ? "opacity-100 scale-[1.01]" : "opacity-80 hover:opacity-100"
                                        )}
                                        onClick={() => { setFocusedStepIndex(index); setExpandedSteps(prev => ({ ...prev, [index]: true })) }}
                                    >
                                        {/* Timeline Dot */}
                                        <div className={cn(
                                            "absolute -left-[41px] top-0 w-6 h-6 rounded-full border-2 flex items-center justify-center text-[10px] font-bold z-10 bg-manus transition-colors",
                                            isFocused ? "border-accent text-accent" : "border-manus-border text-manus-muted"
                                        )}>
                                            {step.step}
                                        </div>

                                        {/* Step Card */}
                                        <div className={cn(
                                            "rounded-xl border transition-all duration-300 overflow-hidden",
                                            isFocused ? "border-accent/40 bg-manus-secondary/50 shadow-lg shadow-accent/5" : "border-manus-border bg-manus-secondary/20 hover:border-manus-border-strong"
                                        )}>
                                            {/* Step Header / Summary */}
                                            <div
                                                className="flex items-center justify-between p-4 cursor-pointer hover:bg-manus-tertiary/50"
                                                onClick={(e) => { e.stopPropagation(); toggleStepExpand(index); setFocusedStepIndex(index); }}
                                            >
                                                <div className="flex items-center gap-3">
                                                    <div className={cn("p-1.5 rounded-md", toolConf.bg, toolConf.color)}>
                                                        <ToolIcon className="h-4 w-4" />
                                                    </div>
                                                    <div className="font-medium text-manus-text">
                                                        {step.action || <span className="text-manus-muted italic">定义步骤行为...</span>}
                                                    </div>
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    {isFocused && (
                                                        <Button variant="ghost" size="icon" className="h-7 w-7 text-manus-muted hover:text-red-400" onClick={(e) => { e.stopPropagation(); removeStep(index); }}>
                                                            <Trash2 className="h-4 w-4" />
                                                        </Button>
                                                    )}
                                                    <ChevronDown className={cn("h-4 w-4 text-manus-muted transition-transform", isExpanded && "rotate-180")} />
                                                </div>
                                            </div>

                                            {/* Expanded Form */}
                                            {isExpanded && (
                                                <div className="p-4 pt-0 space-y-4 border-t border-manus-border/50 bg-manus-tertiary/10 animate-in slide-in-from-top-2">
                                                    <div className="space-y-2 mt-4">
                                                        <Label className="text-xs uppercase text-manus-muted">
                                                            执行动作 (Prompt) <span className="text-red-500">*</span>
                                                        </Label>
                                                        <Textarea
                                                            value={step.action}
                                                            onChange={e => updateStep(index, 'action', e.target.value)}
                                                            placeholder="告诉 AI 这一步做什么... *"
                                                            className="bg-manus border-transparent focus:border-accent/50 transition-colors"
                                                            autoFocus={isFocused && !step.action}
                                                        />
                                                    </div>

                                                    <div className="grid grid-cols-2 gap-4">
                                                        <div className="space-y-2">
                                                            <Label className="text-xs uppercase text-manus-muted">使用工具</Label>
                                                            <Select value={step.tool || 'none'} onValueChange={v => updateStep(index, 'tool', v === 'none' ? null : v)}>
                                                                <SelectTrigger className="bg-manus border-0">
                                                                    <SelectValue />
                                                                </SelectTrigger>
                                                                <SelectContent>
                                                                    {TOOL_OPTIONS.map(opt => {
                                                                        const disableReason =
                                                                            opt.value === 'sql_worker' && !hasDb ? '请先连接数据库' :
                                                                            opt.value === 'doc_worker' && !hasKnowledge ? '请先上传知识库文档' :
                                                                            null
                                                                        return (
                                                                            <SelectItem key={opt.value} value={opt.value} disabled={!!disableReason}>
                                                                                <span className="flex items-center gap-2">
                                                                                    <opt.icon className={cn("h-4 w-4", opt.color)} />
                                                                                    {opt.label}
                                                                                    {disableReason && (
                                                                                        <span className="flex items-center gap-1 text-yellow-500 text-xs">
                                                                                            <AlertTriangle className="h-3 w-3" />
                                                                                            {disableReason}
                                                                                        </span>
                                                                                    )}
                                                                                </span>
                                                                            </SelectItem>
                                                                        )
                                                                    })}
                                                                </SelectContent>
                                                            </Select>
                                                        </div>

                                                        <div className="space-y-2">
                                                            <Label className="text-xs uppercase text-manus-muted">变量占位符</Label>
                                                            <Input
                                                                value={(step.keywords || []).join(', ')}
                                                                onChange={e => updateStep(index, 'keywords', e.target.value.split(/[,，]/).map(k => k.trim()).filter(Boolean))}
                                                                placeholder="如: 部门名称, 时间范围"
                                                                className="bg-manus border-0"
                                                            />
                                                            <p className="text-[10px] text-manus-muted/70">用户输入中会被提取的参数名，多个用逗号隔开</p>
                                                        </div>
                                                    </div>

                                                    {/* Tool Specific Configs */}
                                                    {step.tool === 'doc_worker' && (
                                                        <div className="bg-manus rounded-lg border border-manus-border p-3">
                                                            <div className="flex items-center justify-between">
                                                                <span className="text-xs font-medium text-green-400">知识库范围</span>
                                                                <KnowledgeScopePicker
                                                                    value={normalizeDocScope(step.doc_scope as DocScope | null)}
                                                                    onChange={(scope) => updateStep(index, 'doc_scope', compactDocScope(scope) ?? null)}
                                                                    modal={false}
                                                                />
                                                            </div>
                                                        </div>
                                                    )}

                                                    {step.tool === 'sql_worker' && (
                                                        <div className="space-y-2">
                                                            <Label className="text-xs uppercase text-blue-400">SQL 模板</Label>
                                                            <Textarea
                                                                value={step.template || ''}
                                                                onChange={e => updateStep(index, 'template', e.target.value)}
                                                                className="font-mono text-xs bg-manus border-blue-500/20"
                                                                placeholder="SELECT * FROM ..."
                                                            />
                                                        </div>
                                                    )}

                                                    {step.tool === 'office_worker' && (
                                                        <div className="space-y-3">
                                                            <div className="space-y-2">
                                                                <Label className="text-xs uppercase text-orange-400">选择模板</Label>
                                                                <Select
                                                                    value={step.template_id?.toString() || ''}
                                                                    onValueChange={v => updateStep(index, 'template_id', v ? parseInt(v) : null)}
                                                                >
                                                                    <SelectTrigger className="bg-manus border-orange-500/20">
                                                                        <SelectValue placeholder="选择文档模板..." />
                                                                    </SelectTrigger>
                                                                    <SelectContent>
                                                                        {templates.length === 0 ? (
                                                                            <div className="text-xs text-manus-muted p-2 text-center">暂无模板，请先在模板管理中创建</div>
                                                                        ) : (
                                                                            templates.map(t => (
                                                                                <SelectItem key={t.id} value={t.id.toString()}>
                                                                                    <span className="flex items-center gap-2">
                                                                                        <FileText className="h-3 w-3 text-orange-400" />
                                                                                        {t.name}
                                                                                    </span>
                                                                                </SelectItem>
                                                                            ))
                                                                        )}
                                                                    </SelectContent>
                                                                </Select>
                                                            </div>
                                                            <div className="space-y-2">
                                                                <Label className="text-xs uppercase text-orange-400">输出文件名</Label>
                                                                <Input
                                                                    value={step.output_filename || ''}
                                                                    onChange={e => updateStep(index, 'output_filename', e.target.value)}
                                                                    placeholder="如: 销售报告_{{日期}}.docx"
                                                                    className="bg-manus border-orange-500/20"
                                                                />
                                                            </div>
                                                        </div>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )
                            })}

                            {/* Add Step Button */}
                            <div className="relative pl-0 pt-4 cursor-pointer group" onClick={addStep}>
                                <div className="absolute -left-[41px] top-6 w-6 h-6 rounded-full border-2 border-dashed border-manus-muted/30 flex items-center justify-center text-manus-muted group-hover:border-accent group-hover:text-accent group-hover:scale-110 transition-all bg-manus shadow-sm">
                                    <Plus className="h-3 w-3" />
                                </div>
                                <div className="border-2 border-dashed border-manus-border rounded-xl p-6 flex flex-col items-center justify-center text-manus-muted hover:border-accent/40 hover:text-accent hover:bg-accent/5 hover:shadow-md transition-all">
                                    <Plus className="h-6 w-6 mb-2 opacity-50 group-hover:opacity-100 transition-opacity" />
                                    <span className="text-sm font-medium">点击添加下一个步骤</span>
                                </div>
                            </div>

                            <div ref={stepsEndRef} className="h-20" /> {/* Spacer */}
                        </div>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    )
}
