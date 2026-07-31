/**
 * 用户智能体配置 Hook
 * 
 * 职责：获取当前用户的智能体配置（如 execution_mode）
 * 
 * 边界处理：
 * - loading 状态：阻止发送消息，避免首条消息跳过配置
 * - 错误 fallback：请求失败时默认 'auto'，不影响正常使用
 */
import { useState, useEffect, useCallback } from 'react'
import { fetchWithAuth } from '@/stores/authStore'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

export interface UserAgentConfig {
    execution_mode: string
    // 可扩展其他配置字段
}

export interface UseUserAgentConfigReturn {
    /** 用户执行模式 */
    executionMode: string
    /** 配置加载中 */
    isLoading: boolean
    /** 加载错误 */
    error: string | null
    /** 手动刷新配置 */
    refresh: () => Promise<void>
}

/**
 * 获取用户智能体配置
 * 
 * @returns {UseUserAgentConfigReturn} 配置状态和方法
 * 
 * @example
 * const { executionMode, isLoading } = useUserAgentConfig()
 * // isLoading 时禁用发送按钮
 * // executionMode 用于隐藏工具栏和强制直连
 */
export function useUserAgentConfig(): UseUserAgentConfigReturn {
    const [executionMode, setExecutionMode] = useState<string>('auto')
    const [isLoading, setIsLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    const fetchConfig = useCallback(async () => {
        setIsLoading(true)
        setError(null)

        try {
            const response = await fetchWithAuth(`${API_BASE_URL}/config/agent/me`)
            
            if (!response.ok) {
                // 401/403: 未登录或无权限，fallback to auto
                // 500: 服务器错误，fallback to auto
                console.warn(`[useUserAgentConfig] 获取配置失败: ${response.status}`)
                setExecutionMode('auto')
                setError(`获取配置失败: ${response.status}`)
                return
            }

            const data = await response.json()
            setExecutionMode(data.execution_mode || 'auto')
        } catch (err) {
            console.error('[useUserAgentConfig] 请求异常:', err)
            setExecutionMode('auto')
            setError(err instanceof Error ? err.message : '网络错误')
        } finally {
            setIsLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchConfig()
    }, [fetchConfig])

    return {
        executionMode,
        isLoading,
        error,
        refresh: fetchConfig,
    }
}
