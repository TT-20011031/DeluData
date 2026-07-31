/**
 * API 请求工具
 * 
 * 提供统一的 API 请求方法，自动处理认证和 401 跳转
 */
import { useAuthStore, getAuthHeader } from '@/stores/authStore'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

/**
 * 处理 401 响应
 * 清除认证状态并跳转到登录页
 */
function handleUnauthorized() {
    const { logout } = useAuthStore.getState()
    logout()

    // 跳转到登录页（使用 location 而非 router，因为这是在 React 组件外）
    if (window.location.pathname !== '/login') {
        window.location.href = '/login'
    }
}

/**
 * 统一的 API 请求方法
 * 自动添加认证 Header，处理 401 跳转
 */
export async function apiRequest<T = unknown>(
    endpoint: string,
    options: RequestInit = {}
): Promise<T> {
    const url = endpoint.startsWith('http') ? endpoint : `${API_BASE_URL}${endpoint}`

    const headers = {
        'Content-Type': 'application/json',
        ...getAuthHeader(),
        ...options.headers,
    }

    const response = await fetch(url, {
        ...options,
        headers,
    })

    // 处理 401 未授权
    if (response.status === 401) {
        handleUnauthorized()
        throw new Error('认证已过期，请重新登录')
    }

    // 处理其他错误
    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: '请求失败' }))
        throw new Error(error.detail || `请求失败: ${response.status}`)
    }

    return response.json()
}

/**
 * GET 请求
 */
export function apiGet<T = unknown>(endpoint: string): Promise<T> {
    return apiRequest<T>(endpoint, { method: 'GET' })
}

/**
 * POST 请求
 */
export function apiPost<T = unknown>(endpoint: string, data?: unknown): Promise<T> {
    return apiRequest<T>(endpoint, {
        method: 'POST',
        body: data ? JSON.stringify(data) : undefined,
    })
}

/**
 * PUT 请求
 */
export function apiPut<T = unknown>(endpoint: string, data?: unknown): Promise<T> {
    return apiRequest<T>(endpoint, {
        method: 'PUT',
        body: data ? JSON.stringify(data) : undefined,
    })
}

/**
 * DELETE 请求
 */
export function apiDelete<T = unknown>(endpoint: string): Promise<T> {
    return apiRequest<T>(endpoint, { method: 'DELETE' })
}

export { API_BASE_URL }
