/**
 * 聊天 API 服务层
 * 
 * 封装所有聊天相关的 HTTP 请求
 */
import { getAuthHeader } from '@/stores/authStore'
import type { TaskStep } from '@/types/chat'
import type { DocScope } from '@/types/docScope'
import type { ReplyModelKey } from '@/types/replyModel'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

// 请求/响应类型
export interface StartChatPayload {
    session_id: string
    message: string
    reply_model_key?: ReplyModelKey
    user_context?: {
        doc_scope?: DocScope
    }
    image_url?: string
    image_name?: string
    file_path?: string
    file_name?: string
    execution_mode?: string  // [NEW] 直连执行模式
    deep_search?: boolean    // [NEW] 深度检索模式
    skill_id?: string        // [NEW] 技能选择（操作手册）
}

export interface StartChatResponse {
    plan_id: string
    summary: string
    steps: TaskStep[]
    message?: string
    status: 'draft' | 'executing' | 'completed'  // [Auto-Confirm] draft=需确认, executing=自动执行
    need_confirm: boolean          // [Auto-Confirm] Planner 判定是否需要用户确认
    selected_skill_name?: string
}

export interface ConfirmPlanPayload {
    session_id: string
    plan_id: string
    modified_steps: TaskStep[]
}

export interface ResumeChatPayload {
    session_id: string
    plan_id: string
    input: string
    target_step_id?: string
}

export interface SessionPlanResponse {
    plan_id: string
    summary: string
    steps: TaskStep[]
    status: string
    thought_nodes?: Array<{
        id: string
        thought: string
        verdict: string
        round_index: number
        target_step_id?: string  // [修复] 关联的任务步骤 ID
    }>
    // [FIX] 新增字段：用于刷新后恢复 artifact TAB
    execution_results?: Array<{
        step_id: string
        worker: string
        result: any
        error?: string
    }>
    round_index?: number
    selected_skill_name?: string
}

/**
 * 聊天服务
 */
export const chatService = {
    /**
     * 开始聊天 - 生成任务计划
     */
    async startChat(payload: StartChatPayload): Promise<StartChatResponse> {
        const response = await fetch(`${API_BASE_URL}/chat/start`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader(),
            },
            body: JSON.stringify(payload),
        })

        if (!response.ok) {
            const error = await response.json()
            throw new Error(error.detail || '请求失败')
        }

        return response.json()
    },

    /**
     * 确认执行计划
     */
    async confirmPlan(payload: ConfirmPlanPayload): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/chat/confirm`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader(),
            },
            body: JSON.stringify(payload),
        })

        if (!response.ok) {
            const error = await response.json()
            throw new Error(error.detail || '执行失败')
        }
    },

    /**
     * 恢复聊天 - 处理中断输入
     */
    async resumeChat(payload: ResumeChatPayload): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/chat/resume`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getAuthHeader(),
            },
            body: JSON.stringify(payload),
        })

        if (!response.ok) {
            throw new Error(`Resume failed: ${response.status}`)
        }
    },

    /**
     * 获取会话的任务计划（用于恢复状态）
     */
    async getSessionPlan(sessionId: string): Promise<SessionPlanResponse | null> {
        try {
            const response = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}/plan`, {
                headers: getAuthHeader(),
            })

            if (!response.ok) return null

            return response.json()
        } catch {
            console.warn('获取任务计划失败')
            return null
        }
    },
}
