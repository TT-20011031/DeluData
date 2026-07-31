/**
 * DeluData 前端配置
 * 
 * 所有配置通过环境变量注入，避免硬编码
 */

function isLoopbackHost(hostname: string): boolean {
    return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'
}

function normalizeDevApiBaseUrl(rawValue: string | undefined, fallbackPath: string): string {
    const trimmed = rawValue?.trim()
    if (!trimmed) {
        return fallbackPath
    }

    if (!import.meta.env.DEV || typeof window === 'undefined') {
        return trimmed
    }

    try {
        const resolved = new URL(trimmed, window.location.origin)
        if (
            isLoopbackHost(window.location.hostname) &&
            isLoopbackHost(resolved.hostname) &&
            resolved.origin !== window.location.origin
        ) {
            return resolved.pathname || fallbackPath
        }
        return trimmed
    } catch {
        return trimmed
    }
}

// API 基础 URL
export const API_BASE_URL = normalizeDevApiBaseUrl(import.meta.env.VITE_API_BASE_URL, '/api')

// SSE 事件流 URL
export const SSE_BASE_URL = normalizeDevApiBaseUrl(import.meta.env.VITE_SSE_BASE_URL, '/api/events')

// 应用配置
export const APP_CONFIG = {
    name: 'DeluData',
    title: 'DeluData 智能问数',
    description: '基于 E-SOA 架构的企业级智能数据问答系统',
    version: import.meta.env.VITE_APP_VERSION || '1.0.0',
}

// 功能开关
export const FEATURE_FLAGS = {
    enableDebugMode: import.meta.env.DEV,
    enableTelemetry: true,
    enableCharts: true,
}

// SSE 事件类型
export const SSE_EVENTS = {
    // 会话轨事件
    MESSAGE_START: 'message_start',
    MESSAGE_CHUNK: 'message_chunk',
    MESSAGE_END: 'message_end',
    PLAN_STATUS: 'PLAN_STATUS',
    PLAN_COMPLETE: 'plan_complete',
    // 迭代式规划事件 (动态追加步骤)
    PLAN_UPDATE: 'plan_update',
    // 遥测轨事件
    STEP_UPDATE: 'step_update',
    THINKING_LOG: 'thinking_log',
    ARTIFACT: 'artifact',
    ERROR: 'error',
    // 中断事件（需要用户补充信息）
    INTERRUPT: 'interrupt',
    // [双通道反馈] Reflector 拟人化思考事件
    AI_THOUGHT: 'ai_thought',
    // [异步流式响应] 终结类任务事件
    CHART_STATUS: 'chart_status',    // 图表生成状态
    FILE_RESULT: 'file_result',      // 文件生成完成
    // [LLM 推理令牌] Qwen3 thinking 模式
    REASONING_CHUNK: 'reasoning_chunk',  // 推理内容片段（流式）
    REASONING_END: 'reasoning_end',      // 推理阶段结束（附带 duration_ms）
} as const

// SSE 频道
export const SSE_CHANNELS = {
    CONVERSATION: 'conversation',
    TELEMETRY: 'telemetry',
} as const
