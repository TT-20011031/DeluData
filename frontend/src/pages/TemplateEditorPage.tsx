/**
 * 模板编辑独立页面
 * 
 * 设计原则：
 * - 薄页面层：只负责路由参数解析和组件组装
 * - 复用 Hooks：使用 useTemplates 和 useTemplateDialog 中的 Hooks
 * - 分层解耦：UI 逻辑在组件层，业务逻辑在 Hooks 层
 * 
 * 路由：
 * - /extend-config/templates/new - 新建模板
 * - /extend-config/templates/:templateId - 编辑模板
 */
import { useEffect, useState, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Loader2, Save, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { useToast } from '@/components/ui/toast'
import { Breadcrumb } from '@/components/ui/breadcrumb'
import { extendConfigService } from '@/services/extendConfigService'
import type {
    CandidateField,
    VariableItem,
    BlankFieldDetectResponse,
    TemplateForm,
    TemplateGroup,
    Template,
} from '@/types/extendConfig'

// Hooks - 复用现有
import {
    useTemplateDialogWorkflow,
    useTemplatePreview,
    useVariableBinding,
} from '@/hooks/extendConfig/useTemplateDialog'
import { useLLMDetection } from '@/hooks/extendConfig/useLLMDetection'

// 组件 - 复用现有
import { FloatingToolbar } from '@/components/extendConfig/TemplateManager/FloatingToolbar'
import {
    IdleUploadView,
    FileReadyView,
    ProcessingView,
    ErrorView,
    PreviewSuccessView,
} from '@/components/extendConfig/TemplateManager/TemplateDialogViews'
import { SettingsPanel } from '@/components/extendConfig/TemplateManager/TemplateDialogPanels'
import { CandidateFieldList } from '@/components/extendConfig/TemplateManager/CandidateFieldList'

/**
 * 将 Template.variables_schema 转换为 VariableItem[]
 */
function parseVariablesFromTemplate(template: Template): VariableItem[] {
    if (!template.variables_schema) return []

    const exampleContext = template.example_context || {}
    return Object.entries(template.variables_schema).map(([name, schema]) => ({
        name,
        type: schema.type || 'text',
        desc: schema.desc || '',
        exampleValue: String(exampleContext[name] || ''),
        location: schema.location,
    }))
}

/**
 * 将 VariableItem[] 转换为 API payload 格式
 */
function convertVariablesToPayload(variables: VariableItem[]) {
    const variables_schema: Record<string, { type: string; desc: string; location?: VariableItem['location'] }> = {}
    const example_context: Record<string, string> = {}

    for (const v of variables) {
        if (v.name.trim()) {
            variables_schema[v.name] = { type: v.type, desc: v.desc, location: v.location }
            example_context[v.name] = v.exampleValue
        }
    }

    return { variables_schema, example_context }
}

export default function TemplateEditorPage() {
    const { templateId: templateIdParam } = useParams<{ templateId: string }>()
    const navigate = useNavigate()
    const { toast } = useToast()

    // 判断是新建还是编辑
    const isNew = templateIdParam === 'new'
    const templateId = isNew ? null : Number(templateIdParam)
    const isEditing = !isNew && templateId !== null && !isNaN(templateId)

    // ========== 本地状态 ==========
    const [form, setForm] = useState<TemplateForm>({
        name: '',
        description: '',
        keywords: '',
        group_id: null,
    })
    const [variables, setVariables] = useState<VariableItem[]>([])
    const [file, setFile] = useState<File | null>(null)
    const [isSaving, setIsSaving] = useState(false)
    const [isLoading, setIsLoading] = useState(false)
    const [groups, setGroups] = useState<TemplateGroup[]>([])
    const [templateFileType, setTemplateFileType] = useState<string | undefined>()

    // 处理文件选择
    const handleFileChange = useCallback((newFile: File | null) => {
        setFile(newFile)
        if (newFile && !form.name.trim()) {
            // 自动填充模板名称（去除扩展名）
            const name = newFile.name.replace(/\.[^/.]+$/, "")
            setForm(prev => ({ ...prev, name }))
        }
    }, [form.name])

    // ========== LLM 智能检测 ==========
    const [localCandidates, setLocalCandidates] = useState<CandidateField[]>([])
    const {
        startDetection,
        isLoading: isDetecting,
        progress: detectionProgress,
        stage: detectionStage,
    } = useLLMDetection({
        onComplete: (fields) => {
            setLocalCandidates(fields)
            setShowCandidates(true)
            toast({
                type: 'success',
                title: '检测完成',
                description: `发现 ${fields.length} 个候选字段`,
            })
        },
        onError: (err) => {
            toast({
                type: 'error',
                title: '检测失败',
                description: err,
            })
        }
    })
    const [showCandidates, setShowCandidates] = useState(false)
    const [isOptimizing, setIsOptimizing] = useState(false)

    // ========== 复用 Hooks ==========
    const preview = useTemplatePreview({
        templateId: templateId,
        onStatusChange: (status, error) => {
            workflowHook.setStatus(status, error)
        },
    })

    const workflowHook = useTemplateDialogWorkflow({
        isSaving,
        templateFileType,
        templateId,
        onPreviewNeeded: preview.loadPreview,
    })

    const binding = useVariableBinding({
        previewMapping: preview.previewMapping,
        previewHtml: preview.previewHtml,
        variables,
        onVariablesChange: setVariables,
    })


    // 预处理预览 HTML：将已绑定变量的高亮内联到 HTML 中
    // 这样 React 重渲染时高亮不会丢失
    const processedPreviewHtml = useMemo(() => {
        const html = preview.previewHtml
        if (!html || variables.length === 0) return html

        try {
            const parser = new DOMParser()
            const doc = parser.parseFromString(html, 'text/html')

            variables.forEach(v => {
                if (v.location?.mapping_id) {
                    const el = doc.querySelector(`[data-id="${v.location.mapping_id}"]`)
                    if (el) {
                        el.classList.add('variable-bound')
                    }
                }
            })

            return doc.body.innerHTML
        } catch {
            return html
        }
    }, [preview.previewHtml, variables])

    // ========== 加载模板数据 ==========
    useEffect(() => {
        const loadData = async () => {
            // 加载分组列表
            try {
                const groupList = await extendConfigService.getTemplateGroups()
                setGroups(groupList)
            } catch (error) {
                console.error('加载分组失败:', error)
            }

            // 如果是编辑模式，加载模板数据
            if (isEditing && templateId) {
                setIsLoading(true)
                try {
                    const template = await extendConfigService.getTemplate(templateId)
                    setForm({
                        name: template.name,
                        description: template.description || '',
                        keywords: template.keywords || '',
                        group_id: template.group_id,
                    })
                    setVariables(parseVariablesFromTemplate(template))
                    setTemplateFileType(template.file_type)

                    // 自动加载预览
                    if (template.file_type?.toLowerCase() === 'docx') {
                        preview.loadPreview()
                    }
                } catch (error) {
                    toast({
                        type: 'error',
                        title: '加载失败',
                        description: error instanceof Error ? error.message : '无法加载模板',
                    })
                } finally {
                    setIsLoading(false)
                }
            }
        }

        loadData()
    }, [templateId, isEditing])

    // ========== 保存处理 ==========
    const handleSave = useCallback(async () => {
        if (!form.name.trim()) {
            toast({ type: 'error', title: '请输入模板名称' })
            return
        }

        setIsSaving(true)
        try {
            const { variables_schema, example_context } = convertVariablesToPayload(variables)

            if (isNew) {
                // 新建模板
                if (!file) {
                    toast({ type: 'error', title: '请选择模板文件' })
                    setIsSaving(false)
                    return
                }

                const formData = new FormData()
                formData.append('name', form.name)
                formData.append('description', form.description)
                formData.append('keywords', form.keywords)
                formData.append('variables_schema', JSON.stringify(variables_schema))
                formData.append('example_context', JSON.stringify(example_context))
                if (form.group_id !== null) {
                    formData.append('group_id', String(form.group_id))
                }
                formData.append('file', file)

                const newId = await extendConfigService.createTemplate(formData)
                toast({ type: 'success', title: '模板创建成功' })
                // 跳转到编辑页面
                navigate(`/extend-config/templates/${newId}`, { replace: true })
            } else if (templateId) {
                // 更新模板
                await extendConfigService.updateTemplate(templateId, {
                    name: form.name,
                    description: form.description,
                    keywords: form.keywords,
                    variables_schema,
                    example_context,
                })
                toast({ type: 'success', title: '模板保存成功，正在编译...' })
                // 编译是后台自动进行的，给用户一个提示即可
                await new Promise(resolve => setTimeout(resolve, 1500))
                toast({ type: 'success', title: '模板编译完成' })
            }
        } catch (error) {
            toast({
                type: 'error',
                title: '保存失败',
                description: error instanceof Error ? error.message : '保存失败',
            })
        } finally {
            setIsSaving(false)
        }
    }, [form, variables, file, isNew, templateId, navigate, toast])

    // ========== 返回列表 ==========
    const handleBack = useCallback(() => {
        navigate('/extend-config?tab=templates')
    }, [navigate])

    // ========== 空白检测 ==========
    const enableDetection = useMemo(() =>
        Boolean(isEditing && templateId && templateFileType?.toLowerCase() === 'docx'),
        [isEditing, templateId, templateFileType]
    )

    const handleDetectRequest = useCallback(async () => {
        if (!templateId) return
        startDetection(templateId)
    }, [templateId, startDetection])

    const handleCandidatesConfirm = useCallback((newVariables: VariableItem[]) => {
        const existingNames = new Set(variables.map(v => v.name))
        const uniqueNewVars = newVariables.filter(v => !existingNames.has(v.name))

        if (uniqueNewVars.length < newVariables.length) {
            toast({
                type: 'warning',
                title: '部分变量已存在',
                description: `跳过 ${newVariables.length - uniqueNewVars.length} 个重复变量`,
            })
        }

        setVariables(prev => [...prev, ...uniqueNewVars])
        setShowCandidates(false)
        setLocalCandidates([])

        // 弹窗关闭后刷新高亮
        binding.refreshHighlight()
    }, [variables, toast, binding])

    const handleCandidatesCancel = useCallback(() => {
        setShowCandidates(false)
        setLocalCandidates([])
    }, [])

    // ========== 悬停高亮 ==========
    const handleHighlight = useCallback((mappingId: string | null) => {
        document.querySelectorAll('[data-id].candidate-highlight').forEach(el => {
            el.classList.remove('candidate-highlight')
        })

        if (mappingId) {
            const el = document.querySelector(`[data-id="${mappingId}"]`)
            if (el) {
                el.classList.add('candidate-highlight')
                el.scrollIntoView({ behavior: 'smooth', block: 'center' })
            }
        }
    }, [])

    // ========== LLM 优化 ==========
    const handleOptimize = useCallback(async () => {
        if (!templateId || localCandidates.length === 0) return

        setIsOptimizing(true)
        try {
            const requestData: BlankFieldDetectResponse = {
                candidates: localCandidates,
                total: localCandidates.length,
                high_confidence_count: localCandidates.filter(c => c.confidence >= 0.8).length
            }
            const result = await extendConfigService.optimizeBlankLabels(templateId, requestData)
            setLocalCandidates(result.candidates)
            toast({
                type: 'success',
                title: 'AI 优化完成',
                description: `已优化 ${result.total} 个字段的命名`,
            })
        } catch (error) {
            toast({
                type: 'error',
                title: '优化失败',
                description: error instanceof Error ? error.message : '优化失败',
            })
        } finally {
            setIsOptimizing(false)
        }
    }, [templateId, localCandidates, toast])

    // ========== 重试处理 ==========
    const handleRetry = useCallback(() => {
        if (templateId) {
            preview.loadPreview()
        } else {
            workflowHook.resetToIdle()
        }
    }, [templateId, preview, workflowHook])

    // ========== 主区域渲染 ==========
    const renderMainContent = () => {
        const { status, step, progress, errorMessage } = workflowHook.workflow

        // 加载中
        if (isLoading) {
            return (
                <div className="flex-1 flex flex-col items-center justify-center">
                    <Loader2 className="h-8 w-8 animate-spin text-accent" />
                    <p className="mt-4 text-manus-muted">加载模板数据...</p>
                </div>
            )
        }

        // 错误状态
        if (status === 'error') {
            return (
                <div className="flex-1 flex flex-col items-center justify-center p-12">
                    <ErrorView
                        message={errorMessage || '未知错误'}
                        onRetry={handleRetry}
                        onClose={workflowHook.resetToIdle}
                    />
                </div>
            )
        }

        // 处理中 or 预览加载中
        if (status === 'processing' || status === 'previewing') {
            return (
                <div className="flex-1 flex flex-col items-center justify-center p-12">
                    <ProcessingView step={step} progress={progress} />
                </div>
            )
        }

        // 成功：显示预览内容
        if (status === 'success' && processedPreviewHtml) {
            return (
                <PreviewSuccessView
                    html={processedPreviewHtml}
                    previewRef={binding.previewRef}
                    onMouseUp={binding.handleMouseUp}
                />
            )
        }

        // 空闲状态 - 有文件
        if (file) {
            return (
                <div className="flex-1 flex flex-col items-center justify-center p-12">
                    <FileReadyView
                        file={file}
                        onFileChange={handleFileChange}
                        onSave={handleSave}
                    />
                </div>
            )
        }

        // 空闲状态 - 无文件
        return (
            <div className="flex-1 flex flex-col items-center justify-center p-12">
                <IdleUploadView onFileChange={handleFileChange} />
            </div>
        )
    }

    return (
        <div className="flex-1 flex flex-col h-full bg-manus overflow-hidden">
            {/* 顶部导航栏 */}
            <header className="flex-none h-14 border-b border-manus-border bg-manus/95 backdrop-blur z-20 px-6 flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={handleBack}
                        className="-ml-2 text-manus-muted hover:text-manus-text"
                    >
                        <ArrowLeft className="h-5 w-5" />
                    </Button>

                    <Breadcrumb
                        showHome={false}
                        items={[
                            { label: '扩展配置', href: '/extend-config' },
                            { label: '模板管理', href: '/extend-config?tab=templates' },
                            { label: isNew ? '新建模板' : (form.name || '编辑模板') }
                        ]}
                    />
                </div>

                <div className="flex items-center gap-2">
                    <Button
                        onClick={handleSave}
                        disabled={isSaving}
                        className="bg-accent hover:bg-accent/90 text-white min-w-[80px]"
                    >
                        {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <><Save className="h-4 w-4 mr-2" />保存</>}
                    </Button>
                </div>
            </header>

            {/* 主内容区 */}
            <div className="flex-1 overflow-hidden flex flex-row">
                {/* 左侧：预览区 */}
                <div className="flex-1 bg-manus-elevated relative overflow-hidden flex flex-col">
                    {renderMainContent()}

                    {/* 浮动绑定工具栏 - 无 Portal，直接渲染 */}
                    {binding.selection && binding.selection.visible && (
                        <FloatingToolbar
                            visible={binding.selection.visible}
                            x={binding.selection.x}
                            y={binding.selection.y}
                            selectedText={binding.selection.text}
                            existingVariable={binding.getExistingVariable()}
                            onCreateAndBind={binding.handleCreateAndBind}
                            onUpdateBinding={binding.handleUpdateBinding}
                            onUnbind={binding.handleUnbind}
                            onClose={binding.clearSelection}
                        />
                    )}
                </div>

                {/* 右侧：设置面板 */}
                <SettingsPanel
                    form={form}
                    onFormChange={setForm}
                    variables={variables}
                    onVariablesChange={setVariables}
                    groups={groups}
                    isEditing={isEditing}
                    file={file}
                    onFileChange={handleFileChange}
                    templateId={templateId}
                    enableDetection={enableDetection}
                    onDetectRequest={handleDetectRequest}
                    isDetecting={isDetecting}
                    detectionProgress={detectionProgress}
                    detectionStage={detectionStage}
                />
            </div>

            {/* 候选字段弹窗 */}
            <Dialog open={showCandidates} onOpenChange={(o) => !o && handleCandidatesCancel()}>
                <DialogContent className="max-w-4xl h-[80vh] p-0 flex flex-col">
                    <CandidateFieldList
                        candidates={localCandidates}
                        onConfirm={handleCandidatesConfirm}
                        onCancel={handleCandidatesCancel}
                        onOptimize={handleOptimize}
                        isOptimizing={isOptimizing}
                        onHighlight={handleHighlight}
                    />
                </DialogContent>
            </Dialog>
        </div>
    )
}
