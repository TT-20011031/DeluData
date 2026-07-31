/**
 * 导览 SSE Hook
 * 
 * 处理服务端事件流，支持：
 * - 自动重连
 * - 心跳检测
 * - 类型安全的事件处理
 * - v2.2: 状态机管理 (IDLE -> THINKING -> STREAMING -> INTERACTIVE)
 */
import { useCallback, useRef, useState } from 'react'
import { generateId } from '@/utils/id'
import { getAuthHeader } from '@/stores/authStore'
import type {
    SSEEventType,
    PersonType,
    PersonAnalysisResult,
    AdjustmentStep,
    TTSChunk,
    ProductRecommendItem,
    AnalysisStep,
    SceneAnalysisResult,
} from '../types'
import { useGuideStore } from '../stores/guideStore'
import { API_BASE_URL } from '../config'

interface UseGuideSSEOptions {
    onPersonAnalysis?: (data: PersonAnalysisResult) => void
    onAdjustmentStep?: (step: AdjustmentStep) => void
    onMessageChunk?: (content: string) => void
    onMessageEnd?: (fullText: string) => void
    onTTSChunk?: (chunk: TTSChunk) => void
    onProductRecommend?: (products: ProductRecommendItem[]) => void
    onSceneAnalysisStep?: (step: AnalysisStep) => void
    onSceneAnalysisResult?: (result: SceneAnalysisResult) => void
    onError?: (error: string) => void
    onEnd?: () => void
}

async function parseErrorMessage(response: Response): Promise<string> {
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
        const payload = await response.json().catch(() => ({}))
        return payload?.detail || payload?.message || `HTTP ${response.status}`
    }
    return `HTTP ${response.status}`
}

export function useGuideSSE(options: UseGuideSSEOptions = {}) {
    const [isConnected, setIsConnected] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const abortControllerRef = useRef<AbortController | null>(null)

    const store = useGuideStore()

    /**
     * 开始导览会话 (支持图片预上传或直接上传)
     */
    const startGuide = useCallback(async (params: {
        query?: string
        enableTts?: boolean
        visitorUuid?: string
        image?: File
        imageId?: string  // v2.1: 预上传图片ID（推荐，降低TTFT）
        deptId?: string
        skipPersonRecognition?: boolean  // v2.1: 跳过人物识别（对话区图片仅用于VL检索）
    }) => {
        // 取消之前的连接
        if (abortControllerRef.current) {
            abortControllerRef.current.abort()
        }

        const controller = new AbortController()
        abortControllerRef.current = controller

        setIsConnected(true)
        setError(null)
        store.setIsStreaming(true)
        store.setStatus('THINKING') // v2.2 状态转迁

        // 如果有文字查询，先添加用户消息
        if (params.query) {
            store.addMessage({
                id: generateId(),
                role: 'user',
                content: params.query,
                timestamp: Date.now(),
            })
        }

        if (params.image || params.imageId) {
            store.setIsAnalyzing(true)
        }

        try {
            const formData = new FormData()
            if (params.query) formData.append('query', params.query)
            formData.append('enable_tts', String(params.enableTts ?? true))
            if (params.visitorUuid) formData.append('visitor_uuid', params.visitorUuid)
            if (params.deptId) formData.append('dept_id', params.deptId)
            // v2.1: 优先使用预上传的 imageId
            if (params.imageId) formData.append('image_id', params.imageId)
            else if (params.image) formData.append('image', params.image)
            // v2.1: 跳过人物识别（对话区图片仅用于VL检索）
            if (params.skipPersonRecognition) formData.append('skip_person_recognition', 'true')

            const response = await fetch(`${API_BASE_URL}/museum/events/guide/stream`, {
                method: 'POST',
                body: formData,
                headers: getAuthHeader(),
                signal: controller.signal,
            })

            if (!response.ok) {
                throw new Error(await parseErrorMessage(response))
            }

            // 保存会话信息
            const sessionId = response.headers.get('X-Session-Id')
            const visitorUuid = response.headers.get('X-Visitor-UUID')
            console.log('[SSE] Response headers - sessionId:', sessionId, 'visitorUuid:', visitorUuid)
            if (sessionId) {
                store.setSession({
                    id: sessionId,
                    visitor_uuid: visitorUuid || '',
                    created_at: new Date().toISOString(),
                    updated_at: new Date().toISOString(),
                })
                console.log('[SSE] Session saved to store:', sessionId)
            } else {
                console.warn('[SSE] No X-Session-Id header in response!')
            }

            // 处理 SSE 流
            await processEventStream(response, controller.signal)

        } catch (err) {
            if (err instanceof Error && err.name === 'AbortError') {
                console.log('[SSE] 连接取消')
            } else {
                const message = err instanceof Error ? err.message : '连接失败'
                setError(message)
                options.onError?.(message)
                store.setStatus('IDLE')
            }
        } finally {
            setIsConnected(false)
            store.setIsAnalyzing(false)
            // 注意：正常流程下状态会在 GUIDE_END 中重置，异常时在此重置
            // 如果连接正常关闭但没有 guide_end (极少)，这里兜底
            if (abortControllerRef.current === controller) {
                store.setIsStreaming(false)
                // store.setStatus('IDLE') // 不要在这里强制 IDLE，因为 GUIDE_END 可能还没到
                options.onEnd?.()
            }
        }
    }, [options, store])

    /**
     * 继续对话
     */
    const continueChat = useCallback(async (params: {
        sessionId: string
        message: string
        enableTts?: boolean
        deptId?: string
    }) => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort()
        }

        const controller = new AbortController()
        abortControllerRef.current = controller

        setIsConnected(true)
        setError(null)
        store.setIsStreaming(true)
        store.setStatus('THINKING') // v2.2 状态转迁

        // 添加用户消息
        store.addMessage({
            id: generateId(),
            role: 'user',
            content: params.message,
            timestamp: Date.now(),
        })

        try {
            const formData = new FormData()
            formData.append('session_id', params.sessionId)
            formData.append('message', params.message)
            formData.append('enable_tts', String(params.enableTts ?? true))
            if (params.deptId) formData.append('dept_id', params.deptId)

            const response = await fetch(`${API_BASE_URL}/museum/events/guide/chat/stream`, {
                method: 'POST',
                body: formData,
                headers: getAuthHeader(),
                signal: controller.signal,
            })

            if (!response.ok) {
                throw new Error(await parseErrorMessage(response))
            }

            await processEventStream(response, controller.signal)

        } catch (err) {
            if (err instanceof Error && err.name !== 'AbortError') {
                const message = err instanceof Error ? err.message : '连接失败'
                setError(message)
                options.onError?.(message)
                store.setStatus('IDLE')
            }
        } finally {
            setIsConnected(false)
            // 兜底逻辑同上
            if (abortControllerRef.current === controller) {
                store.setIsStreaming(false)
                options.onEnd?.()
            }
        }
    }, [options, store])

    /**
     * 处理事件流
     */
    const processEventStream = async (response: Response, signal: AbortSignal) => {
        const reader = response.body?.getReader()
        if (!reader) return

        const decoder = new TextDecoder()
        let buffer = ''
        let assistantMessageStarted = false

        while (!signal.aborted) {
            const { done, value } = await reader.read()
            if (done) break

            buffer += decoder.decode(value, { stream: true })

            // 解析 SSE 事件
            const lines = buffer.split('\n')
            buffer = lines.pop() || ''

            let eventType: string | null = null
            let eventData: string | null = null

            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    eventType = line.slice(7).trim()
                } else if (line.startsWith('data: ')) {
                    eventData = line.slice(6)

                    if (eventType && eventData) {
                        try {
                            const payload = JSON.parse(eventData)
                            handleEvent(eventType as SSEEventType, payload, {
                                assistantMessageStarted,
                                onStartAssistantMessage: () => { assistantMessageStarted = true }
                            })
                        } catch (e) {
                            console.warn('[SSE] 解析失败:', eventData)
                        }
                        eventType = null
                        eventData = null
                    }
                }
            }
        }
    }

    /**
     * 处理单个事件
     */
    const handleEvent = (
        type: SSEEventType,
        payload: Record<string, unknown>,
        ctx: { assistantMessageStarted: boolean; onStartAssistantMessage: () => void }
    ) => {
        switch (type) {
            case 'HEARTBEAT':
                // 心跳，无需处理
                break

            case 'PERSON_ANALYSIS': {
                store.setIsAnalyzing(false)
                store.setPersonType(
                    payload.person_type as PersonType,
                    payload.features as string[]
                )
                options.onPersonAnalysis?.(payload as unknown as PersonAnalysisResult)

                // 兼容 v2.0 侧边栏：将 PERSON_ANALYSIS 转换为 SceneAnalysisResult 格式
                const personType = payload.person_type as string
                const features = payload.features as string[] || []
                const sceneResult: SceneAnalysisResult = {
                    total_count: 1,
                    group_dynamic: personType === '通用访客' ? '散客' : `${personType}访客`,
                    persons: [{
                        id: 'p1',
                        bbox: [0.1, 0.1, 0.9, 0.9],
                        role_label: personType,
                        visual_cues: features,
                        scores: {
                            purchasing_power: personType === '商务人士' ? 8 : 5,
                            engagement: payload.confidence ? (payload.confidence as number) * 10 : 7,
                            influence: personType === '商务人士' ? 8 : personType === '妇女' ? 7 : 5,
                        },
                        final_weight: payload.confidence ? (payload.confidence as number) * 100 : 70,
                    }],
                    target_person_id: 'p1',
                    suggested_tone: personType === '商务人士' ? '专业严谨' :
                        personType === '儿童' ? '活泼有趣' :
                            personType === '老年人' ? '耐心细致' : '亲切友好',
                    engagement_strategy: `根据${personType}特点，采用${personType === '商务人士' ? '简洁高效的介绍方式，突出展品的历史价值和文化意义' :
                        personType === '儿童' ? '趣味互动的方式，用故事和游戏引导参观' :
                            personType === '老年人' ? '缓慢清晰的讲解，提供充足的休息时间' :
                                '友好热情的服务，关注访客需求'
                        }。`,
                    analysis_steps: [],
                }
                store.setSceneAnalysis(sceneResult)
                break
            }

            case 'ADJUSTMENT_PROCESS':
                store.addAdjustmentStep(payload as unknown as AdjustmentStep)
                options.onAdjustmentStep?.(payload as unknown as AdjustmentStep)
                break

            case 'SEARCH_RESULT':
                // 可选：展示检索状态
                break

            case 'SEARCH_IMAGES':
                // v2.1: 存储检索到的图片到 store
                store.setSearchImages(payload.images as Array<{ fileId: string; imageId: string; caption?: string }>)
                break

            case 'MESSAGE_CHUNK': {
                const rawContent = payload.content as string
                // 移除情感标记 [excited], [friendly] 等，只显示纯文本
                const content = rawContent.replace(/\[[a-zA-Z_]+\]/g, '')

                // 首次收到消息块，更新状态为 STREAMING
                if (!ctx.assistantMessageStarted) {
                    store.setStatus('STREAMING') // v2.2
                    store.addMessage({
                        id: generateId(),
                        role: 'assistant',
                        content: content,
                        timestamp: Date.now(),
                        isStreaming: true,
                    })
                    ctx.onStartAssistantMessage()
                } else {
                    store.updateLastMessage(content)
                }

                options.onMessageChunk?.(content)
                break
            }

            case 'MESSAGE_END':
                // 文本生成结束 -> 进入交互态 (显示按钮，但 TTS 可能还在播)
                store.finishLastMessage()
                store.setStatus('INTERACTIVE') // v2.2: 关键修复，提前解锁 UI
                options.onMessageEnd?.(payload.full_text as string)
                break

            case 'TTS_CHUNK':
                options.onTTSChunk?.(payload as unknown as TTSChunk)
                break

            case 'PRODUCT_RECOMMEND':
                // v2.1: 存储推荐商品到 store
                store.setRecommendedProducts(payload.products as ProductRecommendItem[])
                options.onProductRecommend?.(payload.products as ProductRecommendItem[])
                break

            case 'GUIDE_END':
                // 全流程结束 (TTS 播完) -> 回到空闲态
                store.setIsStreaming(false)
                store.setStatus('IDLE') // v2.2
                store.clearAdjustmentSteps()
                break

            case 'SCENE_ANALYSIS_STEP':
                options.onSceneAnalysisStep?.(payload as unknown as AnalysisStep)
                break

            case 'SCENE_ANALYSIS_RESULT':
                store.setSceneAnalysis(payload as unknown as SceneAnalysisResult)
                options.onSceneAnalysisResult?.(payload as unknown as SceneAnalysisResult)
                break

            case 'ERROR':
                store.setStatus('IDLE')
                setError(payload.message as string)
                options.onError?.(payload.message as string)
                break
        }
    }

    /**
     * 断开连接
     */
    const disconnect = useCallback(() => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort()
            abortControllerRef.current = null
        }
        setIsConnected(false)
        store.setIsStreaming(false)
        store.setStatus('IDLE')
    }, [store])

    return {
        startGuide,
        continueChat,
        disconnect,
        isConnected,
        error,
    }
}

export default useGuideSSE
