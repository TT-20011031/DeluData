/**
 * Skills 操作手册管理主组件
 * 
 * 功能：
 * - CRUD 操作
 * - LLM 生成 Skill（前端入口暂停使用，代码保留待恢复）
 * - Dry Run 测试
 */
import { useState, useCallback, useEffect, useMemo } from 'react'
import {
    BookOpen, Plus, RefreshCw, Loader2, Sparkles, Play, Edit, Trash2,
    Database, Search, Command
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { ConfirmDialog, useConfirmDialog } from '../common'
import { SkillEditorModal } from './SkillEditorModal'
import { SkillPreviewPanel } from './SkillPreviewPanel'
import { SkillDryRunDialog } from './SkillDryRunDialog'
import { SkillGenerateDialog } from './SkillGenerateDialog'
import type {
    Skill,
    SkillSummary,
    SkillCreateRequest,
} from '@/services/skillService'
import { skillService } from '@/services/skillService'
import { cn } from '@/lib/utils'

/** 空 Skill 表单 */
const EMPTY_SKILL_FORM: SkillCreateRequest = {
    title: '',
    description: '',
    steps: [{ step: 1, action: '', tool: null }],
    tags: [],
    example_queries: [],
    visibility: 'workspace',
}

// 前端暂停使用：按当前产品要求禁用 DeluSkills 的 AI 生成功能，仅保留手动新建和编辑。
const DELU_SKILL_AI_GENERATE_DISABLED = true

export function SkillManager() {
    const [skills, setSkills] = useState<SkillSummary[]>([])
    const [isLoading, setIsLoading] = useState(true)
    const [isSaving, setIsSaving] = useState(false)
    const [expandedId, setExpandedId] = useState<string | null>(null)
    const [expandedSkill, setExpandedSkill] = useState<Skill | null>(null)
    const [searchQuery, setSearchQuery] = useState('')

    // 编辑器状态
    const [showEditor, setShowEditor] = useState(false)
    const [editorForm, setEditorForm] = useState<SkillCreateRequest>(EMPTY_SKILL_FORM)
    const [editingId, setEditingId] = useState<string | null>(null)

    const [showGenerateDialog, setShowGenerateDialog] = useState(false)
    const [isGenerating, setIsGenerating] = useState(false)

    const [showDryRun, setShowDryRun] = useState(false)
    const [dryRunSkillId, setDryRunSkillId] = useState<string | null>(null)
    const [dryRunSkillTitle, setDryRunSkillTitle] = useState('')
    const [isReindexing, setIsReindexing] = useState(false)

    const confirmDialog = useConfirmDialog()

    // 加载 Skills
    const loadSkills = useCallback(async () => {
        setIsLoading(true)
        try {
            const data = await skillService.getSkills()
            setSkills(data)
        } catch (error) {
            console.error('加载 Skills 失败:', error)
        } finally {
            setIsLoading(false)
        }
    }, [])

    useEffect(() => {
        loadSkills()
    }, [loadSkills])

    // 过滤 Skills
    const filteredSkills = useMemo(() => {
        if (!searchQuery) return skills
        const lowerQ = searchQuery.toLowerCase()
        return skills.filter(s =>
            s.title.toLowerCase().includes(lowerQ) ||
            s.description?.toLowerCase().includes(lowerQ) ||
            s.tags?.some(t => t.toLowerCase().includes(lowerQ))
        )
    }, [skills, searchQuery])

    // 展开/收起详情
    const toggleExpand = async (id: string, e?: React.MouseEvent) => {
        if (e) e.stopPropagation()

        if (expandedId === id) {
            setExpandedId(null)
            setExpandedSkill(null)
        } else {
            setExpandedId(id)
            try {
                const detail = await skillService.getSkill(id)
                setExpandedSkill(detail)
            } catch (error) {
                confirmDialog.showError('加载详情失败')
            }
        }
    }

    // 新建 Skill
    const handleCreate = useCallback(() => {
        setEditingId(null)
        setEditorForm({ ...EMPTY_SKILL_FORM })
        setShowEditor(true)
    }, [])

    // 编辑 Skill
    const handleEdit = useCallback(async (id: string, e?: React.MouseEvent) => {
        if (e) e.stopPropagation()
        try {
            const skill = await skillService.getSkill(id)
            setEditingId(id)
            setEditorForm({
                title: skill.title,
                description: skill.description,
                steps: skill.steps,
                tags: skill.tags,
                example_queries: skill.example_queries,
                visibility: skill.visibility,
            })
            setShowEditor(true)
        } catch (error) {
            console.error('加载 Skill 失败:', error)
            const errorMsg = error instanceof Error ? error.message : '加载 Skill 失败'
            confirmDialog.showError(errorMsg)
        }
    }, [confirmDialog])

    // 保存 Skill
    const handleSave = useCallback(async () => {
        if (!editorForm.title.trim()) {
            confirmDialog.showError('标题不能为空')
            return
        }
        if (editorForm.steps.length === 0) {
            confirmDialog.showError('至少需要一个步骤')
            return
        }

        setIsSaving(true)
        try {
            if (editingId) {
                await skillService.updateSkill(editingId, editorForm)
            } else {
                await skillService.createSkill(editorForm)
            }
            setShowEditor(false)
            loadSkills()
        } catch (error) {
            console.error('保存 Skill 失败:', error)
            const errorMsg = error instanceof Error ? error.message : '操作失败'
            confirmDialog.showError(errorMsg)
        } finally {
            setIsSaving(false)
        }
    }, [editorForm, editingId, loadSkills, confirmDialog])

    // 删除 Skill
    const handleDelete = useCallback((id: string, title: string, e?: React.MouseEvent) => {
        if (e) e.stopPropagation()
        confirmDialog.showConfirm(`确定要删除「${title}」吗？`, async () => {
            try {
                await skillService.deleteSkill(id)
                loadSkills()
                if (expandedId === id) {
                    setExpandedId(null)
                    setExpandedSkill(null)
                }
            } catch (error) {
                console.error('删除 Skill 失败:', error)
                const errorMsg = error instanceof Error ? error.message : '删除失败'
                confirmDialog.showError(errorMsg)
            }
        })
    }, [loadSkills, expandedId, confirmDialog])

    // LLM 生成
    const handleGenerate = useCallback(async (userInput: string) => {
        if (DELU_SKILL_AI_GENERATE_DISABLED) {
            confirmDialog.showError('AI 生成功能前端已暂停使用')
            return
        }

        setIsGenerating(true)
        try {
            const result = await skillService.generateSkill({ user_input: userInput })
            // 填充到编辑器
            setEditingId(null)
            setEditorForm({
                title: result.title,
                description: result.description,
                steps: result.steps,
                tags: result.tags,
                example_queries: result.example_queries,
                visibility: 'workspace',
            })
            setShowGenerateDialog(false)
            setShowEditor(true)
        } catch (error) {
            console.error('生成 Skill 失败:', error)
            const errorMsg = error instanceof Error ? error.message : '生成失败'
            confirmDialog.showError(errorMsg)
        } finally {
            setIsGenerating(false)
        }
    }, [confirmDialog])

    // Dry Run 测试
    const openDryRun = useCallback((id: string, title: string, e?: React.MouseEvent) => {
        if (e) e.stopPropagation()
        setDryRunSkillId(id)
        setDryRunSkillTitle(title)
        setShowDryRun(true)
    }, [])

    // 重新索引
    const handleReindex = useCallback(async () => {
        setIsReindexing(true)
        try {
            const result = await skillService.reindexSkills()
            confirmDialog.showSuccess(`索引完成: ${result.success}/${result.total} 成功`)
        } catch (error) {
            console.error('重新索引失败:', error)
            const errorMsg = error instanceof Error ? error.message : '重新索引失败'
            confirmDialog.showError(errorMsg)
        } finally {
            setIsReindexing(false)
        }
    }, [confirmDialog])

    if (isLoading) {
        return (
            <div className="flex h-full items-center justify-center">
                <div className="flex flex-col items-center gap-2">
                    <Loader2 className="h-8 w-8 animate-spin text-accent" />
                    <span className="text-sm text-manus-muted">加载技能库...</span>
                </div>
            </div>
        )
    }

    // 列表渲染组件
    const SkillList = () => (
        <div className="flex flex-col h-full bg-manus-secondary border-r border-manus-border">
            {/* Header */}
            <div className="p-4 border-b border-manus-border space-y-4">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-manus-text font-semibold">
                        <BookOpen className="h-5 w-5 text-accent" />
                        DeluSkills
                        <Badge variant="secondary" className="bg-manus-tertiary text-xs rounded-full px-2">
                            {skills.length}
                        </Badge>
                    </div>
                    <div className="flex gap-1">
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={loadSkills}
                            className="h-8 w-8 text-manus-muted hover:text-manus-text"
                            title="刷新列表"
                        >
                            <RefreshCw className="h-4 w-4" />
                        </Button>
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={handleReindex}
                            disabled={isReindexing}
                            className={cn(
                                "h-8 w-8 text-manus-muted hover:text-manus-text",
                                isReindexing && "animate-spin"
                            )}
                            title="重建索引"
                        >
                            <Database className="h-4 w-4" />
                        </Button>
                    </div>
                </div>

                <div className="relative">
                    <Search className="absolute left-3 top-2.5 h-4 w-4 text-manus-muted" />
                    <Input
                        className="pl-9 h-9 bg-manus-tertiary border-0 text-sm placeholder:text-manus-muted/50 focus-visible:ring-1 focus-visible:ring-accent"
                        placeholder="搜索手册、标签..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                    />
                </div>

                <div className="grid grid-cols-2 gap-2">
                    <Button
                        onClick={() => setShowGenerateDialog(true)}
                        disabled={DELU_SKILL_AI_GENERATE_DISABLED}
                        className={cn(
                            "w-full border h-9",
                            DELU_SKILL_AI_GENERATE_DISABLED
                                ? "bg-manus-tertiary text-manus-muted border-manus-border cursor-not-allowed"
                                : "bg-gradient-to-r from-indigo-500/10 to-purple-500/10 hover:from-indigo-500/20 hover:to-purple-500/20 text-accent border-accent/20"
                        )}
                    >
                        <Sparkles className="h-4 w-4 mr-2" />
                        AI 生成（暂停使用）
                    </Button>
                    <Button
                        onClick={handleCreate}
                        className="w-full bg-accent hover:bg-accent/90 text-white h-9"
                    >
                        <Plus className="h-4 w-4 mr-2" />
                        新建
                    </Button>
                </div>
            </div>

            {/* List Content */}
            <div className="flex-1 overflow-y-auto p-2 space-y-2 custom-scrollbar">
                {filteredSkills.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-48 text-manus-muted text-sm space-y-2">
                        <Command className="h-8 w-8 opacity-20" />
                        <p>未找到匹配的手册</p>
                    </div>
                ) : (
                    filteredSkills.map(skill => (
                        <div
                            key={skill.id}
                            onClick={() => toggleExpand(skill.id)}
                            className={cn(
                                "group relative rounded-lg border p-3 cursor-pointer transition-all duration-200",
                                expandedId === skill.id
                                    ? "bg-accent/5 border-accent shadow-sm"
                                    : "bg-manus hover:bg-manus-hover border-transparent hover:border-manus-border"
                            )}
                        >
                            <div className="flex justify-between items-start mb-2">
                                <div className="space-y-1">
                                    <div className="font-medium text-manus-text text-sm line-clamp-1 group-hover:text-accent transition-colors">
                                        {skill.title}
                                    </div>
                                    <div className="text-xs text-manus-muted line-clamp-2 leading-relaxed">
                                        {skill.description || '暂无描述'}
                                    </div>
                                </div>
                                <div className={cn(
                                    "opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1",
                                    expandedId === skill.id && "opacity-100"
                                )}>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        className="h-6 w-6 text-manus-muted hover:text-accent hover:bg-accent/10"
                                        onClick={(e) => openDryRun(skill.id, skill.title, e)}
                                        title="测试运行"
                                    >
                                        <Play className="h-3 w-3" />
                                    </Button>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        className="h-6 w-6 text-manus-muted hover:text-accent hover:bg-accent/10"
                                        onClick={(e) => handleEdit(skill.id, e)}
                                        title="编辑"
                                    >
                                        <Edit className="h-3 w-3" />
                                    </Button>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        className="h-6 w-6 text-manus-muted hover:text-red-500 hover:bg-red-500/10"
                                        onClick={(e) => handleDelete(skill.id, skill.title, e)}
                                        title="删除"
                                    >
                                        <Trash2 className="h-3 w-3" />
                                    </Button>
                                </div>
                            </div>

                            <div className="flex flex-wrap gap-1.5 mt-2">
                                {skill.tags?.slice(0, 3).map(tag => (
                                    <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded-full bg-manus-tertiary text-manus-muted border border-manus-border/50">
                                        {tag}
                                    </span>
                                ))}
                                {(skill.tags?.length || 0) > 3 && (
                                    <span className="text-[10px] px-1.5 py-0.5 text-manus-muted">
                                        +{skill.tags!.length - 3}
                                    </span>
                                )}
                            </div>
                        </div>
                    ))
                )}
            </div>
        </div>
    )

    return (
        <>
            <div className="grid grid-cols-1 xl:grid-cols-[380px_1fr] h-full gap-0 bg-manus rounded-xl border border-manus-border overflow-hidden shadow-sm">
                {/* Left Panel: List */}
                <SkillList />

                {/* Right Panel: Content / Editor */}
                <div className="relative h-full overflow-hidden bg-manus-tertiary/20">
                    {/* Background Pattern */}
                    <div className="absolute inset-0 pointer-events-none opacity-[0.02]"
                        style={{ backgroundImage: 'radial-gradient(circle at 1px 1px, currentColor 1px, transparent 0)', backgroundSize: '24px 24px' }}
                    />

                    {expandedId && expandedSkill ? (
                        <div className="h-full flex flex-col">
                            <div className="p-6 border-b border-manus-border bg-manus/50 backdrop-blur-sm sticky top-0 z-10">
                                <div className="flex items-center justify-between mb-2">
                                    <h2 className="text-2xl font-semibold text-manus-text">{expandedSkill.title}</h2>
                                    <div className="flex gap-2">
                                        <Button onClick={() => openDryRun(expandedSkill.id, expandedSkill.title)} size="sm">
                                            <Play className="h-4 w-4 mr-2" /> 运行测试
                                        </Button>
                                        <Button variant="outline" onClick={() => handleEdit(expandedSkill.id)} size="sm">
                                            <Edit className="h-4 w-4 mr-2" /> 编辑
                                        </Button>
                                    </div>
                                </div>
                                <p className="text-manus-muted text-sm max-w-2xl">{expandedSkill.description}</p>
                                <div className="flex flex-wrap gap-2 mt-4">
                                    {expandedSkill.tags?.map(tag => (
                                        <Badge key={tag} variant="outline" className="text-xs text-manus-muted">{tag}</Badge>
                                    ))}
                                </div>
                            </div>
                            <div className="flex-1 overflow-y-auto p-6">
                                <SkillPreviewPanel skill={expandedSkill} />
                            </div>
                        </div>
                    ) : (
                        <div className="flex flex-col items-center justify-center h-full text-manus-muted space-y-4">
                            <div className="h-20 w-20 rounded-2xl bg-manus-tertiary flex items-center justify-center mb-2 animate-pulse-slow">
                                <BookOpen className="h-10 w-10 opacity-30" />
                            </div>
                            <div className="text-center">
                                <h3 className="text-lg font-medium text-manus-text mb-1">选择一个 DeluSkills</h3>
                                <p className="text-sm opacity-60 max-w-xs mx-auto">
                                    点击左侧列表查看详情、编辑或测试运行。
                                </p>
                            </div>
                            <Button
                                variant="outline"
                                onClick={() => setShowGenerateDialog(true)}
                                disabled={DELU_SKILL_AI_GENERATE_DISABLED}
                                className="mt-4"
                            >
                                <Sparkles className="h-4 w-4 mr-2 text-accent" />
                                AI 生成暂停使用
                            </Button>
                        </div>
                    )}
                </div>
            </div>

            <SkillEditorModal
                open={showEditor}
                onClose={() => setShowEditor(false)}
                form={editorForm}
                onFormChange={setEditorForm}
                onSave={handleSave}
                isSaving={isSaving}
                isEditing={editingId !== null}
            />

            <SkillGenerateDialog
                open={!DELU_SKILL_AI_GENERATE_DISABLED && showGenerateDialog}
                onClose={() => setShowGenerateDialog(false)}
                onGenerate={handleGenerate}
                isGenerating={isGenerating}
            />

            <SkillDryRunDialog
                open={showDryRun}
                onClose={() => setShowDryRun(false)}
                skillId={dryRunSkillId}
                skillTitle={dryRunSkillTitle}
            />

            <ConfirmDialog
                type={confirmDialog.state.type}
                isOpen={confirmDialog.state.isOpen}
                onClose={confirmDialog.close}
                title={confirmDialog.state.title}
                message={confirmDialog.state.message}
                onConfirm={confirmDialog.state.onConfirm}
                isLoading={confirmDialog.state.isLoading}
            />
        </>
    )
}
