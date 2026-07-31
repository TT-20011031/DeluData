/**
 * Session Round 架构类型定义
 *
 * 将消息升级为"会话轮次"结构，支持：
 * - 文字流（message）
 * - 思考流（thoughts）
 * - 产物流（artifacts）
 */

import type { ThoughtNode } from './chat'

/**
 * Artifact 产物类型
 */
export interface Artifact {
    stepId: string
    type: 'chart' | 'doc'
    status: 'pending' | 'generating' | 'completed' | 'error' | 'cancelled' | 'invalid_data'
    title?: string
    data?: {
        report_id?: string
        html_content?: string
        download_url?: string
        file_name?: string
        file_type?: 'word' | 'excel' | 'pdf' | 'html'
        [key: string]: unknown
    }
}

/**
 * Session Round 会话轮次
 *
 * 一个 Round 对应一次 AI 响应，包含：
 * - 文本流（流式输出的消息内容）
 * - 思考流（Reflector 的拟人化思考）
 * - 产物流（图表、文档等生成产物）
 */
export interface SessionRound {
    id: number // round_index
    messageId: string

    // 插槽 A: 文本流
    message: {
        content: string
        isStreaming: boolean
    }

    // 插槽 B: 思考流
    thoughts: ThoughtNode[]

    // 插槽 C: 产物流
    artifacts: Artifact[]
}

/**
 * 终结类 Worker 类型常量
 * 用于判断哪些 Worker 会产生 Artifact
 */
export const TERMINAL_WORKERS = ['chart_worker', 'office_worker'] as const
export type TerminalWorker = (typeof TERMINAL_WORKERS)[number]

/**
 * 工具函数：判断是否是终结类 Worker
 */
export function isTerminalWorker(worker: string): worker is TerminalWorker {
    return TERMINAL_WORKERS.includes(worker as TerminalWorker)
}
