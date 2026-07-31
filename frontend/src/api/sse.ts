/**
 * SSE 事件流管理
 * 
 * 管理与后端的 SSE 连接，解析事件并分发
 */
import { SSE_BASE_URL, SSE_EVENTS } from '@/config'

// ========== 类型定义 ==========

export interface SSEEvent {
    channel: string
    type: string
    payload: Record<string, unknown>
    timestamp: string
    event_id: string
}

export interface StepUpdate {
    id: string
    status: 'pending' | 'running' | 'completed' | 'failed'
    label: string
}

export interface MessageChunk {
    message_id: string
    content: string
}

export interface ArtifactEvent {
    type: string
    data: Record<string, unknown>
}

type EventHandler = (event: SSEEvent) => void

// ========== SSE 客户端类 ==========

export class SSEClient {
    private eventSource: EventSource | null = null
    private sessionId: string
    private handlers: Map<string, Set<EventHandler>> = new Map()
    private reconnectAttempts = 0
    private maxReconnectAttempts = 5
    private reconnectDelay = 1000

    constructor(sessionId: string) {
        this.sessionId = sessionId
    }

    /**
     * 连接到 SSE 服务
     * @returns Promise that resolves when connection is established
     */
    connect(): Promise<void> {
        return new Promise((resolve, reject) => {
            // [幂等性检查] 如果已有 OPEN 状态的连接，直接返回
            if (this.eventSource && this.eventSource.readyState === EventSource.OPEN) {
                resolve()
                return
            }

            // 如果有非 OPEN 状态的连接，先断开
            if (this.eventSource) {
                this.disconnect()
            }

            const url = `${SSE_BASE_URL}/stream/${this.sessionId}`
            this.eventSource = new EventSource(url)

            this.eventSource.onopen = () => {
                this.reconnectAttempts = 0
                resolve()
            }

            // 后端使用具名事件（event: message_chunk/step_update/...），
            // 不能只依赖 onmessage（它只接收默认 message 事件）。
            // 这里对已知事件类型逐个 addEventListener。
            Object.values(SSE_EVENTS).forEach((eventType) => {
                this.eventSource?.addEventListener(eventType, (event) => {
                    try {
                        const data: SSEEvent = JSON.parse((event as MessageEvent).data)
                        // 保险：如果后端未来改为只发 payload，这里也能兜底
                        if (data && typeof data === 'object' && 'type' in data) {
                            this.dispatch(data)
                        } else {
                            this.dispatch({
                                channel: 'telemetry',
                                type: String(eventType),
                                payload: data as any,
                                timestamp: new Date().toISOString(),
                                event_id: 'client'
                            })
                        }
                    } catch (e) {
                        console.error(`[SSE] Parse error (${eventType}):`, e)
                    }
                })
            })

            // 兼容：如果后端发送默认 message 事件，也能处理
            this.eventSource.onmessage = (event) => {
                try {
                    const data: SSEEvent = JSON.parse(event.data)
                    this.dispatch(data)
                } catch (e) {
                    console.error('[SSE] Parse error (message):', e)
                }
            }

            this.eventSource.onerror = () => {
                console.error('[SSE] Connection error')
                if (this.reconnectAttempts === 0) {
                    // First error, reject the promise
                    reject(new Error('SSE connection failed'))
                }
                this.handleReconnect()
            }

            // Timeout after 5 seconds
            setTimeout(() => {
                if (this.eventSource?.readyState !== EventSource.OPEN) {
                    reject(new Error('SSE connection timeout'))
                }
            }, 5000)
        })
    }

    /**
     * 断开连接
     */
    disconnect(): void {
        if (this.eventSource) {
            this.eventSource.close()
            this.eventSource = null
        }
    }

    /**
     * 订阅事件
     */
    on(eventType: string, handler: EventHandler): () => void {
        if (!this.handlers.has(eventType)) {
            this.handlers.set(eventType, new Set())
        }
        this.handlers.get(eventType)!.add(handler)

        // 返回取消订阅函数
        return () => {
            this.handlers.get(eventType)?.delete(handler)
        }
    }

    /**
     * 订阅特定频道的所有事件
     */
    onChannel(channel: string, handler: EventHandler): () => void {
        return this.on(`channel:${channel}`, handler)
    }

    /**
     * 分发事件
     */
    private dispatch(event: SSEEvent): void {

        // 按事件类型分发
        const typeHandlers = this.handlers.get(event.type)
        typeHandlers?.forEach((handler) => handler(event))

        // 按频道分发
        const channelHandlers = this.handlers.get(`channel:${event.channel}`)
        channelHandlers?.forEach((handler) => handler(event))

        // 通配符分发
        const allHandlers = this.handlers.get('*')
        allHandlers?.forEach((handler) => handler(event))
    }

    /**
     * 重连逻辑
     */
    private handleReconnect(): void {
        this.disconnect()

        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++
            const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1)
            setTimeout(() => this.connect(), delay)
        } else {
            console.error('[SSE] Max reconnect attempts reached')
        }
    }
}

// ========== 工具函数 ==========

/**
 * 创建 SSE 客户端的 Hook 工厂
 */
export function createSSEClient(sessionId: string): SSEClient {
    return new SSEClient(sessionId)
}
