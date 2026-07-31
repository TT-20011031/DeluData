/**
 * 知识库 API 服务层
 * 
 * 封装所有知识库相关的 HTTP 请求
 */
import { getAuthHeader } from '@/stores/authStore'
import { API_BASE_URL } from '@/config'
import type {
    StatsData,
    FolderNode,
    OriginInfo,
    DepartmentOption,
    KnowledgeNode,
    KnowledgeEdge,
} from '@/types/knowledge'

// =============================================================================
// 请求/响应类型（服务层专用）
// =============================================================================

export interface GraphData {
    nodes: KnowledgeNode[]
    edges: KnowledgeEdge[]
}

export interface CreateFolderPayload {
    name: string
    parent_id?: string
    visibility: string
    dept_id?: number | null
}

export interface CreateRelationshipPayload {
    source_id: string
    target_id: string
    relation_type: string
}

export interface NodePosition {
    x: number
    y: number
}

export interface UploadResponse {
    document_id: string
    name: string
    status: string
    message: string
    task_id?: string
}

export interface IngestionTaskStatus {
    task_id: string
    file_id: string
    status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancel_requested' | 'cancelled'
    stage: 'queued' | 'preclean' | 'parsing' | 'ocr' | 'chunking' | 'embedding' | 'storing' | 'completed' | 'failed'
    progress: number
    detail: Record<string, any>
    error_message?: string | null
    created_at?: string
    started_at?: string | null
    updated_at?: string | null
    finished_at?: string | null
}

async function parseErrorMessage(response: Response, fallback: string): Promise<string> {
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
        const payload = await response.json().catch(() => ({}))
        const detail = payload?.detail
        if (typeof detail === 'string' && detail.trim()) {
            return detail
        }
        if (payload?.message && typeof payload.message === 'string') {
            return payload.message
        }
    }

    const text = await response.text().catch(() => '')
    return text.trim() || fallback
}

async function ensureMutationSuccess(response: Response, fallback: string): Promise<void> {
    if (response.ok) {
        return
    }
    throw new Error(await parseErrorMessage(response, fallback))
}

// =============================================================================
// 知识库服务
// =============================================================================

export const knowledgeService = {
    // =========================================================================
    // 数据获取
    // =========================================================================

    /**
     * 获取概览统计数据
     */
    async fetchStats(): Promise<StatsData> {
        const response = await fetch(`${API_BASE_URL}/knowledge/stats`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            console.error('[Stats API] 请求失败:', response.status)
            // 返回默认数据作为兜底
            return {
                global: { count: 0 },
                private: { count: 0 },
                departments: [],
            }
        }

        return response.json()
    },

    /**
     * 获取文件结构
     */
    async fetchStructure(scope: string): Promise<FolderNode[]> {
        const response = await fetch(
            `${API_BASE_URL}/knowledge/structure?scope=${scope}`,
            { headers: getAuthHeader() }
        )

        if (!response.ok) {
            throw new Error(`Failed to fetch structure: ${response.status}`)
        }

        return response.json()
    },

    /**
     * 获取指定文件夹的子节点 (用于懒加载)
     */
    async fetchFolderChildren(folderId: string, scope: string, limit = 200, cursor?: string): Promise<FolderNode[]> {
        const q = new URLSearchParams({
            scope,
            parent_id: folderId,
            limit: String(limit),
        })
        if (cursor) q.set('cursor', cursor)

        const response = await fetch(
            `${API_BASE_URL}/knowledge/structure/nodes?${q}`,
            { headers: getAuthHeader() }
        )

        if (!response.ok) {
            throw new Error(`Failed to fetch folder children: ${response.status}`)
        }

        const data: unknown = await response.json()
        if (Array.isArray(data)) {
            return data as FolderNode[]
        }
        if (data && typeof data === 'object' && Array.isArray((data as { items?: unknown }).items)) {
            return (data as { items: FolderNode[] }).items
        }
        return []
    },

    async fetchVisibleStructure(payload: { visibilities?: string[]; dept_ids?: number[] }): Promise<FolderNode[]> {
        const response = await fetch(`${API_BASE_URL}/knowledge/structure/visible`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })

        if (!response.ok) {
            throw new Error(`Failed to fetch visible structure: ${response.status}`)
        }

        return response.json()
    },

    /**
     * 获取图谱数据
     */
    async fetchGraph(): Promise<GraphData> {
        const response = await fetch(`${API_BASE_URL}/knowledge/graph`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`Failed to fetch graph: ${response.status}`)
        }

        return response.json()
    },

    /**
     * 获取部门列表（树状结构扁平化）
     */
    async fetchDepartments(): Promise<DepartmentOption[]> {
        const response = await fetch(`${API_BASE_URL}/authorization/org-units`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            return []
        }

        const treeData = await response.json()

        if (Array.isArray(treeData)) {
            return treeData.map((item) => ({ id: item.id, name: item.name }))
        }

        // 扁平化处理
        const flatten = (list: any[]): DepartmentOption[] =>
            list.flatMap((d) => [
                { id: d.id, name: d.name },
                ...flatten(d.children || []),
            ])

        return flatten(treeData)
    },

    /**
     * 获取文档溯源信息
     */
    async fetchOriginInfo(fileId: string): Promise<OriginInfo | null> {
        const response = await fetch(
            `${API_BASE_URL}/knowledge/documents/${fileId}/origin`,
            { headers: getAuthHeader() }
        )

        if (!response.ok) {
            console.error('获取溯源信息失败')
            return null
        }

        return response.json()
    },

    // =========================================================================
    // 文件夹操作
    // =========================================================================

    /**
     * 创建文件夹
     */
    async createFolder(payload: CreateFolderPayload): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/knowledge/folders`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        await ensureMutationSuccess(response, '创建文件夹失败')
    },

    /**
     * 删除文件夹
     */
    async deleteFolder(id: string): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/knowledge/folders/${id}`, {
            method: 'DELETE',
            headers: getAuthHeader(),
        })
        await ensureMutationSuccess(response, '删除文件夹失败')
    },

    /**
     * 批量删除文件夹
     */
    async batchDeleteFolders(ids: string[]): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/knowledge/folders/batch-delete`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        })
        await ensureMutationSuccess(response, '批量删除文件夹失败')
    },

    /**
     * 重命名文件夹
     */
    async renameFolder(id: string, name: string): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/knowledge/folders/${id}/name`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        })
        await ensureMutationSuccess(response, '重命名文件夹失败')
    },

    /**
     * 移动文件夹
     */
    async moveFolder(id: string, targetFolderId: string | null): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/knowledge/folders/${id}/move`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ parent_id: targetFolderId }),
        })
        await ensureMutationSuccess(response, '移动文件夹失败')
    },

    // =========================================================================
    // 文件操作
    // =========================================================================

    /**
     * 上传文件
     */
    async uploadFile(formData: FormData): Promise<{ success: boolean; data?: UploadResponse; error?: string }> {
        try {
            const response = await fetch(`${API_BASE_URL}/knowledge/upload`, {
                method: 'POST',
                headers: getAuthHeader(),
                body: formData,
            })

            if (response.ok) {
                const data = await response.json()
                return { success: true, data }
            } else {
                const errorData = await response.json().catch(() => ({}))
                return {
                    success: false,
                    error: errorData.detail || `HTTP ${response.status}`,
                }
            }
        } catch (err) {
            return {
                success: false,
                error: err instanceof Error ? err.message : '网络错误',
            }
        }
    },

    getUploadUrl(): string {
        return `${API_BASE_URL}/knowledge/upload`
    },

    getAuthHeadersForXHR(): Record<string, string> {
        return getAuthHeader()
    },

    /**
     * 获取文件处理状态
     */
    async getFileStatus(id: string): Promise<{ status: string;[key: string]: any }> {
        const response = await fetch(`${API_BASE_URL}/knowledge/files/${id}/info`, {
            headers: getAuthHeader(),
        })

        if (!response.ok) {
            throw new Error(`Failed to fetch file status: ${response.status}`)
        }

        return response.json()
    },

    async updateFileMetadata(id: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
        const response = await fetch(`${API_BASE_URL}/knowledge/files/${id}/metadata`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        await ensureMutationSuccess(response, '更新文档元数据失败')
        return response.json()
    },

    /**
     * 获取入库任务状态
     */
    async getIngestionTask(taskId: string): Promise<IngestionTaskStatus> {
        const response = await fetch(`${API_BASE_URL}/knowledge/tasks/${taskId}`, {
            headers: getAuthHeader(),
        })
        if (!response.ok) {
            throw new Error(`Failed to fetch ingestion task: ${response.status}`)
        }
        return response.json()
    },

    /**
     * 取消入库任务
     */
    async cancelIngestionTask(taskId: string): Promise<IngestionTaskStatus> {
        const response = await fetch(`${API_BASE_URL}/knowledge/tasks/${taskId}/cancel`, {
            method: 'POST',
            headers: getAuthHeader(),
        })
        if (!response.ok) {
            throw new Error(`Failed to cancel ingestion task: ${response.status}`)
        }
        return response.json()
    },

    /**
     * 删除文件
     */
    async deleteFile(id: string): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/knowledge/documents/${id}`, {
            method: 'DELETE',
            headers: getAuthHeader(),
        })
        await ensureMutationSuccess(response, '删除文件失败')
    },

    /**
     * 批量删除文件
     */
    async batchDeleteFiles(ids: string[]): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/knowledge/files/batch-delete`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        })
        await ensureMutationSuccess(response, '批量删除文件失败')
    },

    /**
     * 重命名文件
     */
    async renameFile(id: string, name: string): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/knowledge/files/${id}/name`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        })
        await ensureMutationSuccess(response, '重命名文件失败')
    },

    /**
     * 移动文件
     */
    async moveFile(id: string, targetFolderId: string | null): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/knowledge/files/${id}/move`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ parent_id: targetFolderId }),
        })
        await ensureMutationSuccess(response, '移动文件失败')
    },

    // =========================================================================
    // 图谱关系操作
    // =========================================================================

    /**
     * 创建关联
     */
    async createRelationship(payload: CreateRelationshipPayload): Promise<void> {
        await fetch(`${API_BASE_URL}/knowledge/relationships`, {
            method: 'POST',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
    },

    /**
     * 删除关联
     */
    async deleteRelationship(sourceId: string, targetId: string): Promise<void> {
        await fetch(
            `${API_BASE_URL}/knowledge/relationships?source_id=${sourceId}&target_id=${targetId}`,
            {
                method: 'DELETE',
                headers: getAuthHeader(),
            }
        )
    },

    /**
     * 更新节点位置
     */
    async updateNodePosition(nodeId: string, position: NodePosition): Promise<void> {
        await fetch(`${API_BASE_URL}/knowledge/nodes/${nodeId}/position`, {
            method: 'PUT',
            headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
            body: JSON.stringify(position),
        })
    },
}
