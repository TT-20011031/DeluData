import { useCallback, useEffect, useState } from 'react'

import { fetchWithAuth } from '@/stores/authStore'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'
const POLL_INTERVAL_MS = 15000

export interface WorkspaceReadiness {
    has_db: boolean
    has_knowledge: boolean
    db: {
        connected: boolean
        source?: string | null
        host?: string | null
        port?: number | null
        database?: string | null
    }
    knowledge: {
        global_count: number
        private_count: number
        dept_count: number
        total_count: number
    }
    reasons: string[]
}

export interface UseWorkspaceReadinessReturn {
    readiness: WorkspaceReadiness | null
    hasDb: boolean
    hasKnowledge: boolean
    isAvailable: boolean
    isLoading: boolean
    error: string | null
    refresh: () => Promise<void>
}

async function parseJsonSafe(res: Response): Promise<any> {
    const text = await res.text()
    try {
        return text ? JSON.parse(text) : {}
    } catch {
        return { detail: text || 'invalid_response' }
    }
}

export function useWorkspaceReadiness(): UseWorkspaceReadinessReturn {
    const [readiness, setReadiness] = useState<WorkspaceReadiness | null>(null)
    const [isLoading, setIsLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    const fetchReadiness = useCallback(async () => {
        try {
            const response = await fetchWithAuth(`${API_BASE_URL}/config/workspace-readiness`)
            const data = await parseJsonSafe(response)

            if (!response.ok) {
                setReadiness(null)
                setError(data.detail || data.message || `HTTP_${response.status}`)
                return
            }

            setReadiness(data as WorkspaceReadiness)
            setError(null)
        } catch (err) {
            setReadiness(null)
            setError(err instanceof Error ? err.message : 'network_error')
        } finally {
            setIsLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchReadiness()

        const timer = window.setInterval(() => {
            fetchReadiness()
        }, POLL_INTERVAL_MS)

        return () => {
            window.clearInterval(timer)
        }
    }, [fetchReadiness])

    return {
        readiness,
        hasDb: Boolean(readiness?.has_db),
        hasKnowledge: Boolean(readiness?.has_knowledge),
        isAvailable: readiness !== null,
        isLoading,
        error,
        refresh: fetchReadiness,
    }
}
