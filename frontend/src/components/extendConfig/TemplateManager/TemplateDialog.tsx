/**
 * Template edit dialog.
 *
 * This is a modal-oriented variant of TemplateEditorPage and keeps
 * the same interaction flow:
 * - upload/process/preview workflow
 * - variable binding
 * - optional LLM blank-field detection
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronRight, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { useToast } from '@/components/ui/toast'
import { extendConfigService } from '@/services/extendConfigService'
import type {
    BlankFieldDetectResponse,
    CandidateField,
    TemplateDialogProps,
    VariableItem,
} from '@/types/extendConfig'

import {
    useTemplateDialogWorkflow,
    useTemplatePreview,
    useVariableBinding,
} from '@/hooks/extendConfig/useTemplateDialog'
import { useLLMDetection } from '@/hooks/extendConfig/useLLMDetection'

import { FloatingToolbar } from './FloatingToolbar'
import { CandidateFieldList } from './CandidateFieldList'
import { SettingsPanel } from './TemplateDialogPanels'
import {
    ErrorView,
    FileReadyView,
    IdleUploadView,
    PreviewSuccessView,
    ProcessingView,
} from './TemplateDialogViews'

export function TemplateDialog({
    open,
    onClose,
    form,
    onFormChange,
    variables,
    onVariablesChange,
    file,
    onFileChange,
    onSave,
    isSaving,
    isEditing,
    groups,
    templateId,
    templateFileType,
}: TemplateDialogProps) {
    const { toast } = useToast()

    const [candidates, setCandidates] = useState<CandidateField[]>([])
    const [showCandidates, setShowCandidates] = useState(false)
    const [isOptimizing, setIsOptimizing] = useState(false)

    const {
        startDetection,
        isLoading: isDetecting,
        progress: detectionProgress,
        stage: detectionStage,
    } = useLLMDetection({
        onComplete: (fields) => {
            setCandidates(fields)
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
        },
    })

    const preview = useTemplatePreview({
        templateId: templateId ?? null,
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
        onVariablesChange,
    })

    const processedPreviewHtml = useMemo(() => {
        const html = preview.previewHtml
        if (!html || variables.length === 0) return html

        try {
            const parser = new DOMParser()
            const doc = parser.parseFromString(html, 'text/html')
            variables.forEach((v) => {
                if (!v.location?.mapping_id) return
                const el = doc.querySelector(`[data-id="${v.location.mapping_id}"]`)
                if (el) el.classList.add('variable-bound')
            })
            return doc.body.innerHTML
        } catch {
            return html
        }
    }, [preview.previewHtml, variables])

    const canBind = Boolean(isEditing && templateId && templateFileType?.toLowerCase() === 'docx')
    const enableDetection = canBind

    useEffect(() => {
        if (open && canBind && !preview.previewHtml && workflowHook.workflow.status === 'idle') {
            preview.loadPreview()
        }
        if (!open) {
            preview.reset()
            binding.clearSelection()
            workflowHook.resetToIdle()
            setShowCandidates(false)
            setCandidates([])
        }
    }, [
        open,
        canBind,
        preview,
        binding,
        workflowHook,
    ])

    const handleDetectRequest = useCallback(() => {
        if (!templateId) return
        startDetection(templateId)
    }, [templateId, startDetection])

    const handleCandidatesConfirm = useCallback(
        (newVariables: VariableItem[]) => {
            const existingNames = new Set(variables.map((v) => v.name))
            const uniqueNewVars = newVariables.filter((v) => !existingNames.has(v.name))

            if (uniqueNewVars.length < newVariables.length) {
                toast({
                    type: 'warning',
                    title: '部分变量已存在',
                    description: `跳过 ${newVariables.length - uniqueNewVars.length} 个重复变量`,
                })
            }

            onVariablesChange([...variables, ...uniqueNewVars])
            setShowCandidates(false)
            setCandidates([])
            binding.refreshHighlight()
        },
        [variables, onVariablesChange, toast, binding],
    )

    const handleCandidatesCancel = useCallback(() => {
        setShowCandidates(false)
        setCandidates([])
    }, [])

    const handleHighlight = useCallback((mappingId: string | null) => {
        document.querySelectorAll('[data-id].candidate-highlight').forEach((el) => {
            el.classList.remove('candidate-highlight')
        })
        if (!mappingId) return
        const el = document.querySelector(`[data-id="${mappingId}"]`)
        if (!el) return
        el.classList.add('candidate-highlight')
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, [])

    const handleOptimize = useCallback(async () => {
        if (!templateId || candidates.length === 0) return
        setIsOptimizing(true)
        try {
            const requestData: BlankFieldDetectResponse = {
                candidates,
                total: candidates.length,
                high_confidence_count: candidates.filter((c) => c.confidence >= 0.8).length,
            }
            const result = await extendConfigService.optimizeBlankLabels(templateId, requestData)
            setCandidates(result.candidates)
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
    }, [templateId, candidates, toast])

    const handleRetry = useCallback(() => {
        if (templateId) {
            preview.loadPreview()
        } else {
            workflowHook.resetToIdle()
        }
    }, [templateId, preview, workflowHook])

    const renderMainContent = () => {
        const { status, step, progress, errorMessage } = workflowHook.workflow

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

        if (status === 'processing' || status === 'previewing') {
            return (
                <div className="flex-1 flex flex-col items-center justify-center p-12">
                    <ProcessingView step={step} progress={progress} />
                </div>
            )
        }

        if (status === 'success' && processedPreviewHtml) {
            return (
                <PreviewSuccessView
                    html={processedPreviewHtml}
                    previewRef={binding.previewRef}
                    onMouseUp={binding.handleMouseUp}
                />
            )
        }

        if (file) {
            return (
                <div className="flex-1 flex flex-col items-center justify-center p-12">
                    <FileReadyView
                        file={file}
                        onFileChange={onFileChange}
                        onSave={onSave}
                    />
                </div>
            )
        }

        return (
            <div className="flex-1 flex flex-col items-center justify-center p-12">
                <IdleUploadView onFileChange={onFileChange} />
            </div>
        )
    }

    const isProcessing =
        workflowHook.workflow.status === 'processing' ||
        workflowHook.workflow.status === 'previewing'

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent
                className="w-screen h-screen max-w-none m-0 p-0 rounded-none border-0 bg-manus flex flex-col focus:outline-none"
                hideCloseButton
            >
                <header className="flex-none h-14 border-b border-manus-border bg-manus/95 backdrop-blur z-20 px-6 flex items-center justify-between">
                    <div className="flex items-center gap-4">
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={onClose}
                            className="-ml-2 text-manus-muted hover:text-manus-text"
                        >
                            <ChevronRight className="h-5 w-5 rotate-180" />
                        </Button>
                        <div className="flex items-center gap-2">
                            <div className="font-medium text-manus-text">
                                {isEditing ? form.name || '编辑模板' : '上传新模板'}
                            </div>
                            {isProcessing && (
                                <Loader2 className="h-3 w-3 animate-spin text-manus-muted" />
                            )}
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <Button
                            onClick={onSave}
                            disabled={isSaving || isProcessing}
                            className="bg-accent hover:bg-accent/90 text-white min-w-[80px]"
                        >
                            {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : '保存'}
                        </Button>
                    </div>
                </header>

                <div className="flex-1 overflow-hidden flex flex-row">
                    <div className="flex-1 bg-manus-elevated relative overflow-hidden flex flex-col">
                        {renderMainContent()}

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

                    <SettingsPanel
                        form={form}
                        onFormChange={onFormChange}
                        variables={variables}
                        onVariablesChange={onVariablesChange}
                        groups={groups}
                        isEditing={isEditing}
                        file={file}
                        onFileChange={onFileChange}
                        templateId={templateId}
                        enableDetection={enableDetection}
                        onDetectRequest={handleDetectRequest}
                        isDetecting={isDetecting}
                        detectionProgress={detectionProgress}
                        detectionStage={detectionStage}
                    />
                </div>

                <Dialog open={showCandidates} onOpenChange={(o) => !o && handleCandidatesCancel()}>
                    <DialogContent className="max-w-4xl h-[80vh] p-0 flex flex-col">
                        <CandidateFieldList
                            candidates={candidates}
                            onConfirm={handleCandidatesConfirm}
                            onCancel={handleCandidatesCancel}
                            isLoading={isDetecting}
                            onOptimize={handleOptimize}
                            isOptimizing={isOptimizing}
                            onHighlight={handleHighlight}
                        />
                    </DialogContent>
                </Dialog>
            </DialogContent>
        </Dialog>
    )
}
