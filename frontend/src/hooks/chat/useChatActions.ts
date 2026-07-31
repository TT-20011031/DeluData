/**
 * 聊天业务动作 Hook
 * 
 * 职责：封装 ChatPage 的核心业务动作
 * - 发送消息 (handleSend)
 * - 确认计划 (handleConfirmPlan)
 * - 取消计划 (handleCancelPlan)
 * - 中断提交 (handleInterruptSubmit)
 * - 中断取消 (handleInterruptCancel)
 * 
 * 设计原则：
 * - 使用 Ref 策略处理 SSE 异步回调中的最新状态
 * - 保持单向依赖流：Actions -> Store/Planner
 */
import { useCallback, useRef, useEffect } from 'react'
import { useChatStore, type Message } from '@/stores/chatStore'
import { useMissionStore } from '@/stores/missionStore'
import { chatService } from '@/services/chatService'
import type { UseTaskPlannerReturn } from '@/hooks/chat/useTaskPlanner'
import type { TaskStep, UploadedFile } from '@/types/chat'
import type { InterruptData } from '@/components/chat/InterruptInput'
import type { QuickToolMode } from '@/components/chat/QuickToolModes'
import type { DocScope } from '@/types/docScope'
import type { ReplyModelKey } from '@/types/replyModel'
import { compactDocScope } from '@/types/docScope'
import { encodeThinkingContent, DEFAULT_THINKING_STATUS } from '@/components/chat/ThinkingStatusConfig'

// ========== 接口定义 ==========

export interface UseChatActionsOptions {
    /** 当前会话 ID */
    sessionId: string | null
    /** 任务计划器 Hook */
    planner: UseTaskPlannerReturn
    /** SSE 连接函数 */
    connectSSE: (sessionIdOverride?: string) => Promise<void>
    /** SSE 断开函数 */
    disconnectSSE: () => void
    /** Thinking 消息 ID Ref (外部注入，实现单一数据源) */
    thinkingMsgIdRef: React.MutableRefObject<string | null>
    /** 用户配置的执行模式，用于强制直连 */
    userExecutionMode?: string
    /** 深度检索开关 */
    deepSearch?: boolean
}

export interface UseChatActionsReturn {
    /** 发送消息 */
    handleSend: (options: SendMessageOptions) => Promise<void>
    /** 确认计划 */
    handleConfirmPlan: (steps: TaskStep[]) => Promise<void>
    /** 取消计划 */
    handleCancelPlan: () => void
    /** 中断提交 */
    handleInterruptSubmit: (input: string, interruptData: InterruptData) => Promise<void>
    /** 中断取消 */
    handleInterruptCancel: () => void
}

export interface SendMessageOptions {
    /** 输入消息 */
    message: string
    /** 上传的文件 */
    uploadedFile?: UploadedFile | null
    /** 直连执行模式 */
    selectedMode?: QuickToolMode | null
    /** 已选择的技能 ID */
    selectedSkillId?: string | null
    /** 发送后回调（用于清理状态） */
    onAfterSend?: () => void
    /** 本轮会话级知识库范围 */
    docScope?: DocScope | null
    /** 正常回答模型档位 */
    replyModelKey?: ReplyModelKey
    /** 会话 ID 确定后回调（用于外部把临时状态迁移到会话） */
    onSessionResolved?: (sessionId: string) => void
}

// ========== Hook 实现 ==========

export function useChatActions({
    sessionId,
    planner,
    connectSSE,
    disconnectSSE,
    thinkingMsgIdRef,
    userExecutionMode = 'auto',
    deepSearch = false,
}: UseChatActionsOptions): UseChatActionsReturn {
    // ========== Store Actions ==========
    const {
        createSession,
        addMessage,
        updateMessage,
        replaceMessage,
        finishMessageStream,
        setLoading,
    } = useChatStore()

    const { startMission, setError } = useMissionStore()

    // ========== Refs (防止闭包过时) ==========
    // thinkingMsgIdRef 由外部注入，不再内部创建
    const sessionIdRef = useRef(sessionId)
    const sendInFlightRef = useRef(false)

    // 同步 sessionId 到 ref
    useEffect(() => {
        sessionIdRef.current = sessionId
    }, [sessionId])

    // ========== 发送消息 ==========
    const handleSend = useCallback(async ({
        message,
        uploadedFile,
        selectedMode,
        selectedSkillId,
        onAfterSend,
        docScope,
        replyModelKey,
        onSessionResolved,
    }: SendMessageOptions) => {
        const trimmedMessage = message.trim()
        if (!trimmedMessage) return
        if (sendInFlightRef.current) return
        sendInFlightRef.current = true

        setLoading(true)

        // 获取或创建会话
        let currentSessionId = sessionIdRef.current
        if (!currentSessionId) {
            currentSessionId = createSession()
        }
        onSessionResolved?.(currentSessionId)

        // 添加用户消息
        const userMessage: Message = {
            id: `user-${Date.now()}`,
            role: 'user',
            content: trimmedMessage,
            timestamp: new Date().toISOString(),
            attachments: uploadedFile ? [{
                type: uploadedFile.type || 'file',
                url: uploadedFile.sandbox_path,
                name: uploadedFile.name,
                previewUrl: uploadedFile.previewUrl
            }] : undefined
        }
        addMessage(currentSessionId, userMessage)

        // [Session Round] 新一轮开始时清除上一轮未完成的骨架屏
        useChatStore.getState().clearPendingArtifacts()

        // 发送思考中消息
        const thinkingMsgId = `thinking-${Date.now()}`
        thinkingMsgIdRef.current = thinkingMsgId
        addMessage(currentSessionId, {
            id: thinkingMsgId,
            role: 'assistant',
            content: encodeThinkingContent(DEFAULT_THINKING_STATUS),
            timestamp: new Date().toISOString(),
            isStreaming: true
        })

        try {
            // 建立 SSE 连接
            try {
                await connectSSE(currentSessionId)
            } catch {
                // SSE 连接失败不阻塞
            }

            // 构建请求体
            const requestBody: Parameters<typeof chatService.startChat>[0] = {
                session_id: currentSessionId,
                message: trimmedMessage,
                deep_search: deepSearch,
                reply_model_key: replyModelKey,
            }
            const compactScope = compactDocScope(docScope)
            if (compactScope) {
                requestBody.user_context = {
                    doc_scope: compactScope,
                }
            }

            if (uploadedFile?.type === 'image' && uploadedFile.sandbox_path) {
                requestBody.image_url = uploadedFile.sandbox_path
                requestBody.image_name = uploadedFile.name
            }
            if (uploadedFile?.type === 'file' && uploadedFile.sandbox_path) {
                requestBody.file_path = uploadedFile.sandbox_path
                requestBody.file_name = uploadedFile.name
            }

            // 直连执行模式：优先用户手动选择，其次用户配置
            const effectiveMode = selectedMode?.id || userExecutionMode
            if (effectiveMode && effectiveMode !== 'auto') {
                requestBody.execution_mode = effectiveMode
            }

            // 技能选择：携带 skill_id
            if (selectedSkillId) {
                requestBody.skill_id = selectedSkillId
            }

            const data = await chatService.startChat(requestBody)
            const planStatus = data.status || 'draft'

            // 调用发送后回调（清理状态）
            onAfterSend?.()

            // 设置任务计划（仅当 steps 非空时）
            if (data.steps && data.steps.length > 0) {
                planner.setTaskPlan({
                    session_id: currentSessionId,
                    plan_id: data.plan_id,
                    summary: data.summary,
                    steps: data.steps,
                    status: planStatus,
                    selected_skill_name: data.selected_skill_name,
                })

                // 根据状态设置不同的消息和行为
                if (planStatus === 'executing') {
                    const aiMessage: Message = {
                        id: data.plan_id,
                        role: 'assistant',
                        content: '',
                        timestamp: new Date().toISOString(),
                        isStreaming: true,
                    }
                    replaceMessage(currentSessionId, thinkingMsgId, aiMessage)
                    thinkingMsgIdRef.current = null
                    planner.setIsExecuting(true)
                } else {
                    updateMessage(currentSessionId, thinkingMsgId, data.message || "任务规划完成，请在下方确认。")
                }

                startMission()
                planner.setIsPanelExpanded(true)
            }

            // [兜底] 直连模式 completed 可能快于 SSE 建连，确保消息落地并结束流式
            if (planStatus === 'completed') {
                const responseMessageId = data.plan_id || `assistant-${Date.now()}`
                const messageContent = data.message || ''
                const currentSession = useChatStore.getState().sessions[currentSessionId]
                const existingMessage = currentSession?.messages.find(m => m.id === responseMessageId)

                if (!existingMessage) {
                    const aiMessage: Message = {
                        id: responseMessageId,
                        role: 'assistant',
                        content: messageContent,
                        timestamp: new Date().toISOString(),
                        isStreaming: false,
                    }

                    if (thinkingMsgIdRef.current) {
                        replaceMessage(currentSessionId, thinkingMsgIdRef.current, aiMessage)
                        thinkingMsgIdRef.current = null
                    } else {
                        addMessage(currentSessionId, aiMessage)
                    }
                } else {
                    if (messageContent) {
                        updateMessage(currentSessionId, responseMessageId, messageContent)
                    }
                    finishMessageStream(currentSessionId, responseMessageId)
                }
            }

            setLoading(false)
            sendInFlightRef.current = false

        } catch (error) {
            console.error('Send error:', error)
            setLoading(false)
            sendInFlightRef.current = false

            addMessage(currentSessionId, {
                id: `error-${Date.now()}`,
                role: 'assistant',
                content: `抱歉，请求失败: ${error instanceof Error ? error.message : '未知错误'}`,
                timestamp: new Date().toISOString(),
            })
        }
    }, [
        createSession, addMessage, updateMessage, replaceMessage, finishMessageStream,
        setLoading, connectSSE, startMission, planner, userExecutionMode, deepSearch
    ])

    // ========== 确认计划 ==========
    const handleConfirmPlan = useCallback(async (steps: TaskStep[]) => {
        if (!planner.taskPlan || !sessionIdRef.current) return

        try {
            const aiMessage: Message = {
                id: planner.taskPlan.plan_id,
                role: 'assistant',
                content: '',
                timestamp: new Date().toISOString(),
                isStreaming: true,
            }

            if (thinkingMsgIdRef.current) {
                replaceMessage(planner.taskPlan.session_id, thinkingMsgIdRef.current, aiMessage)
                thinkingMsgIdRef.current = null
            } else {
                addMessage(planner.taskPlan.session_id, aiMessage)
            }

            try {
                await connectSSE(sessionIdRef.current || undefined)
            } catch {
                // SSE 连接失败不阻塞
            }

            await planner.confirmPlan(steps)

        } catch (error) {
            console.error('Confirm plan error:', error)
            setError(error instanceof Error ? error.message : '执行失败')
        }
    }, [planner, replaceMessage, addMessage, connectSSE, setError])

    // ========== 取消计划 ==========
    const handleCancelPlan = useCallback(() => {
        disconnectSSE()
        planner.cancelPlan()
        setLoading(false)

        const currentSessionId = sessionIdRef.current
        if (currentSessionId && thinkingMsgIdRef.current) {
            updateMessage(currentSessionId, thinkingMsgIdRef.current, '任务已取消。')
            finishMessageStream(currentSessionId, thinkingMsgIdRef.current)
            thinkingMsgIdRef.current = null
        }
    }, [disconnectSSE, planner, updateMessage, finishMessageStream, setLoading])

    // ========== 中断提交 ==========
    const handleInterruptSubmit = useCallback(async (
        input: string,
        interruptData: InterruptData
    ) => {
        const currentSessionId = sessionIdRef.current
        if (!currentSessionId) return

        try {
            if (interruptData.signal_type === 'semantic_clarification') {
                try {
                    await connectSSE(currentSessionId)
                } catch {
                    // SSE connection failures are handled by the normal stream recovery path.
                }
                planner.handlePlanStatus('executing')
                planner.setIsExecuting(true)
                setLoading(true)
                await chatService.resumeChat({
                    session_id: currentSessionId,
                    plan_id: interruptData.target_step_id || currentSessionId,
                    input,
                    target_step_id: interruptData.target_step_id
                })
                return
            }

            const displayInput = interruptData.signal_type === 'template_preview'
                ? '已确认模板字段预览'
                : input

            addMessage(currentSessionId, {
                id: `user-${Date.now()}`,
                role: 'user',
                content: displayInput,
                timestamp: new Date().toISOString()
            })

            const thinkingMsgId = `assistant-${Date.now()}`
            thinkingMsgIdRef.current = thinkingMsgId
            addMessage(currentSessionId, {
                id: thinkingMsgId,
                role: 'assistant',
                content: encodeThinkingContent(DEFAULT_THINKING_STATUS),
                timestamp: new Date().toISOString(),
                isStreaming: true
            })

            setLoading(true)

            await chatService.resumeChat({
                session_id: currentSessionId,
                plan_id: interruptData.target_step_id || currentSessionId,
                input: input,
                target_step_id: interruptData.target_step_id
            })

        } catch (error) {
            console.error('handleInterruptSubmit error:', error)
            setError(`恢复执行失败: ${error}`)
            setLoading(false)
        }
    }, [addMessage, setLoading, setError, connectSSE, planner])

    // ========== 中断取消 ==========
    const handleInterruptCancel = useCallback(() => {
        // 仅清理状态，由调用方处理 interruptData
    }, [])

    return {
        handleSend,
        handleConfirmPlan,
        handleCancelPlan,
        handleInterruptSubmit,
        handleInterruptCancel,
    }
}
