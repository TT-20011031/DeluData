/**
 * 任务计划管理 Hook
 * 
 * 管理任务面板状态，提供回调函数供 useChatSSE 分发事件
 */
import { useState, useCallback, useEffect } from 'react'
import { chatService } from '@/services/chatService'
import type { TaskPlan, TaskStep, ThoughtNode, SystemNode, SubStep } from '@/types/chat'
import { useMissionStore } from '@/stores/missionStore'

type ArtifactStatus = 'pending' | 'generating' | 'completed' | 'error' | 'cancelled' | 'invalid_data'

function isTerminalWorker(worker: string): boolean {
    return worker === 'chart_worker' || worker === 'office_worker'
}

function resolveArtifactStatus(
    stepStatus?: string,
    hasResultError: boolean = false,
    hasResultPayload: boolean = false,
    planStatus?: string
): ArtifactStatus | undefined {
    if (hasResultError) return 'error'

    switch (stepStatus) {
        case 'completed':
            return 'completed'
        case 'error':
            return 'error'
        case 'cancelled':
            return 'cancelled'
        case 'invalid_data':
            return 'invalid_data'
        case 'running':
            return 'generating'
        case 'pending':
        case 'waiting':
        case 'loading':
            return planStatus === 'completed' || planStatus === 'error' ? 'cancelled' : 'pending'
        case 'skipped':
            return 'cancelled'
        default:
            if (hasResultPayload) return 'completed'
            return planStatus === 'completed' || planStatus === 'error' ? 'cancelled' : undefined
    }
}

function extractArtifactData(raw: unknown): Record<string, unknown> | undefined {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        return undefined
    }
    return raw as Record<string, unknown>
}

export interface UseTaskPlannerReturn {
    // State
    taskPlan: TaskPlan | null
    isExecuting: boolean
    isPanelExpanded: boolean

    // Setters
    setTaskPlan: React.Dispatch<React.SetStateAction<TaskPlan | null>>
    setIsExecuting: React.Dispatch<React.SetStateAction<boolean>>
    setIsPanelExpanded: React.Dispatch<React.SetStateAction<boolean>>

    // SSE 回调函数
    handleStepUpdate: (payload: StepUpdatePayload) => void
    handlePlanComplete: (payload: PlanCompletePayload) => void
    handlePlanUpdate: (payload: PlanUpdatePayload) => void
    handlePlanStatus: (status: string) => void
    handleAiThought: (payload: AiThoughtPayload) => void
    handleThinkingLog: (log: string) => void

    // Actions
    confirmPlan: (steps: TaskStep[]) => Promise<void>
    cancelPlan: () => void
    removeStep: (index: number) => void
    restorePlan: (sessionId: string) => Promise<void>
}

// Payload 类型
export interface StepUpdatePayload {
    id?: string
    step_id?: string
    status?: string
    label?: string
    result?: string
    parent_step_id?: string
}

export interface PlanCompletePayload {
    session_id?: string
    plan_id?: string
    summary?: string
    steps?: TaskStep[]
    status?: string
    selected_skill_name?: string
}

export interface PlanUpdatePayload {
    action?: 'append' | 'replace' | 'nest'  // [嵌套化] 新增 nest 动作
    steps?: TaskStep[]
    status?: 'draft' | 'confirmed' | 'executing'
    parent_step_id?: string  // [嵌套化] 父任务 ID
}

export interface AiThoughtPayload {
    id?: string
    thought?: string
    verdict?: string
    round_index?: number
    target_step_id?: string  // [嵌套化] 关联的任务 ID
}

// ========== [嵌套化] 递归更新辅助函数 ==========
function updateStepInTree(
    steps: TaskStep[],
    targetId: string,
    updater: (step: TaskStep) => TaskStep
): TaskStep[] {
    return steps.map(step => {
        if (step.step_id === targetId) {
            return updater(step)
        }
        if (step.retrySteps?.length) {
            return {
                ...step,
                retrySteps: updateStepInTree(step.retrySteps, targetId, updater)
            }
        }
        return step
    })
}

/**
 * 任务计划管理 Hook
 */
export function useTaskPlanner(sessionId: string | null): UseTaskPlannerReturn {
    const [taskPlan, setTaskPlan] = useState<TaskPlan | null>(null)
    const [isExecuting, setIsExecuting] = useState(false)
    const [isPanelExpanded, setIsPanelExpanded] = useState(true)

    const { startMission, endMission } = useMissionStore()

    // 恢复任务计划（会话切换时调用）
    const restorePlan = useCallback(async (sid: string) => {
        // [FIX] 导入 chatStore 用于恢复 artifacts
        const { initArtifactsFromPlan, updateArtifactStatus } = await import('@/stores/chatStore').then(m => m.useChatStore.getState())

        try {
            const data = await chatService.getSessionPlan(sid)
            if (data && data.status !== 'none' && data.steps && data.steps.length > 0) {
                const status = data.status as TaskPlan['status']

                // [修复] 将带有 target_step_id 的反思分配到对应步骤的 inlineThoughts
                const allThoughts = data.thought_nodes?.map(t => ({
                    id: t.id,
                    thought: t.thought,
                    verdict: t.verdict as ThoughtNode['verdict'],
                    roundIndex: t.round_index,
                    timestamp: new Date().toISOString(),
                    targetStepId: t.target_step_id  // [修复] 保留关联信息
                })) || []

                // 分离有 targetStepId 的和没有的
                const globalThoughts = allThoughts.filter(t => !t.targetStepId)
                const nestedThoughts = allThoughts.filter(t => t.targetStepId)

                // 将嵌套反思分配到对应步骤的 inlineThoughts
                const stepsWithThoughts = data.steps.map(step => {
                    const stepThoughts = nestedThoughts.filter(t => t.targetStepId === step.step_id)
                    return stepThoughts.length > 0
                        ? { ...step, inlineThoughts: stepThoughts }
                        : step
                })

                setTaskPlan({
                    session_id: sid,
                    plan_id: data.plan_id,
                    summary: data.summary,
                    steps: stepsWithThoughts,  // [修复] 使用已分配反思的步骤
                    status: status,
                    selected_skill_name: data.selected_skill_name,
                    thoughtNodes: globalThoughts  // [修复] 只保留全局反思
                })

                if (status === 'executing') {
                    setIsExecuting(true)
                }

                // [FIX] 恢复 artifact TABs
                const roundIndex = data.round_index || 0
                if (roundIndex > 0) {
                    // 先初始化骨架屏
                    initArtifactsFromPlan(roundIndex, data.steps)

                    // [恢复兜底] 以 step.status 为主，execution_results 为数据补充
                    const terminalResultByStepId = new Map<
                        string,
                        { result?: unknown; error?: string; worker?: string }
                    >()

                    for (const result of data.execution_results || []) {
                        if (isTerminalWorker(result.worker || '')) {
                            terminalResultByStepId.set(result.step_id, result)
                        }
                    }

                    for (const step of data.steps) {
                        if (!isTerminalWorker(step.worker || '')) continue

                        const result = terminalResultByStepId.get(step.step_id)
                        const hasResultPayload = Boolean(result && (result.error || result.result !== undefined))
                        const artifactStatus = resolveArtifactStatus(
                            step.status,
                            Boolean(result?.error),
                            hasResultPayload,
                            status
                        )
                        if (!artifactStatus) continue

                        const artifactData = extractArtifactData(result?.result) || {}
                        const fallbackTitle = step.description?.slice(0, 30)
                            || (step.worker === 'chart_worker' ? '图表任务' : '文档任务')

                        updateArtifactStatus(roundIndex, step.step_id, artifactStatus, {
                            ...artifactData,
                            title: (artifactData.title as string) || fallbackTitle
                        })
                    }
                }

                startMission()
                setIsPanelExpanded(true)
            }
        } catch (error) {
        }
    }, [startMission])

    // 会话切换时重置状态
    useEffect(() => {
        setTaskPlan(null)
        setIsExecuting(false)

        if (sessionId) {
            restorePlan(sessionId)
        }
    }, [sessionId, restorePlan])

    // SSE 回调：步骤更新
    const handleStepUpdate = useCallback((payload: StepUpdatePayload) => {
        const stepId = payload.id || payload.step_id
        if (!stepId) return

        setTaskPlan(prev => {
            if (!prev) return null

            // 检查是否是子步骤
            const isSubStep = stepId.startsWith('xiyan-') || stepId.startsWith('doc_')

            if (isSubStep) {
                let subStepName = stepId
                if (stepId.startsWith('xiyan-')) {
                    subStepName = stepId.replace('xiyan-', '')
                } else if (stepId.startsWith('doc_')) {
                    const docStepNames: Record<string, string> = {
                        'doc_rewrite': '理解问题',
                        'doc_retrieval': '翻阅知识库',
                        'doc_rerank': '筛选内容',
                        'doc_expand': '补充上下文'
                    }
                    subStepName = docStepNames[stepId] || stepId.replace('doc_', '')
                }

                const mapStatus = (s: string): SubStep['status'] => {
                    if (s === 'done' || s === 'completed') return 'done'
                    if (s === 'running') return 'running'
                    if (s === 'error') return 'error'
                    return 'pending'
                }

                const updatedSteps = prev.steps.map(step => {
                    const isParent = payload.parent_step_id
                        ? step.step_id === payload.parent_step_id
                        : (step.status === 'running' || step.worker === 'sql_worker' || step.worker === 'doc_worker')

                    if (isParent) {
                        const existingSubSteps = [...(step.subSteps || [])]
                        const existingIndex = existingSubSteps.findIndex(s => s.id === stepId)

                        const newSubStep: SubStep = {
                            id: stepId,
                            name: subStepName,
                            status: mapStatus(payload.status || 'pending'),
                            detail: payload.label || ''
                        }

                        if (existingIndex >= 0) {
                            existingSubSteps[existingIndex] = newSubStep
                        } else {
                            existingSubSteps.push(newSubStep)
                        }
                        return { ...step, subSteps: existingSubSteps }
                    }
                    return step
                })
                return { ...prev, steps: updatedSteps }
            } else {
                // 检查是否是系统节点
                const isSystemNode = stepId === 'reflector' || stepId === 'failsafe'

                if (isSystemNode) {
                    const systemNodes = [...(prev.systemNodes || [])]
                    const existingIndex = systemNodes.findIndex(n => n.id === stepId)
                    const nodeLabel = stepId === 'reflector' ? '质量检查' : '兜底回复'

                    if (existingIndex >= 0) {
                        const existing = systemNodes[existingIndex]
                        systemNodes[existingIndex] = {
                            ...existing,
                            label: payload.label || existing.label,
                            status: (payload.status || 'pending') as SystemNode['status']
                        }
                    } else {
                        systemNodes.push({
                            id: stepId,
                            label: payload.label || nodeLabel,
                            status: (payload.status || 'pending') as SystemNode['status']
                        })
                    }
                    return { ...prev, systemNodes }
                } else {
                    // 普通步骤更新
                    const updatedSteps = prev.steps.map(s =>
                        s.step_id === stepId
                            ? { ...s, status: (payload.status || 'pending') as TaskStep['status'], result: payload.result }
                            : s
                    )
                    return { ...prev, steps: updatedSteps }
                }
            }
        })
    }, [])

    // SSE 回调：计划完成
    const handlePlanComplete = useCallback((payload: PlanCompletePayload) => {
        const planStatus = (payload.status as TaskPlan['status']) || 'draft'

        if (payload.steps && payload.steps.length > 0) {
            setTaskPlan(prevPlan => {
                if (prevPlan && prevPlan.plan_id && payload.plan_id && prevPlan.plan_id !== payload.plan_id) {
                }

                return {
                    session_id: payload.session_id || sessionId || '',
                    plan_id: payload.plan_id || prevPlan?.plan_id || '',
                    summary: payload.summary || '',
                    steps: payload.steps!,
                    status: planStatus,
                    selected_skill_name: payload.selected_skill_name || prevPlan?.selected_skill_name,
                }
            })
            startMission()
            setIsPanelExpanded(true)

            if (planStatus === 'completed') {
                setIsExecuting(false)
                endMission()
            }
        } else {
            setTaskPlan(prev => prev ? { ...prev, status: 'completed' } : prev)
            setIsExecuting(false)
            endMission()
        }
    }, [sessionId, startMission, endMission])

    // SSE 回调：计划状态更新
    const handlePlanStatus = useCallback((status: string) => {
        const newStatus = status as TaskPlan['status']
        setTaskPlan(prev => prev ? {
            ...prev,
            status: newStatus,
            summary: newStatus === 'executing' ? '' : prev.summary
        } : null)
        if (newStatus === 'suspended' || newStatus === 'completed' || newStatus === 'error') {
            setIsExecuting(false)
        } else if (newStatus === 'confirmed' || newStatus === 'executing') {
            setIsExecuting(true)
        }
    }, [])

    // SSE 回调：计划动态更新（追加/替换/嵌套步骤）
    const handlePlanUpdate = useCallback((payload: PlanUpdatePayload) => {
        if (!payload.steps || payload.steps.length === 0) return

        setTaskPlan(prev => {
            if (!prev) return null

            const action = payload.action || 'append'

            // [扁平化] 补充任务直接追加到列表（后端已处理插入位置）
            if (action === 'append') {
                const existingIds = new Set(prev.steps.map(s => s.step_id))
                const newSteps = payload.steps!.filter(s => !existingIds.has(s.step_id))

                if (newSteps.length > 0) {
                    const stepsWithWaiting = newSteps.map(s => ({
                        ...s,
                        status: s.status || 'waiting' as const
                    }))
                    return {
                        ...prev,
                        steps: [...prev.steps, ...stepsWithWaiting],
                        status: payload.status || prev.status
                    }
                }
            } else if (action === 'replace') {
                // [修复] 合并新旧 steps，保留原有的 inlineThoughts 和 subSteps
                const oldStepsMap = new Map(prev.steps.map(s => [s.step_id, s]))
                const mergedSteps = payload.steps!.map(newStep => {
                    const oldStep = oldStepsMap.get(newStep.step_id)
                    if (oldStep) {
                        return {
                            ...newStep,
                            inlineThoughts: oldStep.inlineThoughts || newStep.inlineThoughts,
                            subSteps: oldStep.subSteps || newStep.subSteps
                        }
                    }
                    return newStep
                })
                return {
                    ...prev,
                    steps: mergedSteps,
                    status: payload.status || prev.status
                }
            }
            return prev
        })
        if (payload.status === 'confirmed' || payload.status === 'executing') {
            setIsExecuting(true)
        }
    }, [])

    // SSE 回调：AI 思考节点
    const handleAiThought = useCallback((payload: AiThoughtPayload) => {
        if (!payload.thought && payload.verdict !== 'loading') return

        setTaskPlan(prev => {
            if (!prev) return null

            const newThought: ThoughtNode = {
                id: payload.id || `thought-${Date.now()}`,
                thought: payload.thought || '',
                verdict: (payload.verdict || 'fail') as ThoughtNode['verdict'],
                roundIndex: payload.round_index || 0,
                timestamp: new Date().toISOString(),
                targetStepId: payload.target_step_id  // [嵌套化] 保存目标步骤
            }

            // [嵌套化] 如果有 target_step_id，插入到对应任务的 inlineThoughts
            if (payload.target_step_id) {
                // 先移除对应 loading 节点
                const removeLoading = (steps: TaskStep[]): TaskStep[] => {
                    return steps.map(step => {
                        let newStep = step
                        if (step.step_id === payload.target_step_id) {
                            const filtered = (step.inlineThoughts || []).filter(
                                t => !(t.verdict === 'loading')
                            )
                            newStep = { ...step, inlineThoughts: filtered }
                        }
                        if (newStep.retrySteps?.length) {
                            newStep = { ...newStep, retrySteps: removeLoading(newStep.retrySteps) }
                        }
                        return newStep
                    })
                }

                const stepsWithoutLoading = payload.verdict !== 'loading'
                    ? removeLoading(prev.steps)
                    : prev.steps

                return {
                    ...prev,
                    steps: updateStepInTree(stepsWithoutLoading, payload.target_step_id, step => ({
                        ...step,
                        inlineThoughts: [...(step.inlineThoughts || []), newThought]
                    }))
                }
            }

            // 兆底：没有 target_step_id 时放入全局 thoughtNodes
            if (payload.verdict !== 'loading') {
                const filteredNodes = (prev.thoughtNodes || []).filter(
                    t => !(t.verdict === 'loading' && t.roundIndex === newThought.roundIndex)
                )
                return {
                    ...prev,
                    thoughtNodes: [...filteredNodes, newThought]
                }
            }

            return {
                ...prev,
                thoughtNodes: [...(prev.thoughtNodes || []), newThought]
            }
        })
    }, [])

    // SSE 回调：思考日志
    const handleThinkingLog = useCallback((log: string) => {
        const isReflector = log.includes('[Reflector]')
        const isFailSafe = log.includes('[FailSafe]')
        const nodeId = isReflector ? 'reflector' : isFailSafe ? 'failsafe' : null

        if (!nodeId) return

        setTaskPlan(prev => {
            if (!prev) return null

            const systemNodes = [...(prev.systemNodes || [])]
            const existingIndex = systemNodes.findIndex(n => n.id === nodeId)

            if (existingIndex >= 0) {
                const existing = systemNodes[existingIndex]
                systemNodes[existingIndex] = {
                    ...existing,
                    thinking: (existing.thinking || '') + log + '\n'
                }
            } else {
                systemNodes.push({
                    id: nodeId,
                    label: nodeId === 'reflector' ? '质量检查' : '兜底回复',
                    status: 'running',
                    thinking: log + '\n'
                })
            }
            return { ...prev, systemNodes }
        })
    }, [])

    // 确认执行计划
    const confirmPlan = useCallback(async (steps: TaskStep[]) => {
        if (!taskPlan || !sessionId) return

        const stepsWithTemplateMode = steps.map(step => {
            if (step.worker !== 'office_worker') return step
            const params = { ...(step.params || {}) }
            if (params.template_id && !params.template_mode) {
                params.template_mode = 'draft'
            }
            return { ...step, params }
        })

        setIsExecuting(true)
        setTaskPlan({ ...taskPlan, steps: stepsWithTemplateMode, status: 'executing' })

        try {
            await chatService.confirmPlan({
                session_id: taskPlan.session_id,
                plan_id: taskPlan.plan_id,
                modified_steps: stepsWithTemplateMode
            })
        } catch (error) {
            console.error('Confirm plan error:', error)
            setIsExecuting(false)
            setTaskPlan({ ...taskPlan, status: 'draft' })
            throw error
        }
    }, [taskPlan, sessionId])

    // 取消计划
    const cancelPlan = useCallback(() => {
        setTaskPlan(null)
        setIsExecuting(false)
        endMission()
    }, [endMission])

    // 移除步骤
    const removeStep = useCallback((index: number) => {
        setTaskPlan(prev => {
            if (!prev) return null
            const newSteps = prev.steps.filter((_, i) => i !== index)
            return { ...prev, steps: newSteps }
        })
    }, [])

    return {
        taskPlan,
        isExecuting,
        isPanelExpanded,
        setTaskPlan,
        setIsExecuting,
        setIsPanelExpanded,
        handleStepUpdate,
        handlePlanComplete,
        handlePlanUpdate,
        handlePlanStatus,
        handleAiThought,
        handleThinkingLog,
        confirmPlan,
        cancelPlan,
        removeStep,
        restorePlan,
    }
}
