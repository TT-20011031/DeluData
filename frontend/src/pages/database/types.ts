export interface TableData {
    columns: string[]
    rows: any[][]
    totalCount: number
    page: number
    pageSize: number
    hasMore: boolean
}

export interface TableColumn {
    name: string
    type: string
    nullable: boolean
    primary_key: boolean
    business_name?: string
    description?: string | null
    is_sensitive?: boolean
    queryable?: boolean
}

export interface TableSchema {
    name: string
    columns: TableColumn[]
    row_count?: number
    business_name?: string | null
    description?: string | null
    is_sensitive?: boolean
    queryable?: boolean
}

export interface QueryResult {
    columns: string[]
    rows: any[][]
    rowCount: number
    truncated: boolean
    executionTimeMs: number
    error: string | null
}

export interface ConnectionConfig {
    host: string
    port: number
    username: string
    password: string
    database: string
}

export interface ConnectionTestMessage {
    success: boolean
    message?: string
    code?: string
}
