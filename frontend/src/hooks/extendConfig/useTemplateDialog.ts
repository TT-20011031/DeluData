/**
 * TemplateDialog 专用 Hooks
 * 
 * 职责：
 * - useTemplateDialogWorkflow: 状态机管理，控制处理流程
 * - useTemplatePreview: 预览加载与数据管理
 * - useVariableBinding: 变量绑定交互逻辑
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useToast } from '@/components/ui/toast'
import { extendConfigService } from '@/services/extendConfigService'
import type {
    WorkflowState,
    WorkflowStatus,
    PreviewMappingItem,
    VariableItem,
    TemplateVariableLocation,
} from '@/types/extendConfig'

// =============================================================================
// 常量
// =============================================================================

const STEP_MIN_DURATION = 1000  // 每步最小显示时间
const TOTAL_MIN_DURATION = 3000 // 总共至少 3 秒

// =============================================================================
// useTemplateDialogWorkflow - 工作流状态机
// =============================================================================

interface UseWorkflowOptions {
    isSaving: boolean
    templateFileType?: string
    templateId?: number | null
    onPreviewNeeded: () => void
}

export function useTemplateDialogWorkflow({
    isSaving,
    templateFileType,
    templateId,
    onPreviewNeeded,
}: UseWorkflowOptions) {
    const [workflow, setWorkflow] = useState<WorkflowState>({
        status: 'idle',
        step: 'uploading',
        progress: 0,
        errorMessage: null
    })

    // Refs for animation control
    const progressStartTimeRef = useRef<number>(0)
    const stepStartTimeRef = useRef<number>(0)
    const apiCompleteRef = useRef<boolean>(false)
    const animationFrameRef = useRef<number | null>(null)

    const resetToIdle = useCallback(() => {
        setWorkflow({
            status: 'idle',
            step: 'uploading',
            progress: 0,
            errorMessage: null
        })
        apiCompleteRef.current = false
        if (animationFrameRef.current) {
            cancelAnimationFrame(animationFrameRef.current)
            animationFrameRef.current = null
        }
    }, [])

    // 监听 isSaving 开始处理
    useEffect(() => {
        if (isSaving && workflow.status === 'idle') {
            progressStartTimeRef.current = Date.now()
            stepStartTimeRef.current = Date.now()
            apiCompleteRef.current = false
            setWorkflow({
                status: 'processing',
                step: 'uploading',
                progress: 0,
                errorMessage: null
            })
        }

        if (!isSaving && workflow.status === 'processing') {
            apiCompleteRef.current = true
        }
    }, [isSaving, workflow.status])

    // 触发预览加载的 Ref，防止重复触发
    const previewTriggeredRef = useRef(false)

    // 重置 previewTriggeredRef
    useEffect(() => {
        if (isSaving && workflow.status === 'idle') {
            previewTriggeredRef.current = false
        }
    }, [isSaving, workflow.status])

    // 当保存完成且是 docx 时，且进度 >= 80% 时，触发预览加载
    useEffect(() => {
        const isDocx = templateFileType?.toLowerCase() === 'docx' || templateFileType?.toLowerCase() === 'doc'
        const shouldTrigger =
            workflow.status === 'processing' &&
            apiCompleteRef.current &&
            templateId &&
            isDocx &&
            workflow.progress >= 80 &&
            !previewTriggeredRef.current

        if (shouldTrigger) {
            previewTriggeredRef.current = true
            onPreviewNeeded()
        }
    }, [workflow.status, workflow.progress, templateId, templateFileType, onPreviewNeeded])

    // 非 docx 时直接完成
    useEffect(() => {
        const isDocx = templateFileType?.toLowerCase() === 'docx'
        if (
            workflow.status === 'processing' &&
            apiCompleteRef.current &&
            !isDocx
        ) {
            const remaining = TOTAL_MIN_DURATION - (Date.now() - progressStartTimeRef.current)
            const timer = setTimeout(() => {
                setWorkflow(prev => ({
                    ...prev,
                    status: 'success',
                    progress: 100
                }))
            }, Math.max(0, remaining))
            return () => clearTimeout(timer)
        }
    }, [workflow.status, templateFileType])

    // 进度动画循环 - 模拟进度
    useEffect(() => {
        if (workflow.status !== 'processing') {
            if (animationFrameRef.current) {
                cancelAnimationFrame(animationFrameRef.current)
                animationFrameRef.current = null
            }
            return
        }

        // 使用 interval 而非 requestAnimationFrame，更稳定的更新
        const intervalId = setInterval(() => {
            const totalElapsed = Date.now() - progressStartTimeRef.current
            const stepElapsed = Date.now() - stepStartTimeRef.current

            setWorkflow(prev => {
                // === 计算模拟进度 ===
                let newProgress = prev.progress

                if (apiCompleteRef.current) {
                    // API 已完成（保存成功）
                    const isDocx = templateFileType?.toLowerCase() === 'docx' || templateFileType?.toLowerCase() === 'doc'

                    if (isDocx) {
                        // Docx 需要等待预览加载
                        // 快速推进到 99%，然后停住等待外部将状态设为 success
                        newProgress = Math.min(99, prev.progress + 2)
                    } else {
                        // 其他文件直接完成
                        newProgress = Math.min(100, prev.progress + 5)
                    }
                } else {
                    // API 尚未完成：基于时间的模拟进度（更慢）
                    // 0-5s: -> 30%
                    // 5-15s: -> 60%
                    // 15-30s: -> 85%
                    // 30s+: -> 90%
                    if (totalElapsed < 5000) {
                        newProgress = Math.min(30, (totalElapsed / 5000) * 30)
                    } else if (totalElapsed < 15000) {
                        newProgress = 30 + ((totalElapsed - 5000) / 10000) * 30
                    } else if (totalElapsed < 30000) {
                        newProgress = 60 + ((totalElapsed - 15000) / 15000) * 25
                    } else {
                        const extraTime = totalElapsed - 30000
                        newProgress = Math.min(90, 85 + (extraTime / 20000) * 5)
                    }
                }

                // === 计算步骤切换 ===
                let newStep = prev.step
                if (stepElapsed >= STEP_MIN_DURATION) {
                    if (prev.step === 'uploading') {
                        stepStartTimeRef.current = Date.now()
                        newStep = 'analyzing'
                    } else if (prev.step === 'analyzing') {
                        stepStartTimeRef.current = Date.now()
                        newStep = 'generating'
                    }
                }

                return { ...prev, progress: Math.floor(newProgress), step: newStep }
            })
        }, 80) // 每 80ms 更新一次

        return () => clearInterval(intervalId)
    }, [workflow.status])

    const setStatus = useCallback((status: WorkflowStatus, errorMessage?: string) => {
        setWorkflow(prev => ({
            ...prev,
            status,
            progress: status === 'success' ? 100 : prev.progress,
            errorMessage: errorMessage || null
        }))
    }, [])

    return {
        workflow,
        setWorkflow,
        setStatus,
        resetToIdle,
    }
}

// =============================================================================
// useTemplatePreview - 预览加载
// =============================================================================

interface UsePreviewOptions {
    templateId: number | null | undefined
    onStatusChange: (status: WorkflowStatus, error?: string) => void
}

export function useTemplatePreview({ templateId, onStatusChange }: UsePreviewOptions) {
    const { toast } = useToast()
    const [previewHtml, setPreviewHtml] = useState('')
    const [previewMapping, setPreviewMapping] = useState<PreviewMappingItem[]>([])
    const [isLoading, setIsLoading] = useState(false)

    const loadPreview = useCallback(async () => {
        if (!templateId) return

        setIsLoading(true)
        onStatusChange('previewing')

        try {
            const data = await extendConfigService.previewTemplate(templateId)
            setPreviewHtml(data.html || '')
            setPreviewMapping(data.mapping || [])
            onStatusChange('success')
        } catch (error) {
            const msg = error instanceof Error ? error.message : '无法加载预览'
            onStatusChange('error', msg)
            toast({
                type: 'error',
                title: '预览失败',
                description: msg,
            })
        } finally {
            setIsLoading(false)
        }
    }, [templateId, onStatusChange, toast])

    const reset = useCallback(() => {
        setPreviewHtml('')
        setPreviewMapping([])
    }, [])

    return {
        previewHtml,
        previewMapping,
        isLoading,
        loadPreview,
        reset,
    }
}

// =============================================================================
// useVariableBinding - 变量绑定交互
// =============================================================================

interface SelectionState {
    visible: boolean
    x: number
    y: number
    text: string
    mapping: PreviewMappingItem | null
}

interface UseVariableBindingOptions {
    previewMapping: PreviewMappingItem[]
    previewHtml: string  // 用于触发 DOM 重渲染后的高亮同步
    variables: VariableItem[]
    onVariablesChange: (variables: VariableItem[]) => void
}

export function useVariableBinding({
    previewMapping,
    previewHtml,
    variables,
    onVariablesChange,
}: UseVariableBindingOptions) {
    const { toast } = useToast()
    const [selection, setSelection] = useState<SelectionState | null>(null)
    const previewRef = useRef<HTMLDivElement>(null)

    // 同步所有已绑定变量的高亮状态
    useEffect(() => {
        // 延迟执行，确保 DOM 已渲染
        const timer = setTimeout(() => {
            // 移除所有现有的 variable-bound 高亮
            document.querySelectorAll('[data-id].variable-bound').forEach(el => {
                el.classList.remove('variable-bound')
            })
            // 为所有有 location.mapping_id 的变量添加高亮
            variables.forEach(v => {
                if (v.location?.mapping_id) {
                    const el = document.querySelector(`[data-id="${v.location.mapping_id}"]`)
                    if (el) {
                        el.classList.add('variable-bound')
                    }
                }
            })
        }, 100)
        return () => clearTimeout(timer)
    }, [variables, previewHtml])  // 依赖 previewHtml 确保 DOM 重渲染后重新应用高亮

    const buildLocation = useCallback((mapping: PreviewMappingItem): TemplateVariableLocation => {
        if (mapping.type === 'table_cell') {
            return {
                type: 'table_cell',
                table_index: mapping.table_index,
                row_index: mapping.row_index,
                cell_index: mapping.cell_index,
                selected_text: mapping.text || '',
                mapping_id: mapping.id,
            }
        }
        return {
            type: 'paragraph',
            paragraph_index: mapping.paragraph_index,
            selected_text: mapping.text || '',
            mapping_id: mapping.id,
        }
    }, [])

    // 查找当前位置已绑定的变量
    const getExistingVariable = useCallback((): VariableItem | null => {
        if (!selection?.mapping) return null
        const mappingId = selection.mapping.id
        return variables.find(v => v.location?.mapping_id === mappingId) || null
    }, [selection, variables])

    const handleMouseUp = useCallback((e: React.MouseEvent) => {
        if (!previewRef.current) return

        // 检查点击目标是否在 FloatingToolbar 内部（防止穿透）
        const target = e.target as HTMLElement
        if (target.closest('.fixed.z-\\[9999\\]')) {
            return // 忽略 FloatingToolbar 内部的点击
        }

        const mappingId = target.getAttribute('data-id') || target.closest('[data-id]')?.getAttribute('data-id')

        if (mappingId) {
            const mapping = previewMapping.find(m => m.id === mappingId)
            if (mapping) {
                const rect = (target.closest('[data-id]') as HTMLElement).getBoundingClientRect()
                setSelection({
                    visible: true,
                    x: rect.left + rect.width / 2,
                    y: rect.top,
                    text: mapping.text || '',
                    mapping: mapping
                })
                return
            }
        }

        setSelection(null)
    }, [previewMapping])

    // 创建新变量并绑定到当前位置
    const handleCreateAndBind = useCallback((variable: VariableItem) => {
        if (!selection?.mapping) return

        const location = buildLocation(selection.mapping)
        const newVariable: VariableItem = {
            ...variable,
            location,
        }

        // 检查变量名是否重复
        if (variables.some(v => v.name === variable.name)) {
            toast({
                type: 'error',
                title: '变量名重复',
                description: `已存在名为 "${variable.name}" 的变量`,
            })
            return
        }

        onVariablesChange([...variables, newVariable])
        setSelection(null)
        toast({
            type: 'success',
            title: '变量已创建并绑定',
            description: `"${selection.text}" → ${variable.name}`,
        })

        // 立即高亮该位置
        setTimeout(() => {
            const mappingId = selection.mapping?.id
            if (mappingId) {
                const el = document.querySelector(`[data-id="${mappingId}"]`)
                if (el) {
                    el.classList.add('variable-bound')
                }
            }
        }, 50)
    }, [selection, variables, onVariablesChange, buildLocation, toast])

    // 更新已绑定的变量
    const handleUpdateBinding = useCallback((oldName: string, variable: VariableItem) => {
        if (!selection?.mapping) return

        const location = buildLocation(selection.mapping)

        // 检查新名称是否与其他变量重复
        if (oldName !== variable.name && variables.some(v => v.name === variable.name)) {
            toast({
                type: 'error',
                title: '变量名重复',
                description: `已存在名为 "${variable.name}" 的变量`,
            })
            return
        }

        const next = variables.map(v =>
            v.name === oldName ? { ...variable, location } : v
        )
        onVariablesChange(next)
        setSelection(null)
        toast({
            type: 'success',
            title: '变量已更新',
            description: `变量 "${variable.name}" 已保存`,
        })
    }, [selection, variables, onVariablesChange, buildLocation, toast])

    // 解绑变量（从变量中移除 location，但保留变量）
    const handleUnbind = useCallback((varName: string) => {
        const next = variables.map(v =>
            v.name === varName ? { ...v, location: undefined } : v
        )
        onVariablesChange(next)
        setSelection(null)
        toast({
            type: 'info',
            title: '已解绑',
            description: `变量 "${varName}" 已从该位置解绑`,
        })
    }, [variables, onVariablesChange, toast])

    const clearSelection = useCallback(() => {
        setSelection(null)
    }, [])

    // 强制刷新高亮（用于弹窗关闭等场景）
    const refreshHighlight = useCallback(() => {
        setTimeout(() => {
            document.querySelectorAll('[data-id].variable-bound').forEach(el => {
                el.classList.remove('variable-bound')
            })
            variables.forEach(v => {
                if (v.location?.mapping_id) {
                    const el = document.querySelector(`[data-id="${v.location.mapping_id}"]`)
                    if (el) {
                        el.classList.add('variable-bound')
                    }
                }
            })
        }, 150)  // 稍长的延迟确保 DOM 已更新
    }, [variables])

    return {
        selection,
        previewRef,
        handleMouseUp,
        handleCreateAndBind,
        handleUpdateBinding,
        handleUnbind,
        getExistingVariable,
        clearSelection,
        refreshHighlight,
    }
}

