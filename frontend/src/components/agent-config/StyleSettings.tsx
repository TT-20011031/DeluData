/**
 * 智能体配置 - 回复风格组件
 */
import { useState } from 'react'
import { MessageSquare, RotateCcw, Plus, Trash2, Shield, User } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
    Select,
    SelectContent,
    SelectGroup,
    SelectItem,
    SelectLabel,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '@/components/ui/dialog'
import type { TemplateListResponse } from './types'

interface StyleSettingsProps {
    templates: TemplateListResponse
    selectedTemplateId: string
    setSelectedTemplateId: (id: string) => void
    customPrompt: string
    setCustomPrompt: (val: string) => void
    maxPromptLength: number
    isLoading: boolean
    onResetToDefault: () => void
    onCreateTemplate: (name: string, desc: string, prompt: string) => Promise<void>
    onDeleteTemplate: (id: string) => void
}

export function StyleSettings({
    templates,
    selectedTemplateId,
    setSelectedTemplateId,
    customPrompt,
    setCustomPrompt,
    maxPromptLength,
    isLoading,
    onResetToDefault,
    onCreateTemplate,
    onDeleteTemplate
}: StyleSettingsProps) {
    const [isDialogOpen, setIsDialogOpen] = useState(false)
    const [isCreating, setIsCreating] = useState(false)
    const [newName, setNewName] = useState('')
    const [newDesc, setNewDesc] = useState('')
    const [newPrompt, setNewPrompt] = useState('')

    const allTemplates = [...templates.system_templates, ...templates.user_templates]
    const selectedTemplate = allTemplates.find(t => t.template_id === selectedTemplateId)
    const isOverLimit = customPrompt.length > maxPromptLength

    const handleCreate = async () => {
        if (!newName.trim() || !newPrompt.trim()) return
        setIsCreating(true)
        try {
            await onCreateTemplate(newName, newDesc, newPrompt)
            setNewName('')
            setNewDesc('')
            setNewPrompt('')
            setIsDialogOpen(false)
        } finally {
            setIsCreating(false)
        }
    }

    return (
        <Card className="bg-manus-secondary/50 border-manus-border">
            <CardHeader className="pb-4">
                <div className="flex items-center justify-between">
                    <div>
                        <CardTitle className="text-base flex items-center gap-2">
                            <MessageSquare className="h-4 w-4 text-accent" />
                            回复风格
                        </CardTitle>
                        <CardDescription className="text-sm mt-1">
                            配置智能体的语言风格和格式偏好
                        </CardDescription>
                    </div>
                    <Button variant="ghost" size="sm" onClick={onResetToDefault} disabled={isLoading} className="gap-1 text-manus-muted hover:text-manus-text">
                        <RotateCcw className="h-3.5 w-3.5" />
                        重置
                    </Button>
                </div>
            </CardHeader>
            <CardContent className="space-y-6">
                {/* 模板选择 */}
                <div className="space-y-3">
                    <div className="flex items-center justify-between">
                        <Label className="text-sm font-medium">选择模板</Label>
                        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                            <DialogTrigger asChild>
                                <Button variant="ghost" size="sm" className="h-7 gap-1 text-accent hover:text-accent">
                                    <Plus className="h-3 w-3" />
                                    新建
                                </Button>
                            </DialogTrigger>
                            <DialogContent className="bg-manus-secondary border-manus-border">
                                <DialogHeader>
                                    <DialogTitle>新建自定义模板</DialogTitle>
                                    <DialogDescription>创建您自己的回复风格模板</DialogDescription>
                                </DialogHeader>
                                <div className="space-y-4 py-4">
                                    <div className="space-y-2">
                                        <Label>模板名称</Label>
                                        <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="例如：日报风格" className="bg-manus-tertiary border-manus-border" />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>描述（可选）</Label>
                                        <Input value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder="简短描述用途" className="bg-manus-tertiary border-manus-border" />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Prompt 内容</Label>
                                        <Textarea value={newPrompt} onChange={(e) => setNewPrompt(e.target.value)} placeholder="输入 Prompt..." className="bg-manus-tertiary border-manus-border min-h-[120px]" />
                                    </div>
                                </div>
                                <DialogFooter>
                                    <Button variant="outline" onClick={() => setIsDialogOpen(false)}>取消</Button>
                                    <Button onClick={handleCreate} disabled={isCreating || !newName || !newPrompt}>
                                        {isCreating ? '创建中...' : '创建'}
                                    </Button>
                                </DialogFooter>
                            </DialogContent>
                        </Dialog>
                    </div>

                    <Select value={selectedTemplateId} onValueChange={setSelectedTemplateId} disabled={isLoading}>
                        <SelectTrigger className="bg-manus-tertiary border-manus-border">
                            <SelectValue placeholder="选择模板" />
                        </SelectTrigger>
                        <SelectContent className="bg-manus-secondary border-manus-border">
                            <SelectGroup>
                                <SelectLabel className="flex items-center gap-1.5">
                                    <Shield className="h-3 w-3 text-accent" />
                                    系统预设
                                </SelectLabel>
                                {templates.system_templates.map(t => (
                                    <SelectItem key={t.template_id} value={t.template_id}>
                                        <div className="flex items-center gap-2">
                                            <span className="px-1.5 py-0.5 text-[10px] rounded bg-accent/20 text-accent font-medium">官方</span>
                                            <span>{t.name}</span>
                                            <span className="text-manus-muted">- {t.description}</span>
                                        </div>
                                    </SelectItem>
                                ))}
                            </SelectGroup>
                            {templates.user_templates.length > 0 && (
                                <SelectGroup>
                                    <SelectLabel className="flex items-center gap-1.5">
                                        <User className="h-3 w-3 text-success" />
                                        我的模板
                                    </SelectLabel>
                                    {templates.user_templates.map(t => (
                                        <SelectItem key={t.template_id} value={t.template_id}>
                                            <div className="flex items-center gap-2">
                                                <span className="px-1.5 py-0.5 text-[10px] rounded bg-success/20 text-success font-medium">自定义</span>
                                                <span>{t.name}</span>
                                            </div>
                                        </SelectItem>
                                    ))}
                                </SelectGroup>
                            )}
                        </SelectContent>
                    </Select>

                    {/* 模板预览 */}
                    {selectedTemplate && (
                        <div className={`p-3 rounded-lg border ${selectedTemplate.is_system ? 'bg-accent/5 border-accent/30' : 'bg-success/5 border-success/30'}`}>
                            <div className="flex items-center justify-between mb-1.5">
                                <div className="flex items-center gap-2">
                                    {selectedTemplate.is_system ? (
                                        <span className="flex items-center gap-1 text-xs font-medium text-accent">
                                            <Shield className="h-3 w-3" />
                                            系统预设
                                        </span>
                                    ) : (
                                        <span className="flex items-center gap-1 text-xs font-medium text-success">
                                            <User className="h-3 w-3" />
                                            自定义模板
                                        </span>
                                    )}
                                    <span className="text-xs text-manus-text font-medium">· {selectedTemplate.name}</span>
                                </div>
                                {!selectedTemplate.is_system && (
                                    <Button variant="ghost" size="sm" className="h-5 px-1.5 text-red-400 hover:text-red-300" onClick={() => onDeleteTemplate(selectedTemplate.template_id)}>
                                        <Trash2 className="h-3 w-3" />
                                    </Button>
                                )}
                            </div>
                            <p className="text-xs text-manus-muted line-clamp-2">{selectedTemplate.prompt}</p>
                        </div>
                    )}
                </div>

                {/* 自定义微调 */}
                <div className="space-y-3 pt-4 border-t border-manus-border/50">
                    <div className="flex items-center justify-between">
                        <Label className="text-sm font-medium">额外微调指令</Label>
                        <span className={`text-xs font-mono ${isOverLimit ? 'text-red-400' : 'text-manus-subtle'}`}>
                            {customPrompt.length}/{maxPromptLength}
                        </span>
                    </div>
                    <Textarea
                        value={customPrompt}
                        onChange={(e) => setCustomPrompt(e.target.value)}
                        placeholder="输入额外偏好指令..."
                        className={`bg-manus-tertiary border-manus-border min-h-[80px] text-sm ${isOverLimit ? 'border-red-400' : ''}`}
                        disabled={isLoading}
                    />
                    <p className="text-xs text-manus-subtle">微调指令会附加在模板之后，系统会自动添加安全规则。</p>
                </div>
            </CardContent>
        </Card>
    )
}
