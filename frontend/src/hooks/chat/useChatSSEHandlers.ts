/**
 * SSE 回调处理器 Hook
 * 
 * 职责：封装 useChatSSE 的回调逻辑
 * - 消息流处理 (onMessageChunk, onMessageEnd)
 * - Artifact 处理 (onArtifact)
 * - 中断处理 (onInterrupt)
 * - 错误处理 (onError)
 * 
 * 设计原则：
 * - 使用 Ref 策略处理异步回调中的最新状态
 * - 保持与 useTaskPlanner 的协作
 */
import { useRef, useEffect, useCallback } from 'react'
import { useChatStore, type Message } from '@/stores/chatStore'
import { useMissionStore } from '@/stores/missionStore'
import { useArtifactBoxStore } from '@/stores/artifactBoxStore'
import type { UseTaskPlannerReturn } from '@/hooks/chat/useTaskPlanner'
import type { SSECallbacks } from '@/hooks/chat/useChatSSE'
import type { InterruptData } from '@/components/chat/InterruptInput'
import {
    matchThinkingStatus,
    encodeThinkingContent,
    isThinkingContent,
    DEFAULT_THINKING_STATUS,
} from '@/components/chat/ThinkingStatusConfig'

// ========== 接口定义 ==========

export interface UseChatSSEHandlersOptions {
    /** 当前会话 ID */
    sessionId: string | null
    /** 任务计划器 Hook */
    planner: UseTaskPlannerReturn
    /** Thinking 消息 ID Ref (来自 useChatActions) */
    thinkingMsgIdRef: React.MutableRefObject<string | null>
    /** 设置中断数据回调 */
    setInterruptData: (data: InterruptData | null) => void
    /** 设置 HTML 报告回调 */
    setHtmlReport: (report: { id: string; title: string; content: string } | null) => void
    /** 显示 HTML 报告回调 */
    setShowHtmlReport: (show: boolean) => void
}

export interface UseChatSSEHandlersReturn {
    /** SSE 回调对象 */
    sseCallbacks: SSECallbacks
    /** Pending Artifact Ref (供外部访问) */
    pendingArtifactRef: React.MutableRefObject<{ report_id?: string; title?: string; html_content?: string } | null>
}

// ========== Hook 实现 ==========

export function useChatSSEHandlers({
    sessionId,
    planner,
    thinkingMsgIdRef,
    setInterruptData,
    setHtmlReport,
    setShowHtmlReport,
}: UseChatSSEHandlersOptions): UseChatSSEHandlersReturn {
    // ========== Store Actions ==========
    const {
        addMessage,
        updateMessage,
        replaceMessage,
        appendMessageContent,
        finishMessageStream,
        appendThinkingContent,
        markThinkingDone,
        fetchSessionMessages,
        setLoading,
    } = useChatStore()

    const { setError } = useMissionStore()
    const { notifyNewArtifact } = useArtifactBoxStore()

    // ========== Refs ==========
    const sessionIdRef = useRef(sessionId)
    const pendingArtifactRef = useRef<{ report_id?: string; title?: string; html_content?: string } | null>(null)
    const lastStepLabelRef = useRef<string>('')  // 防抖：避免相同 step 重复 setState

    // [rAF 缓冲] 对高频率 reasoning 令牌分批提交到 Store
    const reasoningBufferRef = useRef<Record<string, string>>({})
    const reasoningRafRef = useRef<Record<string, number>>({})
    const answerSeenRef = useRef<Record<string, boolean>>({})
    const answerWatchdogRef = useRef<Record<string, number>>({})

    // 同步 sessionId 到 ref
    useEffect(() => {
        sessionIdRef.current = sessionId
    }, [sessionId])

    useEffect(() => {
        return () => {
            Object.values(answerWatchdogRef.current).forEach((timerId) => {
                window.clearTimeout(timerId)
            })
            answerWatchdogRef.current = {}
        }
    }, [])

    const clearAnswerWatchdog = useCallback((msgId: string) => {
        const timerId = answerWatchdogRef.current[msgId]
        if (timerId) {
            window.clearTimeout(timerId)
            delete answerWatchdogRef.current[msgId]
        }
    }, [])

    const scheduleAnswerWatchdog = useCallback((targetSessionId: string, msgId: string) => {
        clearAnswerWatchdog(msgId)
        answerWatchdogRef.current[msgId] = window.setTimeout(async () => {
            const session = useChatStore.getState().sessions[targetSessionId]
            const message = session?.messages.find((item) => item.id === msgId)
            if (message?.content?.trim() || answerSeenRef.current[msgId]) {
                return
            }
            await fetchSessionMessages(targetSessionId, true)
            setLoading(false)
        }, 2500)
    }, [clearAnswerWatchdog, fetchSessionMessages, setLoading])

    const ensureAssistantMessage = useCallback((
        currentSessionId: string,
        msgId: string,
        content = '',
        isStreaming = true
    ) => {
        if (!currentSessionId || !msgId) return

        const currentSession = useChatStore.getState().sessions[currentSessionId]
        const hasMessage = currentSession?.messages.some(message => message.id === msgId)
        if (hasMessage) return

        const aiMessage: Message = {
            id: msgId,
            role: 'assistant',
            content,
            timestamp: new Date().toISOString(),
            isStreaming,
        }

        if (thinkingMsgIdRef.current) {
            replaceMessage(currentSessionId, thinkingMsgIdRef.current, aiMessage)
            thinkingMsgIdRef.current = null
        } else {
            addMessage(currentSessionId, aiMessage)
        }
    }, [replaceMessage, addMessage, thinkingMsgIdRef])

    const resolveTargetSessionId = useCallback((streamSessionId?: string) => {
        return streamSessionId || sessionIdRef.current || null
    }, [])

    const isCurrentSessionEvent = useCallback((streamSessionId?: string) => {
        const current = sessionIdRef.current
        if (!streamSessionId) return true
        if (!current) return false
        return streamSessionId === current
    }, [])

    // ========== 消息流处理 ==========
    const onMessageChunk = useCallback((msgId: string, chunk: string, streamSessionId?: string) => {
        const targetSessionId = resolveTargetSessionId(streamSessionId)
        if (!targetSessionId || !msgId) return

        ensureAssistantMessage(targetSessionId, msgId, encodeThinkingContent(DEFAULT_THINKING_STATUS), true)
        answerSeenRef.current[msgId] = true
        clearAnswerWatchdog(msgId)
        const session = useChatStore.getState().sessions[targetSessionId]
        const message = session?.messages.find((item) => item.id === msgId)
        if (message?.content && isThinkingContent(message.content.trim())) {
            updateMessage(targetSessionId, msgId, chunk)
        } else {
            appendMessageContent(targetSessionId, msgId, chunk)
        }

        // [修复] AI 开始流式回复时立即关闭骨架屏，不等 API 返回
        if (isCurrentSessionEvent(streamSessionId)) {
            setLoading(false)
        }
    }, [ensureAssistantMessage, appendMessageContent, updateMessage, isCurrentSessionEvent, resolveTargetSessionId, setLoading, clearAnswerWatchdog])

    const onMessageEnd = useCallback((msgId: string, content?: string, roundIndex?: number, streamSessionId?: string) => {
        const targetSessionId = resolveTargetSessionId(streamSessionId)
        if (!targetSessionId || !msgId) return

        ensureAssistantMessage(targetSessionId, msgId, content || '', false)
        answerSeenRef.current[msgId] = true
        clearAnswerWatchdog(msgId)

        // 1. 写入完整内容（即使先只收到思考事件，也要让 message_end 落正文）
        if (content !== undefined) {
            updateMessage(targetSessionId, msgId, content)
        }

        // 2. 结束流式状态
        finishMessageStream(targetSessionId, msgId)

        // 3. [Session Round] 保存 roundIndex 到消息
        if (roundIndex !== undefined) {
            const session = useChatStore.getState().sessions[targetSessionId]
            if (session) {
                const msgIndex = session.messages.findIndex(m => m.id === msgId)
                if (msgIndex !== -1) {
                    const updatedMsg = {
                        ...session.messages[msgIndex],
                        roundIndex
                    }
                    replaceMessage(targetSessionId, msgId, updatedMsg)
                }
            }
        }

        // 4. 合并暂存的 Artifact 到消息
        if (pendingArtifactRef.current && isCurrentSessionEvent(streamSessionId)) {
            const session = useChatStore.getState().sessions[targetSessionId]
            if (session && session.messages.length > 0) {
                const messages = session.messages
                let lastMsgIndex = -1
                for (let i = messages.length - 1; i >= 0; i--) {
                    if (messages[i].role === 'assistant') {
                        lastMsgIndex = i
                        break
                    }
                }
                if (lastMsgIndex !== -1) {
                    const lastMsg = messages[lastMsgIndex]
                    const artifact = pendingArtifactRef.current!
                    const updatedMsg = {
                        ...lastMsg,
                        artifacts: {
                            ...lastMsg.artifacts,
                            html_report: {
                                report_id: artifact.report_id || `report-${Date.now()}`,
                                title: artifact.title || '分析报告',
                                html_content: artifact.html_content || ''
                            }
                        }
                    }
                    replaceMessage(targetSessionId, lastMsg.id, updatedMsg)
                }
            }
            pendingArtifactRef.current = null
        }

        // 5. 更新 loading 状态
        if (isCurrentSessionEvent(streamSessionId)) {
            setLoading(false)
        }
    }, [ensureAssistantMessage, replaceMessage, updateMessage, finishMessageStream, isCurrentSessionEvent, resolveTargetSessionId, setLoading, clearAnswerWatchdog])

    // ========== 计划事件处理 ==========
    const onStepUpdate = useCallback((payload: Parameters<typeof planner.handleStepUpdate>[0], streamSessionId?: string) => {
        if (!isCurrentSessionEvent(streamSessionId)) return

        // 代理给 planner
        planner.handleStepUpdate(payload)

        // 更新 thinking 消息的动态状态
        const currentSessionId = sessionIdRef.current
        const thinkingId = thinkingMsgIdRef.current
        if (!currentSessionId || !thinkingId) return

        const stepLabel = payload.label || payload.id || payload.step_id || ''
        if (!stepLabel || stepLabel === lastStepLabelRef.current) return  // 防抖
        lastStepLabelRef.current = stepLabel

        const status = matchThinkingStatus(stepLabel)
        const encodedContent = encodeThinkingContent(status)
        updateMessage(currentSessionId, thinkingId, encodedContent)
    }, [planner, updateMessage, thinkingMsgIdRef, isCurrentSessionEvent])

    const onPlanUpdate = useCallback((payload: Parameters<typeof planner.handlePlanUpdate>[0], streamSessionId?: string) => {
        if (!isCurrentSessionEvent(streamSessionId)) return
        planner.handlePlanUpdate(payload)
    }, [planner, isCurrentSessionEvent])

    const onPlanStatus = useCallback((status: string, streamSessionId?: string) => {
        if (!isCurrentSessionEvent(streamSessionId)) return
        planner.handlePlanStatus(status)
    }, [planner, isCurrentSessionEvent])

    const onAiThought = useCallback((payload: Parameters<typeof planner.handleAiThought>[0], streamSessionId?: string) => {
        if (!isCurrentSessionEvent(streamSessionId)) return
        planner.handleAiThought(payload)
    }, [planner, isCurrentSessionEvent])

    const onThinkingLog = useCallback((log: string, streamSessionId?: string) => {
        if (!isCurrentSessionEvent(streamSessionId)) return
        planner.handleThinkingLog(log)
    }, [planner, isCurrentSessionEvent])

    const onPlanComplete = useCallback((payload: Parameters<typeof planner.handlePlanComplete>[0], streamSessionId?: string) => {
        if (!isCurrentSessionEvent(streamSessionId)) return
        planner.handlePlanComplete(payload)
        setLoading(false)
    }, [planner, setLoading, isCurrentSessionEvent])

    // ========== 中断处理 ==========
    const onInterrupt = useCallback((data: InterruptData, streamSessionId?: string) => {
        if (!isCurrentSessionEvent(streamSessionId)) return

        const isSemanticClarification = data.signal_type === 'semantic_clarification'
        const targetSessionId = resolveTargetSessionId(streamSessionId)
        if (!isSemanticClarification && targetSessionId && thinkingMsgIdRef.current && data.message) {
            updateMessage(targetSessionId, thinkingMsgIdRef.current, data.message)
            finishMessageStream(targetSessionId, thinkingMsgIdRef.current)
        }
        if (isSemanticClarification) {
            planner.handlePlanStatus('suspended')
            planner.setIsPanelExpanded(true)
        }
        setInterruptData(data)
        setLoading(false)
    }, [updateMessage, finishMessageStream, setInterruptData, setLoading, thinkingMsgIdRef, isCurrentSessionEvent, resolveTargetSessionId, planner])

    // ========== Artifact 处理 ==========
    const onArtifact = useCallback((payload: { type?: string; data?: { report_id?: string; title?: string; html_content?: string } }, streamSessionId?: string) => {
        if (!isCurrentSessionEvent(streamSessionId)) return

        if (payload.type === 'html_report' && payload.data?.html_content) {
            setHtmlReport({
                id: payload.data.report_id || `report-${Date.now()}`,
                title: payload.data.title || '分析报告',
                content: payload.data.html_content
            })
            setShowHtmlReport(true)
            pendingArtifactRef.current = payload.data
            notifyNewArtifact()
        }
    }, [setHtmlReport, setShowHtmlReport, notifyNewArtifact, isCurrentSessionEvent])

    // ========== [LLM 推理令牌] 思考过程处理 ==========
    const onReasoningChunk = useCallback((msgId: string, chunk: string, streamSessionId?: string) => {
        const targetSessionId = resolveTargetSessionId(streamSessionId)
        if (!targetSessionId || !msgId || !chunk) return

        ensureAssistantMessage(targetSessionId, msgId, encodeThinkingContent(DEFAULT_THINKING_STATUS), true)

        // [修复] 思考过程开始时也关闭骨架屏
        if (isCurrentSessionEvent(streamSessionId)) {
            setLoading(false)
        }

        // rAF 缓冲：积累当前帧内的 chunk
        reasoningBufferRef.current[msgId] = (reasoningBufferRef.current[msgId] || '') + chunk

        if (!reasoningRafRef.current[msgId]) {
            reasoningRafRef.current[msgId] = requestAnimationFrame(() => {
                const buffered = reasoningBufferRef.current[msgId] || ''
                if (buffered) {
                    appendThinkingContent(targetSessionId, msgId, buffered)
                    reasoningBufferRef.current[msgId] = ''
                }
                delete reasoningRafRef.current[msgId]
            })
        }
    }, [ensureAssistantMessage, appendThinkingContent, isCurrentSessionEvent, resolveTargetSessionId, setLoading])

    const onReasoningEnd = useCallback((msgId: string, durationMs: number, streamSessionId?: string) => {
        const targetSessionId = resolveTargetSessionId(streamSessionId)
        if (!targetSessionId || !msgId) return

        ensureAssistantMessage(targetSessionId, msgId, '', true)

        // 刷入剩余缓冲
        if (reasoningBufferRef.current[msgId]) {
            appendThinkingContent(targetSessionId, msgId, reasoningBufferRef.current[msgId])
            reasoningBufferRef.current[msgId] = ''
        }
        if (reasoningRafRef.current[msgId]) {
            cancelAnimationFrame(reasoningRafRef.current[msgId])
            delete reasoningRafRef.current[msgId]
        }

        markThinkingDone(targetSessionId, msgId, durationMs)
        scheduleAnswerWatchdog(targetSessionId, msgId)
    }, [ensureAssistantMessage, appendThinkingContent, markThinkingDone, resolveTargetSessionId, scheduleAnswerWatchdog])

    // 文档类产物完成后触发暂存箱刷新（FILE_RESULT 事件链路）
    const onFileResult = useCallback((_payload?: unknown, streamSessionId?: string) => {
        if (!isCurrentSessionEvent(streamSessionId)) return
        notifyNewArtifact()
    }, [notifyNewArtifact, isCurrentSessionEvent])

    // ========== 错误处理 ==========
    const onError = useCallback((error: string, streamSessionId?: string) => {
        const targetSessionId = resolveTargetSessionId(streamSessionId)

        if (isCurrentSessionEvent(streamSessionId)) {
            setLoading(false)
            planner.setIsExecuting(false)
            setError(error)
        }

        if (targetSessionId) {
            addMessage(targetSessionId, {
                id: `error-${Date.now()}`,
                role: 'assistant',
                content: `❌ 执行出错: ${error}`,
                timestamp: new Date().toISOString()
            })
        }
    }, [setLoading, planner, setError, addMessage, isCurrentSessionEvent, resolveTargetSessionId])

    // ========== 组装 SSE Callbacks ==========
    const sseCallbacks: SSECallbacks = {
        onMessageChunk,
        onMessageEnd,
        onStepUpdate,
        onPlanComplete,
        onPlanUpdate,
        onPlanStatus,
        onAiThought,
        onThinkingLog,
        onInterrupt,
        onArtifact,
        onFileResult,
        onError,
        onReasoningChunk,
        onReasoningEnd,
    }

    return {
        sseCallbacks,
        pendingArtifactRef,
    }
}
