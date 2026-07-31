import { create } from 'zustand'

import { getAuthHeader } from './authStore'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

async function parseJsonSafe(res: Response): Promise<any> {
    const text = await res.text()
    try {
        return text ? JSON.parse(text) : {}
    } catch {
        return { raw: text }
    }
}

interface TableColumn {
    name: string
    type: string
    nullable: boolean
    primary_key: boolean
    business_name?: string
    description?: string | null
    is_sensitive?: boolean
    queryable?: boolean
}

interface TableSchema {
    name: string
    columns: TableColumn[]
    row_count?: number
    business_name?: string | null
    description?: string | null
    is_sensitive?: boolean
    queryable?: boolean
}

interface DBState {
    isConnected: boolean
    isLoading: boolean
    error: string | null
    errorCode: string | null

    host: string | null
    database: string | null
    tables: string[]
    schema: TableSchema[]
    source: 'user' | 'workspace' | null
    canManageConnection: boolean
    canManageSemantic: boolean
    canQuerySql: boolean
    canAsk: boolean

    connect: (config: {
        host: string
        port: number
        username: string
        password: string
        database: string
    }) => Promise<boolean>
    testConnection: (config: any) => Promise<{ success: boolean, message?: string, code?: string }>
    disconnect: () => Promise<void>
    fetchStatus: () => Promise<void>
    fetchSchema: () => Promise<void>
    clearError: () => void
}

export const useDBStore = create<DBState>((set, get) => ({
    isConnected: false,
    isLoading: false,
    error: null,
    errorCode: null,
    host: null,
    database: null,
    tables: [],
    schema: [],
    source: null,
    canManageConnection: false,
    canManageSemantic: false,
    canQuerySql: false,
    canAsk: false,

    connect: async (config) => {
        set({ isLoading: true, error: null, errorCode: null })

        try {
            const response = await fetch(`${API_BASE_URL}/db/connect`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify(config),
            })

            const data = await parseJsonSafe(response)

            if (!response.ok) {
                set({
                    isLoading: false,
                    error: data.detail || '连接失败',
                    errorCode: typeof data.code === 'string' ? data.code : null,
                })
                return false
            }

            set({
                isConnected: true,
                isLoading: false,
                host: config.host,
                database: config.database,
                tables: data.tables || [],
                source: data.source || 'user',
                canManageConnection: Boolean(data.can_manage_connection),
                canManageSemantic: Boolean(data.can_manage_semantic),
                canQuerySql: Boolean(data.can_query_sql),
                canAsk: Boolean(data.can_ask),
                error: null,
                errorCode: null,
            })

            return true
        } catch (error) {
            set({
                isLoading: false,
                error: error instanceof Error ? error.message : '网络错误',
                errorCode: null,
            })
            return false
        }
    },

    testConnection: async (config: any): Promise<{ success: boolean, message?: string, code?: string }> => {
        try {
            const response = await fetch(`${API_BASE_URL}/db/test`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify(config),
            })
            const data = await parseJsonSafe(response)
            if (response.ok) {
                return { success: true }
            }
            return {
                success: false,
                message: data.detail || '测试连接失败',
                code: typeof data.code === 'string' ? data.code : undefined,
            }
        } catch (error) {
            return { success: false, message: error instanceof Error ? error.message : '网络错误' }
        }
    },

    disconnect: async () => {
        set({ isLoading: true })

        try {
            await fetch(`${API_BASE_URL}/db/disconnect`, {
                method: 'POST',
                headers: getAuthHeader(),
            })
        } catch {
            // ignore
        }

        set({
            isConnected: false,
            isLoading: false,
            host: null,
            database: null,
            tables: [],
            schema: [],
            source: null,
            canManageConnection: false,
            canManageSemantic: false,
            canQuerySql: false,
            canAsk: false,
            errorCode: null,
        })
    },

    fetchStatus: async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/db/status`, {
                headers: getAuthHeader(),
            })

            if (!response.ok) return

            const data = await response.json()

            set({
                isConnected: data.is_connected,
                host: data.host,
                database: data.database,
                tables: data.tables || [],
                source: data.source || null,
                canManageConnection: Boolean(data.can_manage_connection),
                canManageSemantic: Boolean(data.can_manage_semantic),
                canQuerySql: Boolean(data.can_query_sql),
                canAsk: Boolean(data.can_ask),
            })
        } catch {
            // ignore
        }
    },

    fetchSchema: async () => {
        const { isConnected } = get()
        if (!isConnected) return

        set({ isLoading: true })

        try {
            const response = await fetch(`${API_BASE_URL}/db/schema`, {
                headers: getAuthHeader(),
            })

            if (!response.ok) {
                set({ isLoading: false })
                return
            }

            const data = await response.json()

            set({
                schema: data.tables || [],
                isLoading: false,
            })
        } catch {
            set({ isLoading: false })
        }
    },

    clearError: () => set({ error: null, errorCode: null }),
}))
