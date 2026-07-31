import { create } from 'zustand'
import { generateId } from '@/utils/id'
import { getAuthHeader } from './authStore'
import type { Artifact } from '@/types/sessionRound'
import { isTerminalWorker } from '@/types/sessionRound'

// ...

// ========== 类型定义 ==========

// 消息附件类型（支持图片/文件）
export interface Attachment {
    type: 'image' | 'file'
    url: string           // 后端永久存储URL
    name: string
    previewUrl?: string   // 前端临时预览用
}

export interface Message {
    id: string
    role: 'user' | 'assistant' | 'system'
    content: string
    timestamp: string
    isStreaming?: boolean
    roundIndex?: number  // [Session Round] 用于关联此消息对应的 Artifacts
    attachments?: Attachment[]  // 消息附件（图片/文件）
    artifacts?: {
        html_report?: {
            report_id: string
            title: string
            html_content: string
        }
        [key: string]: any
    }
    // [LLM 推理令牌] Qwen3 thinking 模式
    thinkingContent?: string      // 累积的推理链文本
    thinkingDurationMs?: number   // 推理耗时（毫秒）
    isThinkingDone?: boolean      // 推理阶段是否结束
}


export interface Session {
    id: string
    title: string
    messages: Message[]
    createdAt: string
    updatedAt: string
    activeAssets?: Attachment[]  // 会话级多模态资产（图片/文件），用于多轮对话上下文维护
}

const THINKING_PREFIX = '__THINKING__:'

function normalizeMessageContent(content: string): string {
    return (content || '').replace(/\s+/g, ' ').trim()
}

function visibleUserMessageFingerprint(content: string): string {
    return normalizeMessageContent(content)
        .normalize('NFKC')
        .replace(/[\u200B-\u200D\uFEFF]/g, '')
        .toLowerCase()
        .replace(/[^\p{Script=Han}a-z0-9]+/gu, '')
}

function isLocalOptimisticUserMessage(message: Message): boolean {
    return message.role === 'user' && message.id.startsWith('user-')
}

function isDuplicateOptimisticUser(local: Message, remote: Message): boolean {
    if (!isLocalOptimisticUserMessage(local) || remote.role !== 'user') return false
    if (visibleUserMessageFingerprint(local.content) !== visibleUserMessageFingerprint(remote.content)) return false

    return true
}

function isOptimisticThinkingPlaceholder(message: Message): boolean {
    return message.role === 'assistant'
        && message.id.startsWith('thinking-')
        && Boolean(message.isStreaming)
        && normalizeMessageContent(message.content).startsWith(THINKING_PREFIX)
}

function hasRecentDuplicateUserMessage(messages: Message[], incoming: Message): boolean {
    if (incoming.role !== 'user') return false

    const incomingFingerprint = visibleUserMessageFingerprint(incoming.content)
    if (!incomingFingerprint) return false

    return messages.some((existing) => {
        if (existing.role !== 'user') return false
        return visibleUserMessageFingerprint(existing.content) === incomingFingerprint
    })
}

function dedupeMessages(messages: Message[]): Message[] {
    const result: Message[] = []

    for (const message of messages) {
        if (message.role === 'user' && hasRecentDuplicateUserMessage(result, message)) {
            continue
        }
        if (result.some((existing) => existing.id === message.id)) {
            continue
        }
        result.push(message)
    }

    return result
}

interface ChatState {
    // 当前会话
    currentSessionId: string | null
    sessions: Record<string, Session>

    // 输入状态
    inputMessage: string
    isLoading: boolean
    deepSearch: boolean

    // [Session Round] 产物状态：按 roundIndex 存储各轮次的 Artifacts
    roundArtifacts: Record<number, Artifact[]>

    // Actions
    setCurrentSession: (sessionId: string | null) => void
    createSession: () => string
    deleteSession: (sessionId: string) => Promise<void>
    fetchSessions: () => Promise<void>
    fetchSessionMessages: (sessionId: string, merge?: boolean) => Promise<void>

    addMessage: (sessionId: string, message: Message) => void
    mergeMessages: (sessionId: string, messages: Message[]) => void
    updateMessage: (sessionId: string, messageId: string, content: string) => void
    replaceMessage: (sessionId: string, oldId: string, newMessage: Message) => void
    appendMessageContent: (sessionId: string, messageId: string, chunk: string) => void
    finishMessageStream: (sessionId: string, messageId: string) => void
    appendThinkingContent: (sessionId: string, messageId: string, chunk: string) => void
    markThinkingDone: (sessionId: string, messageId: string, durationMs: number) => void

    setInputMessage: (message: string) => void
    setLoading: (loading: boolean) => void
    setDeepSearch: (enabled: boolean) => void

    // [Session Round] Artifact 管理方法
    initArtifactsFromPlan: (roundIndex: number, steps: Array<{ step_id: string; worker: string; description?: string }>) => void
    updateArtifactStatus: (roundIndex: number, stepId: string, status?: Artifact['status'], data?: Artifact['data']) => void
    getRoundArtifacts: (roundIndex: number) => Artifact[]
    clearRoundArtifacts: (roundIndex: number) => void
    clearPendingArtifacts: () => void  // 仅清除未完成的骨架屏
    resetAllRoundArtifacts: () => void  // [会话隔离] 清空所有 roundArtifacts


    // 工具方法
    getCurrentSession: () => Session | null
    getCurrentMessages: () => Message[]
}

// ========== Store 实现 ==========

export const useChatStore = create<ChatState>((set, get) => ({
    currentSessionId: null,
    sessions: {},
    inputMessage: '',
    isLoading: false,
    deepSearch: false,
    roundArtifacts: {},  // [Session Round] 初始化产物状态



    createSession: () => {
        const sessionId = generateId()
        const now = new Date().toISOString()

        const newSession: Session = {
            id: sessionId,
            title: '新对话',
            messages: [],
            createdAt: now,
            updatedAt: now,
        }

        set((state) => ({
            sessions: { ...state.sessions, [sessionId]: newSession },
            currentSessionId: sessionId,
        }))

        return sessionId
    },

    deleteSession: async (sessionId) => {
        try {
            // 调用后端 API 删除
            const response = await fetch(`/api/chat/sessions/${sessionId}`, {
                method: 'DELETE',
                headers: {
                    ...getAuthHeader()
                }
            })

            if (!response.ok) {
                console.error('删除会话失败:', await response.text())
            }
        } catch (error) {
            console.error('删除会话请求失败:', error)
        }

        // 无论后端是否成功，都从本地移除
        set((state) => {
            const newSessions = { ...state.sessions }
            delete newSessions[sessionId]

            const newCurrentId = state.currentSessionId === sessionId
                ? Object.keys(newSessions)[0] || null
                : state.currentSessionId

            return {
                sessions: newSessions,
                currentSessionId: newCurrentId,
            }
        })
    },

    addMessage: (sessionId, message) => {
        set((state) => {
            const session = state.sessions[sessionId]
            if (!session) {
                return state
            }

            if (session.messages.some((existing) => existing.id === message.id)) {
                return state
            }
            if (message.role === 'user' && hasRecentDuplicateUserMessage(session.messages, message)) {
                return state
            }

            // 更新会话标题（使用第一条用户消息）
            let title = session.title
            if (message.role === 'user' && session.messages.length === 0) {
                title = message.content.slice(0, 30) + (message.content.length > 30 ? '...' : '')
            }

            return {
                sessions: {
                    ...state.sessions,
                    [sessionId]: {
                        ...session,
                        title,
                        messages: dedupeMessages([...session.messages, message]),
                        updatedAt: new Date().toISOString(),
                    },
                },
            }
        })
    },

    mergeMessages: (sessionId, messages) => {
        set((state) => {
            const session = state.sessions[sessionId]
            if (!session || !Array.isArray(messages) || messages.length === 0) return state

            const localById = new Map(session.messages.map((msg) => [msg.id, msg]))
            const remoteIds = new Set(messages.map((msg) => msg.id))
            const mergedRemote = messages.map((remote) => {
                const local = localById.get(remote.id)
                if (!local) return remote
                return {
                    ...local,
                    ...remote,
                    content: remote.content || local.content,
                    thinkingContent: remote.thinkingContent || local.thinkingContent,
                    artifacts: {
                        ...local.artifacts,
                        ...remote.artifacts,
                    },
                }
            })
            const dedupedRemote = dedupeMessages(mergedRemote)
            const remoteUsers = dedupedRemote.filter((msg) => msg.role === 'user')
            const hasRemoteAssistant = dedupedRemote.some((msg) => msg.role === 'assistant')
            const localOnly = session.messages
                .filter((msg) => !remoteIds.has(msg.id))
                .filter((msg) => {
                    if (remoteUsers.some((remote) => isDuplicateOptimisticUser(msg, remote))) {
                        return false
                    }
                    if (hasRemoteAssistant && isOptimisticThinkingPlaceholder(msg)) {
                        return false
                    }
                    return true
                })

            return {
                sessions: {
                    ...state.sessions,
                    [sessionId]: {
                        ...session,
                        messages: dedupeMessages([...dedupedRemote, ...localOnly]),
                        updatedAt: new Date().toISOString(),
                    },
                },
            }
        })
    },

    updateMessage: (sessionId, messageId, content) => {
        set((state) => {
            const session = state.sessions[sessionId]
            if (!session) return state

            return {
                sessions: {
                    ...state.sessions,
                    [sessionId]: {
                        ...session,
                        messages: session.messages.map((msg) =>
                            msg.id === messageId ? { ...msg, content } : msg
                        ),
                        updatedAt: new Date().toISOString(),
                    },
                },
            }
        })
    },



    replaceMessage: (sessionId, oldId, newMessage) => {
        set((state) => {
            const session = state.sessions[sessionId]
            if (!session) return state

            return {
                sessions: {
                    ...state.sessions,
                    [sessionId]: {
                        ...session,
                        messages: session.messages.map((msg) =>
                            msg.id === oldId ? newMessage : msg
                        ),
                        updatedAt: new Date().toISOString(),
                    },
                },
            }
        })
    },

    appendMessageContent: (sessionId, messageId, chunk) => {
        set((state) => {
            const session = state.sessions[sessionId]
            if (!session) return state

            return {
                sessions: {
                    ...state.sessions,
                    [sessionId]: {
                        ...session,
                        messages: session.messages.map((msg) =>
                            msg.id === messageId
                                ? { ...msg, content: msg.content + chunk }
                                : msg
                        ),
                    },
                },
            }
        })
    },

    finishMessageStream: (sessionId, messageId) => {
        set((state) => {
            const session = state.sessions[sessionId]
            if (!session) return state

            return {
                sessions: {
                    ...state.sessions,
                    [sessionId]: {
                        ...session,
                        messages: session.messages.map((msg) =>
                            msg.id === messageId ? { ...msg, isStreaming: false } : msg
                        ),
                        updatedAt: new Date().toISOString(),
                    },
                },
            }
        })
    },

    appendThinkingContent: (sessionId, messageId, chunk) => {
        set((state) => {
            const session = state.sessions[sessionId]
            if (!session) return state

            return {
                sessions: {
                    ...state.sessions,
                    [sessionId]: {
                        ...session,
                        messages: session.messages.map((msg) =>
                            msg.id === messageId
                                ? { ...msg, thinkingContent: (msg.thinkingContent || '') + chunk }
                                : msg
                        ),
                    },
                },
            }
        })
    },

    markThinkingDone: (sessionId, messageId, durationMs) => {
        set((state) => {
            const session = state.sessions[sessionId]
            if (!session) return state

            return {
                sessions: {
                    ...state.sessions,
                    [sessionId]: {
                        ...session,
                        messages: session.messages.map((msg) =>
                            msg.id === messageId
                                ? { ...msg, isThinkingDone: true, thinkingDurationMs: durationMs }
                                : msg
                        ),
                    },
                },
            }
        })
    },

    setInputMessage: (message) => set({ inputMessage: message }),
    setLoading: (loading) => set({ isLoading: loading }),
    setDeepSearch: (enabled) => set({ deepSearch: enabled }),

    getCurrentSession: () => {
        const state = get()
        if (!state.currentSessionId) return null
        return state.sessions[state.currentSessionId] || null
    },

    getCurrentMessages: () => {
        const session = get().getCurrentSession()
        return session?.messages || []
    },

    fetchSessions: async () => {
        try {
            set({ isLoading: true })
            const response = await fetch('/api/chat/sessions', {
                headers: {
                    ...getAuthHeader()
                }
            })
            if (!response.ok) throw new Error('Failed to fetch sessions')

            const data = await response.json()
            // data is Array<{id, title, updatedAt, createdAt}>

            const sessionsMap: Record<string, Session> = {}
            data.forEach((item: any) => {
                sessionsMap[item.id] = {
                    id: item.id,
                    title: item.title,
                    messages: [], // 初始为空，点击时加载
                    createdAt: item.createdAt,
                    updatedAt: item.updatedAt
                }
            })

            set({ sessions: sessionsMap, isLoading: false })
        } catch (error) {
            console.error('Fetch sessions failed:', error)
            set({ isLoading: false })
        }
    },

    fetchSessionMessages: async (sessionId: string, merge = false) => {
        try {
            const response = await fetch(`/api/chat/sessions/${sessionId}/history`, {
                headers: {
                    ...getAuthHeader()
                }
            })

            if (!response.ok) throw new Error('Failed to fetch history')

            const data = await response.json()
            const messages = data.messages || []

            if (merge) {
                get().mergeMessages(sessionId, messages)
                return
            }

            set((state) => {
                const session = state.sessions[sessionId]
                if (!session) return state

                return {
                    sessions: {
                        ...state.sessions,
                        [sessionId]: {
                            ...session,
                            messages: messages
                        }
                    }
                }
            })

        } catch (error) {
            console.error(`Fetch history for ${sessionId} failed:`, error)
        }
    },

    setCurrentSession: (sessionId) => {
        set({ currentSessionId: sessionId })

        // 如果选中了会话且该会话没有消息，则尝试加载历史
        if (sessionId) {
            const state = get()
            const session = state.sessions[sessionId]
            if (session && session.messages.length === 0) {
                // 异步加载，不阻塞 UI
                state.fetchSessionMessages(sessionId)
            }
        }
    },

    // ========== [Session Round] Artifact 管理方法 ==========

    /**
     * 根据 PLAN_UPDATE 事件初始化骨架屏
     * 扫描 steps 中的终结类任务（chart_worker, office_worker），创建 pending 状态的 Artifact
     */
    initArtifactsFromPlan: (roundIndex, steps) => {
        const artifacts: Artifact[] = steps
            .filter(step => isTerminalWorker(step.worker))
            .map(step => ({
                stepId: step.step_id,
                type: step.worker === 'chart_worker' ? 'chart' : 'doc',
                status: 'pending' as const,
                title: step.description?.slice(0, 30) || (step.worker === 'chart_worker' ? '图表生成中...' : '文档生成中...')
            }))

        if (artifacts.length > 0) {
            set(state => ({
                roundArtifacts: {
                    ...state.roundArtifacts,
                    [roundIndex]: artifacts
                }
            }))
        }
    },

    /**
     * 更新指定 Artifact 的状态
     * 用于响应 CHART_STATUS、ARTIFACT、FILE_RESULT 等 SSE 事件
     * 
     * [Bug Fix] 使用增量合并 data，防止后续事件覆盖先前的数据（如 download_url）
     */
    updateArtifactStatus: (roundIndex, stepId, status, data) => {
        set(state => {
            const artifacts = state.roundArtifacts[roundIndex] || []
            const updatedArtifacts: Artifact[] = artifacts.map(artifact => {
                if (artifact.stepId !== stepId) return artifact
                return {
                    ...artifact,
                    // [Bug Fix] status 可选，undefined 时保留原状态
                    status: status ?? artifact.status,
                    // [Bug Fix] data 增量合并，而非全量覆盖
                    data: data ? { ...artifact.data, ...data } : artifact.data,
                    title: (data?.title as string) || artifact.title
                }
            })
            return {
                roundArtifacts: {
                    ...state.roundArtifacts,
                    [roundIndex]: updatedArtifacts
                }
            }
        })
    },


    /**
     * 获取指定轮次的 Artifacts
     */
    getRoundArtifacts: (roundIndex) => {
        return get().roundArtifacts[roundIndex] || []
    },

    /**
     * 清除指定轮次的 Artifacts（用于会话切换时清理）
     */
    clearRoundArtifacts: (roundIndex) => {
        set(state => {
            const { [roundIndex]: _, ...rest } = state.roundArtifacts
            return { roundArtifacts: rest }
        })
    },

    /**
     * 清除所有未完成（pending/generating）的骨架屏
     * 用于新一轮开始时清理上一轮遗留的僵尸骨架，但保留已完成的成果
     */
    clearPendingArtifacts: () => {
        set(state => {
            const newRoundArtifacts: Record<number, Artifact[]> = {}

            Object.keys(state.roundArtifacts).forEach(key => {
                const roundIndex = Number(key)
                const artifacts = state.roundArtifacts[roundIndex]

                // 仅清理未完成骨架：保留所有终态（含 invalid_data）
                const validArtifacts = artifacts.filter(
                    a => a.status === 'completed'
                        || a.status === 'error'
                        || a.status === 'cancelled'
                        || a.status === 'invalid_data'
                )

                if (validArtifacts.length > 0) {
                    newRoundArtifacts[roundIndex] = validArtifacts
                }
            })

            return { roundArtifacts: newRoundArtifacts }
        })
    },

    /**
     * [会话隔离] 清空所有 roundArtifacts
     * 用于会话切换时完全重置产物状态，防止上一会话的 TAB 串到新会话
     */
    resetAllRoundArtifacts: () => set({ roundArtifacts: {} }),
}))
