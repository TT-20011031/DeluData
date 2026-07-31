/**
 * Skills 操作手册 API 服务层
 * 
 * 封装所有 Skill 相关的 HTTP 请求
 */
import { getAuthHeader } from '@/stores/authStore'
import { API_BASE_URL } from '@/config'
import type { DocScope } from '@/types/docScope'

// =============================================================================
// 辅助函数
// =============================================================================

/**
 * 格式化错误消息
 * 处理FastAPI的422验证错误（detail可能是数组或对象）
 */
function formatErrorMessage(error: any, fallback: string): string {
    if (!error || !error.detail) return fallback

    // 如果detail是字符串，直接返回
    if (typeof error.detail === 'string') {
        return error.detail
    }

    // 如果detail是数组（FastAPI 422验证错误）
    if (Array.isArray(error.detail)) {
        // 提取所有错误消息
        const messages = error.detail.map((err: any) => {
            if (typeof err === 'string') return err
            if (err.msg) return `${err.loc ? err.loc.join('.') + ': ' : ''}${err.msg}`
            return JSON.stringify(err)
        })
        return messages.join('; ')
    }

    // 如果detail是对象，尝试转换为可读字符串
    if (typeof error.detail === 'object') {
        return JSON.stringify(error.detail)
    }

    return fallback
}

// =============================================================================
// 类型定义
// =============================================================================

/** Skill 步骤 */
export interface SkillStep {
    step: number
    action: string
    tool?: 'sql_worker' | 'doc_worker' | 'chart_worker' | 'office_worker' | 'synthesizer' | null
    template?: string
    template_id?: number
    template_version?: string
    template_mode?: string
    output_filename?: string
    doc_scope?: DocScope
    keywords?: string[]
}

/** Skill 摘要（列表用） */
export interface SkillSummary {
    id: string
    title: string
    description: string
    tags: string[]
    usage_count: number
    visibility: 'global' | 'workspace'
}

/** Skill 完整信息 */
export interface Skill extends SkillSummary {
    steps: SkillStep[]
    example_queries: string[]
    created_by: string
    created_at: string
    updated_at: string
}

/** 创建 Skill 请求 */
export interface SkillCreateRequest {
    title: string
    description: string
    steps: SkillStep[]
    tags?: string[]
    example_queries?: string[]
    visibility?: 'global' | 'workspace'
}

/** 更新 Skill 请求 */
export interface SkillUpdateRequest {
    title?: string
    description?: string
    steps?: SkillStep[]
    tags?: string[]
    example_queries?: string[]
    visibility?: 'global' | 'workspace'
}

/** LLM 生成请求 */
export interface SkillGenerateRequest {
    user_input: string
}

/** LLM 生成响应 */
export interface SkillGenerateResponse {
    title: string
    description: string
    steps: SkillStep[]
    tags: string[]
    example_queries: string[]
}

/** Dry Run 请求 */
export interface DryRunRequest {
    skill_id: string
    test_query: string
}

/** Dry Run 步骤预览 */
export interface DryRunStepPreview {
    step_id: string
    instruction: string
    worker: string
    params: Record<string, unknown>
}

/** Dry Run 响应 */
export interface DryRunResponse {
    skill_used: string
    test_query: string
    predicted_steps: DryRunStepPreview[]
    thinking: string
}

/** 检索结果 */
export interface SkillSearchResult {
    skill: Skill
    score: number
}

// =============================================================================
// API 服务
// =============================================================================

export const skillService = {
    // =========================================================================
    // CRUD
    // =========================================================================

    /**
     * 获取 Skills 列表
     */
    async getSkills(): Promise<SkillSummary[]> {
        const response = await fetch(`${API_BASE_URL}/config/skills`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取 Skills 失败: ${response.status}`)
        }

        return response.json()
    },

    /**
     * 获取 Skill 详情
     */
    async getSkill(id: string): Promise<Skill> {
        const response = await fetch(`${API_BASE_URL}/config/skills/${id}`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`获取 Skill 详情失败: ${response.status}`)
        }

        return response.json()
    },

    /**
     * 创建 Skill
     */
    async createSkill(data: SkillCreateRequest): Promise<Skill> {
        const response = await fetch(`${API_BASE_URL}/config/skills`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(formatErrorMessage(error, '创建失败'))
        }

        return response.json()
    },

    /**
     * 更新 Skill
     */
    async updateSkill(id: string, data: SkillUpdateRequest): Promise<Skill> {
        const response = await fetch(`${API_BASE_URL}/config/skills/${id}`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(formatErrorMessage(error, '更新失败'))
        }

        return response.json()
    },

    /**
     * 删除 Skill
     */
    async deleteSkill(id: string): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/config/skills/${id}`, {
            method: 'DELETE',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(formatErrorMessage(error, '删除失败'))
        }
    },

    // =========================================================================
    // 检索
    // =========================================================================

    /**
     * 语义检索 Skills
     */
    async searchSkills(query: string, topK: number = 3): Promise<SkillSearchResult[]> {
        const response = await fetch(`${API_BASE_URL}/config/skills/search?query=${encodeURIComponent(query)}&top_k=${topK}`, {
            method: 'POST',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`检索失败: ${response.status}`)
        }

        return response.json()
    },

    // =========================================================================
    // LLM 生成
    // =========================================================================

    /**
     * LLM 生成 Skill 内容
     */
    async generateSkill(request: SkillGenerateRequest): Promise<SkillGenerateResponse> {
        const response = await fetch(`${API_BASE_URL}/config/skills/generate`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(request),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(formatErrorMessage(error, '生成失败'))
        }

        return response.json()
    },

    // =========================================================================
    // Dry Run 测试
    // =========================================================================

    /**
     * Dry Run 测试 Skill
     */
    async dryRun(request: DryRunRequest): Promise<DryRunResponse> {
        const response = await fetch(`${API_BASE_URL}/config/skills/dry-run`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(request),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(formatErrorMessage(error, '测试失败'))
        }

        return response.json()
    },

    /**
     * 重新索引所有 Skills 到向量库
     */
    async reindexSkills(): Promise<{ total: number; success: number; failed: number }> {
        const response = await fetch(`${API_BASE_URL}/config/skills/reindex`, {
            method: 'POST',
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({}))
            throw new Error(formatErrorMessage(error, '重新索引失败'))
        }

        return response.json()
    },
}

