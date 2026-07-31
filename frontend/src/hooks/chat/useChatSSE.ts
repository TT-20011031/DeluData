/**
 * SSE 连接管理 Hook
 * 
 * 负责建立 SSE 连接，监听事件并分发给回调函数
 * 此 Hook 不持有 plan state，只负责事件分发
 */
import { useRef, useCallback, useEffect } from 'react'
import { createSSEClient, type SSEEvent } from '@/api/sse'
import { SSE_EVENTS } from '@/config'
import type { InterruptData } from '@/components/chat/InterruptInput'
import type { TaskStep } from '@/types/chat'
import { useChatStore } from '@/stores/chatStore'

// SSE 回调接口
export interface SSECallbacks {
    // 消息相关
    onMessageChunk?: (messageId: string, chunk: string, streamSessionId?: string) => void
    onMessageEnd?: (messageId: string, content?: string, roundIndex?: number, streamSessionId?: string) => void  // [Session Round] 增加 roundIndex

    // 任务计划相关
    onStepUpdate?: (payload: StepUpdatePayload, streamSessionId?: string) => void
    onPlanComplete?: (payload: PlanCompletePayload, streamSessionId?: string) => void
    onPlanUpdate?: (payload: PlanUpdatePayload, streamSessionId?: string) => void
    onPlanStatus?: (status: string, streamSessionId?: string) => void

    // 中断相关
    onInterrupt?: (data: InterruptData, streamSessionId?: string) => void

    // AI 思考相关
    onAiThought?: (payload: AiThoughtPayload, streamSessionId?: string) => void
    onThinkingLog?: (log: string, streamSessionId?: string) => void

    // [LLM 推理令牌] Qwen3 thinking 模式
    onReasoningChunk?: (messageId: string, chunk: string, streamSessionId?: string) => void
    onReasoningEnd?: (messageId: string, durationMs: number, streamSessionId?: string) => void

    // Artifact 相关
    onArtifact?: (payload: ArtifactPayload, streamSessionId?: string) => void

    // [异步流式响应] 终结类任务事件
    onChartStatus?: (payload: ChartStatusPayload, streamSessionId?: string) => void
    onFileResult?: (payload: FileResultPayload, streamSessionId?: string) => void

    // 错误处理
    onError?: (error: string, streamSessionId?: string) => void
}

// Payload 类型
export interface StepUpdatePayload {
    id?: string
    step_id?: string
    status?: string
    label?: string
    result?: string
    parent_step_id?: string
    round_index?: number  // [Session Round] 用于更新 Artifact 状态
}

export interface PlanCompletePayload {
    session_id?: string
    plan_id?: string
    summary?: string
    steps?: TaskStep[]
    status?: string
}

export interface PlanUpdatePayload {
    action?: 'append' | 'replace' | 'nest'
    steps?: TaskStep[]
    status?: 'draft' | 'confirmed' | 'executing'
    round_index?: number  // [Session Round] 关联轮次
}

export interface AiThoughtPayload {
    id?: string
    thought?: string
    verdict?: string
    round_index?: number
}

export interface ArtifactPayload {
    type?: string
    data?: {
        report_id?: string
        step_id?: string
        title?: string
        html_content?: string
    }
    round_index?: number  // [Session Round] 关联轮次
}

// [异步流式响应] 图表状态 Payload
export interface ChartStatusPayload {
    step_id?: string
    status?: 'generating' | 'completed' | 'error' | 'invalid_data'
    title?: string
    round_index?: number  // [Session Round] 关联轮次
}

// [异步流式响应] 文件结果 Payload
export interface FileResultPayload {
    step_id?: string
    file_type?: 'word' | 'excel' | 'pdf' | 'html'
    file_name?: string
    download_url?: string
}

interface UseChatSSEReturn {
    connect: (sessionIdOverride?: string) => Promise<void>
    disconnect: () => void
    isConnected: boolean
}

function extractArtifactStepId(data?: { report_id?: string; step_id?: string }): string {
    const explicitStepId = String(data?.step_id || '').trim()
    if (explicitStepId) return explicitStepId

    const reportId = String(data?.report_id || '').trim()
    if (!reportId) return ''

    const chartSuffixMatch = reportId.match(/^(.*)_chart$/)
    if (chartSuffixMatch && chartSuffixMatch[1]) {
        return chartSuffixMatch[1]
    }

    return ''
}

/**
 * SSE 连接管理 Hook
 * 
 * @param sessionId - 会话 ID
 * @param callbacks - 事件回调对象
 */
export function useChatSSE(
    sessionId: string | null,
    callbacks: SSECallbacks
): UseChatSSEReturn {
    const sseClientRef = useRef<ReturnType<typeof createSSEClient> | null>(null)
    const sessionIdRef = useRef<string | null>(sessionId)
    const connectedSessionIdRef = useRef<string | null>(null)
    const isConnectedRef = useRef(false)

    useEffect(() => {
        sessionIdRef.current = sessionId
    }, [sessionId])

    // 事件处理函数
    const handleSSEEvent = useCallback((event: SSEEvent) => {
        const streamSessionId = connectedSessionIdRef.current
        if (!streamSessionId) return
        const isActiveSession = streamSessionId === sessionIdRef.current

        switch (event.type) {
            case SSE_EVENTS.MESSAGE_START:
                break

            case SSE_EVENTS.MESSAGE_CHUNK: {
                const payload = event.payload as { content?: string; chunk?: string; message_id?: string }
                const content = payload?.content ?? payload?.chunk
                if (content && callbacks.onMessageChunk) {
                    callbacks.onMessageChunk(payload.message_id || '', content, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.MESSAGE_END: {
                const payload = event.payload as { message_id?: string; content?: string; round_index?: number }
                if (callbacks.onMessageEnd) {
                    callbacks.onMessageEnd(payload?.message_id || '', payload?.content, payload?.round_index, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.STEP_UPDATE: {
                const payload = event.payload as StepUpdatePayload

                // [Session Round] 根据 STEP_UPDATE 中的 error/completed 状态更新 Artifact
                if (isActiveSession && payload.round_index !== undefined && (payload.id || payload.step_id)) {
                    const stepId = payload.step_id || payload.id || ''
                    if (payload.status === 'error') {
                        useChatStore.getState().updateArtifactStatus(
                            payload.round_index,
                            stepId,
                            'error',
                            { title: payload.label || '任务失败' }
                        )
                    } else if (payload.status === 'invalid_data') {
                        // [修复] 数据无效时使用 invalid_data 状态，不再降级为 cancelled
                        useChatStore.getState().updateArtifactStatus(
                            payload.round_index,
                            stepId,
                            'invalid_data',
                            { title: payload.label || '数据无效' }
                        )
                    } else if (payload.status === 'completed') {
                        useChatStore.getState().updateArtifactStatus(
                            payload.round_index,
                            stepId,
                            'completed',
                            { title: payload.label }
                        )
                    } else if (payload.status === 'cancelled') {
                        // [骨架屏清理] 熔断时任务被取消
                        useChatStore.getState().updateArtifactStatus(
                            payload.round_index,
                            stepId,
                            'cancelled',
                            { title: payload.label || '任务已取消' }
                        )
                    }
                }

                if (callbacks.onStepUpdate) {
                    callbacks.onStepUpdate(payload, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.PLAN_STATUS: {
                const payload = event.payload as { status?: string }
                if (payload.status && callbacks.onPlanStatus) {
                    callbacks.onPlanStatus(payload.status, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.PLAN_COMPLETE: {
                const payload = event.payload as PlanCompletePayload
                if (callbacks.onPlanComplete) {
                    callbacks.onPlanComplete(payload, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.PLAN_UPDATE: {
                const payload = event.payload as PlanUpdatePayload

                // [Session Round] 从 PLAN_UPDATE 初始化骨架屏
                if (isActiveSession && payload.steps && payload.round_index !== undefined) {
                    useChatStore.getState().initArtifactsFromPlan(
                        payload.round_index,
                        payload.steps.map(s => ({
                            step_id: s.step_id,
                            worker: s.worker,
                            description: s.description
                        }))
                    )
                }

                if (callbacks.onPlanUpdate) {
                    callbacks.onPlanUpdate(payload, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.THINKING_LOG: {
                const payload = event.payload as { log?: string }
                if (payload.log && callbacks.onThinkingLog) {
                    callbacks.onThinkingLog(payload.log, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.ERROR: {
                const payload = event.payload as { error?: string }
                if (callbacks.onError) {
                    callbacks.onError(payload?.error || '发生错误', streamSessionId)
                }
                break
            }

            case SSE_EVENTS.INTERRUPT: {
                const payload = event.payload as unknown as InterruptData
                if (callbacks.onInterrupt) {
                    callbacks.onInterrupt(payload, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.AI_THOUGHT: {
                const payload = event.payload as AiThoughtPayload
                if (callbacks.onAiThought) {
                    callbacks.onAiThought(payload, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.REASONING_CHUNK: {
                const payload = event.payload as { message_id?: string; chunk?: string }
                if (payload.chunk && callbacks.onReasoningChunk) {
                    callbacks.onReasoningChunk(payload.message_id || '', payload.chunk, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.REASONING_END: {
                const payload = event.payload as { message_id?: string; duration_ms?: number }
                if (callbacks.onReasoningEnd) {
                    callbacks.onReasoningEnd(payload.message_id || '', payload.duration_ms ?? 0, streamSessionId)
                }
                break
            }

            case SSE_EVENTS.ARTIFACT: {
                const payload = event.payload as ArtifactPayload
                const artifactData = payload.data || {}

                // [Session Round] 根据 ARTIFACT 事件更新状态为 completed
                const stepId = extractArtifactStepId(artifactData)
                if (isActiveSession && payload.round_index !== undefined && stepId) {
                    useChatStore.getState().updateArtifactStatus(
                        payload.round_index,
                        stepId,
                        'completed',
                        {
                            report_id: artifactData.report_id,
                            title: artifactData.title,
                            html_content: artifactData.html_content
                        }
                    )
                }

                if (callbacks.onArtifact) {
                    callbacks.onArtifact(payload, streamSessionId)
                }
                break
            }

            // [异步流式响应] 图表生成状态
            case SSE_EVENTS.CHART_STATUS: {
                const payload = event.payload as ChartStatusPayload

                // [Session Round] 更新 Artifact 状态
                if (isActiveSession && payload.step_id && payload.round_index !== undefined) {
                    const status = payload.status === 'generating' ? 'generating'
                        : payload.status === 'completed' ? 'completed'
                            : payload.status === 'error' ? 'error'
                                : payload.status === 'invalid_data' ? 'invalid_data' : 'pending'
                    useChatStore.getState().updateArtifactStatus(
                        payload.round_index,
                        payload.step_id,
                        status,
                        payload.title ? { title: payload.title } : undefined
                    )
                }

                if (callbacks.onChartStatus) {
                    callbacks.onChartStatus(payload, streamSessionId)
                }
                break
            }

            // [异步流式响应] 文件生成完成
            case SSE_EVENTS.FILE_RESULT: {
                const payload = event.payload as FileResultPayload & { round_index?: number }

                // [Bug Fix] 关键修复：更新 Artifact 的 data.download_url
                // 使用 undefined 作为 status 参数，仅更新 data 不改变当前状态
                if (isActiveSession && payload.step_id && payload.round_index !== undefined && payload.download_url) {
                    useChatStore.getState().updateArtifactStatus(
                        payload.round_index,
                        payload.step_id,
                        undefined,  // 不改变当前状态（可能是 running 或 completed）
                        {
                            download_url: payload.download_url,
                            file_name: payload.file_name,
                            file_type: payload.file_type
                        }
                    )
                }

                if (callbacks.onFileResult) {
                    callbacks.onFileResult(payload, streamSessionId)
                }
                break
            }
        }
    }, [callbacks])

    // 连接 SSE
    const connect = useCallback(async (sessionIdOverride?: string) => {
        const targetSessionId = sessionIdOverride ?? sessionIdRef.current ?? sessionId
        if (!targetSessionId) return

        sessionIdRef.current = targetSessionId
        connectedSessionIdRef.current = targetSessionId

        // 断开旧连接
        if (sseClientRef.current) {
            sseClientRef.current.disconnect()
        }

        // 创建新连接
        sseClientRef.current = createSSEClient(targetSessionId)
        sseClientRef.current.on('*', handleSSEEvent)

        try {
            await sseClientRef.current.connect()
            isConnectedRef.current = true
        } catch (error) {
            console.warn('[useChatSSE] Connection failed:', error)
            isConnectedRef.current = false
            throw error
        }
    }, [sessionId, handleSSEEvent])

    // 断开 SSE
    const disconnect = useCallback(() => {
        if (sseClientRef.current) {
            sseClientRef.current.disconnect()
            sseClientRef.current = null
        }
        connectedSessionIdRef.current = null
        isConnectedRef.current = false
    }, [])

    // 组件卸载时清理
    useEffect(() => {
        return () => {
            disconnect()
        }
    }, [disconnect])

    return {
        connect,
        disconnect,
        isConnected: isConnectedRef.current,
    }
}
