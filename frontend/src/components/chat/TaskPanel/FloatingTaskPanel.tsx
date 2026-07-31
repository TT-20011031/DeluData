/**
 * 悬浮任务面板
 * 
 * 显示任务计划、执行进度，支持折叠/展开
 * [嵌套化] 使用递归 StepItem 组件渲染任务树
 */
import { useState, useEffect, useMemo } from 'react'
import { Loader2, Play, ChevronDown, ChevronUp, CheckCircle2, AlertCircle, Trash2, MessageSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { USER_SELECTABLE_WORKERS, WORKER_REQUIRES_CONFIG } from '@/config/workerConfig'
import { useDBStore } from '@/stores/dbStore'
import { knowledgeService } from '@/services/knowledgeService'
import type { TaskPlan, TaskStep, ThoughtNode } from '@/types/chat'
import { InterruptInput, type InterruptData } from '@/components/chat/InterruptInput'
import { StepItem } from './StepItem'

interface FloatingTaskPanelProps {
    plan: TaskPlan
    isExpanded: boolean
    onToggle: () => void
    onConfirm: (steps: TaskStep[]) => void
    onCancel: () => void
    isExecuting: boolean
    onRemoveStep: (index: number) => void
    isAppending?: boolean
    scopeConflictMessage?: string | null
    interruptData?: InterruptData | null
    currentSessionId?: string | null
    onInterruptSubmit?: (input: string) => Promise<void>
    onInterruptCancel?: () => void
    isSubmittingInterrupt?: boolean
}

export function FloatingTaskPanel({
    plan,
    isExpanded,
    onToggle,
    onConfirm,
    onCancel,
    isExecuting,
    onRemoveStep,
    isAppending = false,
    scopeConflictMessage = null,
    interruptData = null,
    currentSessionId = null,
    onInterruptSubmit,
    onInterruptCancel,
    isSubmittingInterrupt = false,
}: FloatingTaskPanelProps) {
    const [localSteps, setLocalSteps] = useState<TaskStep[]>(plan.steps)

    // ========== 工具可用性检测 ==========
    const isDBConnected = useDBStore(state => state.isConnected)
    const [hasKnowledgeDocs, setHasKnowledgeDocs] = useState(true) // 默认可用，异步检测

    useEffect(() => {
        let cancelled = false
        if (plan.status === 'draft') {
            knowledgeService.fetchFolderChildren('', 'all', 1)
                .then(nodes => { if (!cancelled) setHasKnowledgeDocs(nodes.length > 0) })
                .catch(() => { if (!cancelled) setHasKnowledgeDocs(false) })
        }
        return () => { cancelled = true }
    }, [plan.status])

    // 计算禁用的 worker 集合
    const disabledWorkers = useMemo(() => {
        const disabled = new Map<string, string>()
        for (const [worker, configType] of Object.entries(WORKER_REQUIRES_CONFIG)) {
            if (configType === 'database' && !isDBConnected) {
                disabled.set(worker, '未连接数据库')
            } else if (configType === 'knowledge' && !hasKnowledgeDocs) {
                disabled.set(worker, '未上传知识库文档')
            }
        }
        return disabled
    }, [isDBConnected, hasKnowledgeDocs])

    useEffect(() => {
        setLocalSteps(plan.steps)
    }, [plan.steps])

    const finishedStatuses = new Set(['completed', 'invalid_data', 'cancelled', 'skipped'])
    const completedCount = localSteps.filter(s => finishedStatuses.has(s.status)).length
    const runningStep = localSteps.find(s => s.status === 'running')
    const hasSemanticClarification = interruptData?.signal_type === 'semantic_clarification'
    const panelTitle = hasSemanticClarification
        ? '需要确认口径'
        : plan.status === 'draft' ? '待确认任务计划'
            : plan.status === 'completed' ? '任务已完成'
                : plan.status === 'error' ? '执行出错'
                    : isAppending ? '补充任务中...'
                        : `执行中 ${completedCount}/${localSteps.length}`
    const progress = localSteps.length > 0 ? Math.round((completedCount / localSteps.length) * 100) : 0

    const handleRemove = (index: number) => {
        const newSteps = localSteps.filter((_, i) => i !== index)
        setLocalSteps(newSteps)
        onRemoveStep(index)
    }

    const handleWorkerChange = (index: number, worker: string) => {
        const newSteps = [...localSteps]
        newSteps[index] = { ...newSteps[index], worker }
        setLocalSteps(newSteps)
    }

    const handleAddStep = () => {
        // 选择第一个未被禁用的 worker 作为默认值
        const availableWorkers = Object.keys(USER_SELECTABLE_WORKERS).filter(w => !disabledWorkers.has(w))
        const defaultWorker = availableWorkers[0] || Object.keys(USER_SELECTABLE_WORKERS)[0]
        const newStep: TaskStep = {
            step_id: `custom-${Date.now()}`,
            description: '新步骤（请编辑）',
            worker: defaultWorker,
            status: 'pending',
            editable: true
        }
        setLocalSteps([...localSteps, newStep])
    }

    // 自动滚动跟随
    useEffect(() => {
        if (runningStep) {
            const scrollContainer = document.getElementById('task-panel-scroll')
            const runningElement = scrollContainer?.querySelector('[data-running="true"]')
            if (scrollContainer && runningElement) {
                runningElement.scrollIntoView({ behavior: 'smooth', block: 'center' })
            }
        }
    }, [runningStep?.step_id])

    return (
        <div className="absolute bottom-full left-0 right-0 mb-2">
            <div className="bg-manus-secondary border border-accent/30 rounded-xl shadow-lg overflow-hidden w-full">
                {/* 折叠头部 */}
                <div
                    className="flex items-center justify-between p-3 cursor-pointer hover:bg-manus-tertiary/30 transition-colors"
                    onClick={onToggle}
                >
                    <div className="flex items-center gap-3">
                        <div className="flex items-center gap-2">
                            {hasSemanticClarification ? (
                                <MessageSquare className="h-4 w-4 text-accent" />
                            ) : plan.status === 'completed' ? (
                                <CheckCircle2 className="h-4 w-4 text-success" />
                            ) : plan.status === 'error' ? (
                                <AlertCircle className="h-4 w-4 text-error" />
                            ) : isExecuting ? (
                                <Loader2 className="h-4 w-4 text-warning animate-spin" />
                            ) : (
                                <Play className="h-4 w-4 text-accent" />
                            )}
                            <span className="text-sm font-medium text-manus-text">
                                {panelTitle}
                            </span>
                            <span className="hidden">
                                {plan.status === 'draft' ? '待确认任务计划' :
                                    plan.status === 'completed' ? '任务已完成' :
                                        plan.status === 'error' ? '执行出错' :
                                            isAppending ? '补充任务中...' :
                                                `执行中 ${completedCount}/${localSteps.length}`}
                            </span>
                        </div>
                        {isExecuting && (
                            <div className="w-24 h-1.5 bg-manus-tertiary rounded-full overflow-hidden">
                                <div
                                    className="h-full bg-accent transition-all duration-300"
                                    style={{ width: `${progress}%` }}
                                />
                            </div>
                        )}
                    </div>
                    <div className="flex items-center gap-2">
                        {runningStep && (
                            <span className="text-xs text-manus-muted truncate max-w-[150px]">
                                {runningStep.description}
                            </span>
                        )}
                        {isExpanded ? (
                            <ChevronDown className="h-4 w-4 text-manus-muted" />
                        ) : (
                            <ChevronUp className="h-4 w-4 text-manus-muted" />
                        )}
                    </div>
                </div>

                {/* 展开内容 */}
                {isExpanded && (
                    <div id="task-panel-scroll" className="border-t border-manus-border p-4 space-y-4 max-h-[60vh] overflow-y-auto scroll-smooth">
                        {plan.summary && (
                            <p className="text-sm text-manus-muted">{plan.summary}</p>
                        )}
                        {scopeConflictMessage && (
                            <div className="text-xs rounded-md border border-amber-500/30 bg-amber-500/10 text-amber-200 px-3 py-2">
                                {scopeConflictMessage}
                            </div>
                        )}

                        {/* [嵌套化] 任务列表 */}
                        <div className="space-y-2">
                            {plan.status === 'draft' ? (
                                // Draft 模式：可编辑的扁平列表
                                localSteps.map((step, index) => (
                                    <div
                                        key={step.step_id}
                                        className="flex flex-col gap-2 p-3 rounded-lg bg-manus-tertiary/50 border border-transparent"
                                    >
                                        <div className="flex items-center gap-3">
                                            <input
                                                className="flex-1 bg-transparent border-b border-transparent hover:border-manus-border focus:border-accent/50 focus:outline-none text-sm px-1 py-0.5 transition-colors"
                                                value={step.description}
                                                onChange={(e) => {
                                                    const newSteps = [...localSteps]
                                                    newSteps[index] = { ...newSteps[index], description: e.target.value }
                                                    setLocalSteps(newSteps)
                                                }}
                                            />
                                            <select
                                                className="text-xs text-manus-subtle px-2 py-0.5 bg-manus-tertiary rounded border border-transparent hover:border-manus-border focus:border-accent/50 focus:outline-none cursor-pointer"
                                                value={step.worker}
                                                onChange={(e) => handleWorkerChange(index, e.target.value)}
                                            >
                                                {Object.entries(USER_SELECTABLE_WORKERS).map(([key, label]) => {
                                                    const reason = disabledWorkers.get(key)
                                                    return (
                                                        <option key={key} value={key} disabled={!!reason} title={reason || undefined}>
                                                            {label}
                                                        </option>
                                                    )
                                                })}
                                            </select>
                                            <Button
                                                size="icon"
                                                variant="ghost"
                                                className="h-6 w-6 text-manus-muted hover:text-error"
                                                onClick={(e) => {
                                                    e.stopPropagation()
                                                    handleRemove(index)
                                                }}
                                            >
                                                <Trash2 className="h-3 w-3" />
                                            </Button>
                                        </div>
                                    </div>
                                ))
                            ) : (
                                // 执行模式：使用递归 StepItem 渲染（支持嵌套反思和重试）
                                localSteps.map(step => (
                                    <div key={step.step_id} className="space-y-2">
                                        <StepItem step={step} />
                                        {hasSemanticClarification &&
                                            currentSessionId &&
                                            interruptData?.target_step_id === step.step_id &&
                                            onInterruptSubmit && (
                                                <div className="ml-7">
                                                    <InterruptInput
                                                        data={interruptData}
                                                        sessionId={currentSessionId}
                                                        planId={currentSessionId}
                                                        onSubmit={onInterruptSubmit}
                                                        onCancel={onInterruptCancel}
                                                        isSubmitting={isSubmittingInterrupt}
                                                    />
                                                </div>
                                            )}
                                    </div>
                                ))
                            )}
                        </div>

                        {/* 兜底：全局思考节点（没有 targetStepId 的） */}
                        {plan.thoughtNodes?.filter((t: ThoughtNode) => !t.targetStepId).map((thought: ThoughtNode) => (
                            <div
                                key={thought.id}
                                className={cn(
                                    "flex items-start gap-2 p-3 rounded-lg mt-2 animate-in fade-in slide-in-from-left-2 duration-300",
                                    thought.verdict === 'pass' && "bg-accent/5",
                                    (thought.verdict === 'fail' || thought.verdict === 'partial') && "bg-amber-500/10",
                                    thought.verdict === 'break' && "bg-green-500/10",
                                    thought.verdict === 'loading' && "bg-manus-tertiary/30"
                                )}
                            >
                                {thought.verdict === 'loading' ? (
                                    <div className="flex items-center gap-2 w-full">
                                        <Loader2 className="h-4 w-4 text-manus-muted animate-spin flex-shrink-0" />
                                        <div className="flex-1 space-y-2">
                                            <div className="h-3 bg-gradient-to-r from-manus-tertiary via-manus-secondary/20 to-manus-tertiary rounded animate-pulse" style={{ width: '80%' }} />
                                            <div className="h-3 bg-gradient-to-r from-manus-tertiary via-manus-secondary/20 to-manus-tertiary rounded animate-pulse" style={{ width: '60%' }} />
                                        </div>
                                    </div>
                                ) : (
                                    <>
                                        {(thought.verdict === 'fail' || thought.verdict === 'partial') && (
                                            <AlertCircle className="h-4 w-4 text-amber-500 flex-shrink-0 mt-0.5" />
                                        )}
                                        {thought.verdict === 'break' && (
                                            <CheckCircle2 className="h-4 w-4 text-green-500 flex-shrink-0 mt-0.5" />
                                        )}
                                        <p className="text-sm text-manus-text break-words">{thought.thought}</p>
                                    </>
                                )}
                            </div>
                        ))}

                        {/* 操作按钮 */}
                        {plan.status === 'draft' && (
                            <div className="flex gap-2 pt-2">
                                <Button
                                    onClick={() => onConfirm(localSteps)}
                                    disabled={isExecuting || localSteps.length === 0}
                                    className="bg-accent hover:bg-accent/90 text-white"
                                >
                                    <Play className="h-4 w-4 mr-1" />
                                    确认执行
                                </Button>
                                <Button
                                    variant="outline"
                                    onClick={handleAddStep}
                                    disabled={isExecuting}
                                    className="border-manus-border text-manus-text hover:bg-manus-tertiary"
                                >
                                    添加步骤
                                </Button>
                                <Button
                                    variant="ghost"
                                    onClick={onCancel}
                                    disabled={isExecuting}
                                    className="text-manus-muted hover:text-error"
                                >
                                    取消
                                </Button>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    )
}
