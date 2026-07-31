/**
 * 博物馆模块 - API 客户端
 */
import { getAuthHeader } from '@/stores/authStore'
import type {
    Product,
    PaginatedResponse,
    ProductCategory,
    GuideStartRequest,
    GuideChatRequest,
    GuideSession,
} from '../types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

// ========== 通用请求方法 ==========

async function parseErrorMessage(response: Response, fallback: string): Promise<string> {
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
        const error = await response.json().catch(() => ({}))
        return error?.detail || error?.message || fallback
    }
    return fallback
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${API_BASE_URL}${url}`, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...getAuthHeader(),
            ...options?.headers,
        },
    })

    if (!response.ok) {
        const fallback = `HTTP ${response.status}`
        throw new Error(await parseErrorMessage(response, fallback))
    }

    return response.json()
}

// ========== 导览 API ==========

export const guideApi = {
    /**
     * 开始导览会话
     */
    async start(data: GuideStartRequest & { image?: File }): Promise<{ session_id: string; visitor_uuid: string }> {
        const formData = new FormData()

        if (data.query) formData.append('query', data.query)
        if (data.enable_tts !== undefined) formData.append('enable_tts', String(data.enable_tts))
        if (data.visitor_uuid) formData.append('visitor_uuid', data.visitor_uuid)
        if (data.image) formData.append('image', data.image)

        const response = await fetch(`${API_BASE_URL}/museum/guide/start`, {
            method: 'POST',
            body: formData,
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const fallback = `HTTP ${response.status}`
            throw new Error(await parseErrorMessage(response, fallback))
        }

        return response.json()
    },

    /**
     * 导览对话
     */
    async chat(data: GuideChatRequest): Promise<void> {
        // SSE 流式响应，实际使用时通过 useGuideSSE Hook 处理
        return request('/museum/guide/chat', {
            method: 'POST',
            body: JSON.stringify(data),
        })
    },

    /**
     * 获取会话信息
     */
    async getSession(sessionId: string): Promise<GuideSession> {
        return request(`/museum/guide/session/${sessionId}`)
    },
}

// ========== 商城 API ==========

export const shopApi = {
    /**
     * 获取商品列表
     */
    async listProducts(params?: {
        category?: ProductCategory
        exhibit_id?: string
        page?: number
        page_size?: number
    }): Promise<PaginatedResponse<Product>> {
        const searchParams = new URLSearchParams()
        if (params?.category) searchParams.append('category', params.category)
        if (params?.exhibit_id) searchParams.append('exhibit_id', params.exhibit_id)
        if (params?.page) searchParams.append('page', String(params.page))
        if (params?.page_size) searchParams.append('page_size', String(params.page_size))

        const query = searchParams.toString()
        return request(`/museum/shop/products${query ? `?${query}` : ''}`)
    },

    /**
     * 获取商品详情
     */
    async getProduct(productId: string): Promise<Product> {
        return request(`/museum/shop/products/${productId}`)
    },

    /**
     * 商品咨询
     */
    async inquiry(data: {
        product_id: string
        question: string
        person_type?: string
    }): Promise<{
        product_id: string
        product_name: string
        question: string
        answer: string
    }> {
        const params = new URLSearchParams()
        params.append('product_id', data.product_id)
        params.append('question', data.question)
        if (data.person_type) params.append('person_type', data.person_type)

        return request(`/museum/shop/inquiry?${params.toString()}`, {
            method: 'POST',
        })
    },

    /**
     * 下载导入模板
     */
    async downloadTemplate(): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/museum/shop/template`, {
            headers: getAuthHeader(),
        })
        if (!response.ok) {
            throw new Error(await parseErrorMessage(response, '下载模板失败'))
        }
        const blob = await response.blob()
        const url = window.URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = 'product_import_template.xlsx'
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        window.URL.revokeObjectURL(url)
    },

    /**
     * 导入商品 Excel
     */
    async importProducts(file: File): Promise<{
        message: string
        result: { success: number; failed: number; errors: string[] }
    }> {
        const formData = new FormData()
        formData.append('file', file)

        const response = await fetch(`${API_BASE_URL}/museum/shop/import`, {
            method: 'POST',
            body: formData,
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const fallback = `HTTP ${response.status}`
            throw new Error(await parseErrorMessage(response, fallback))
        }

        return response.json()
    },
    
    /**
     * BUG4: 更新商品信息
     */
    async updateProduct(productId: string, data: {
        name?: string
        price?: number
        stock?: number
        description?: string
    }): Promise<Product> {
        return request(`/museum/shop/products/${productId}`, {
            method: 'PUT',
            body: JSON.stringify(data),
        })
    },
}
