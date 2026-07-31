/**
 * 临时产物暂存箱 API
 */
import { API_BASE_URL } from '@/config'
import { getAuthHeader } from '@/stores/authStore'

export interface TempArtifact {
    id: string
    type: 'chart' | 'doc'
    file_kind?: 'word' | 'excel'
    title: string
    session_id: string
    workspace_id: string
    user_id: string
    created_at: string
    expires_at: string
    download_url?: string
    file_name?: string
}

export interface ArtifactListResponse {
    items: TempArtifact[]
    total: number
}

const BASE = `${API_BASE_URL}/sandbox`

export const artifactApi = {
    async list(): Promise<TempArtifact[]> {
        const res = await fetch(`${BASE}/artifacts`, {
            headers: { ...getAuthHeader() },
        })
        if (!res.ok) throw new Error(`Failed to list artifacts: ${res.status}`)
        const data: ArtifactListResponse = await res.json()
        return data.items
    },

    async getContent(artifactId: string): Promise<string> {
        const res = await fetch(`${BASE}/artifacts/${encodeURIComponent(artifactId)}/content`, {
            headers: { ...getAuthHeader() },
        })
        if (!res.ok) throw new Error(`Failed to get artifact content: ${res.status}`)
        return res.text()
    },

    async delete(artifactId: string): Promise<void> {
        const res = await fetch(`${BASE}/artifacts/${encodeURIComponent(artifactId)}`, {
            method: 'DELETE',
            headers: { ...getAuthHeader() },
        })
        if (!res.ok && res.status !== 204) {
            throw new Error(`Failed to delete artifact: ${res.status}`)
        }
    },
}
