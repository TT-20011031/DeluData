import { API_BASE_URL } from '@/config'

export interface MaintenanceRecordResponse {
    document_id: string
    name: string
    status: string
    message: string
    task_id?: string
    folder_id: string
    image_count: number
}

async function parseError(response: Response, fallback: string): Promise<string> {
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
        const payload = await response.json().catch(() => ({}))
        const detail = payload?.detail
        if (typeof detail === 'string' && detail.trim()) return detail
        if (payload?.message && typeof payload.message === 'string') return payload.message
    }
    const text = await response.text().catch(() => '')
    return text.trim() || fallback
}

export const maintenanceRecordService = {
    async submit(formData: FormData): Promise<MaintenanceRecordResponse> {
        const response = await fetch(`${API_BASE_URL}/public/maintenance-records`, {
            method: 'POST',
            body: formData,
        })

        if (!response.ok) {
            throw new Error(await parseError(response, `提交失败: ${response.status}`))
        }

        return response.json()
    },
}
