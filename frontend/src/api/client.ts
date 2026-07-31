/**
 * DeluData API 客户端
 * 
 * 封装所有后端 API 调用
 */
import { API_BASE_URL } from '@/config'

// ========== 类型定义 ==========

export interface ChatRequest {
    message: string
    session_id?: string
}

export interface ChatResponse {
    session_id: string
    message_id: string
    status: string
    created_at: string
}

export interface HealthResponse {
    status: string
    timestamp: string
    version: string
}

// ========== 通用请求函数 ==========

async function request<T>(
    endpoint: string,
    options: RequestInit = {}
): Promise<T> {
    const url = `${API_BASE_URL}${endpoint}`

    const response = await fetch(url, {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers,
        },
        ...options,
    })

    if (!response.ok) {
        const error = await response.json().catch(() => ({}))
        throw new Error(error.detail || `Request failed: ${response.status}`)
    }

    return response.json()
}

// ========== API 方法 ==========

export const api = {
    /**
     * 健康检查
     */
    health: () => request<HealthResponse>('/health'),

    /**
     * 发送对话消息
     */
    sendMessage: (data: ChatRequest) =>
        request<ChatResponse>('/chat/send', {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    /**
     * 获取会话历史
     */
    getHistory: (sessionId: string) =>
        request<{ messages: unknown[] }>(`/chat/sessions/${sessionId}/history`),

    /**
     * 删除会话
     */
    deleteSession: (sessionId: string) =>
        request<{ success: boolean }>(`/chat/sessions/${sessionId}`, {
            method: 'DELETE',
        }),

    // ========== 知识库 API ==========

    /**
     * 上传文档
     */
    uploadDocument: async (file: File) => {
        const formData = new FormData()
        formData.append('file', file)

        const url = `${API_BASE_URL}/knowledge/upload`
        const response = await fetch(url, {
            method: 'POST',
            body: formData,
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(error.detail || `Upload failed: ${response.status}`)
        }

        return response.json()
    },

    /**
     * 获取文档列表
     */
    getDocuments: () =>
        request<{ documents: unknown[] }>('/knowledge/documents'),

    /**
     * 删除文档
     */
    deleteDocument: (documentId: string) =>
        request<{ success: boolean }>(`/knowledge/documents/${documentId}`, {
            method: 'DELETE',
        }),

    // ========== Inline HITL API ==========

    /**
     * 恢复挂起的会话 (Inline HITL)
     * 当会话因需要用户补充信息而挂起时调用
     */
    resumeSession: (data: {
        session_id: string
        plan_id: string
        input: string
        target_step_id?: string
    }) =>
        request<{
            session_id: string
            status: string
            message: string
        }>('/chat/resume', {
            method: 'POST',
            body: JSON.stringify(data),
        }),
}

export default api
