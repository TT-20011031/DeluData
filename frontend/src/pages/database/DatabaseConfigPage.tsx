/**
 * 数据库配置页面 - Navicat模式
 * 
 * 三种视图：
 * 1. 连接表单（未连接时）
 * 2. Schema概览 + 数据浏览 + SQL查询（已连接时）
 */
import { useEffect, useState, useCallback, useMemo } from 'react'
import {
    Database, Loader2, Link2Off,
    Table2, RefreshCw,
    Eye, Code2, Network,
    type LucideIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import * as React from 'react'
import { Textarea } from '@/components/ui/textarea'
import { useDBStore } from '@/stores/dbStore'
import { cn } from '@/lib/utils'
import { getAuthHeader, useAuthStore } from '@/stores/authStore'
import { DatabaseConnectionForm } from '@/pages/database/components/DatabaseConnectionForm'
import { DatabaseDataTab } from '@/pages/database/components/DatabaseDataTab'
import { DatabaseQueryTab } from '@/pages/database/components/DatabaseQueryTab'
import { DatabaseSchemaTab } from '@/pages/database/components/DatabaseSchemaTab'
import { DatabaseTableSidebar } from '@/pages/database/components/DatabaseTableSidebar'
import { SemanticModelManager } from '@/components/extendConfig'
import type { ConnectionConfig, ConnectionTestMessage, QueryResult, TableData } from '@/pages/database/types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'
type DatabaseTab = 'schema' | 'data' | 'query' | 'semantic'
type DatabaseTabItem = { id: DatabaseTab; label: string; icon: LucideIcon }

/** 安全解析 JSON，防止 502/504 HTML 返回导致 Unexpected token '<' */
async function parseJsonSafe(res: Response): Promise<any> {
    const text = await res.text()
    try { return text ? JSON.parse(text) : {} }
    catch { return { detail: text || '响应解析失败' } }
}

export default function DatabaseConfigPage() {
    const {
        isConnected,
        isLoading,
        isStatusLoading,
        hasFetchedStatus,
        isSchemaLoading,
        hasFetchedSchema,
        schemaError,
        error,
        errorCode,
        host,
        database,
        tables,
        schema,
        source,
        canManageConnection,
        canManageSemantic,
        canQuerySql,
        connect,
        disconnect,
        fetchStatus,
        fetchSchema,
        testConnection,
        clearError,
    } = useDBStore()

    const { user } = useAuthStore()
    const isAdmin = Boolean(user?.permissions?.some((code) => code === '*' || code === 'database:manage'))
    const canConfigureConnection = canManageConnection || isAdmin

    const [isTesting, setIsTesting] = useState(false)
    const [testMessage, setTestMessage] = useState<ConnectionTestMessage | null>(null)

    const [config, setConfig] = useState<ConnectionConfig>({
        host: 'localhost',
        port: 3306,
        username: 'root',
        password: '',
        database: '',
    })

    // 视图状态
    const [activeTab, setActiveTab] = useState<DatabaseTab>(() =>
        new URLSearchParams(window.location.search).get('tab') === 'semantic' ? 'semantic' : 'schema'
    )
    const [expandedTable, setExpandedTable] = useState<string | null>(null)
    const [selectedTable, setSelectedTable] = useState<string | null>(null)

    // 表数据状态
    const [tableData, setTableData] = useState<TableData | null>(null)
    const [isLoadingData, setIsLoadingData] = useState(false)
    const [dataPage, setDataPage] = useState(1)

    // SQL查询状态
    const [sqlQuery, setSqlQuery] = useState('')
    const [queryResult, setQueryResult] = useState<QueryResult | null>(null)
    const [isExecuting, setIsExecuting] = useState(false)

    // 表搜索状态
    const [searchQuery, setSearchQuery] = useState('')

    // 表描述状态
    const [tableDescriptions, setTableDescriptions] = useState<Record<string, string>>({})
    const [showDescDialog, setShowDescDialog] = useState(false)
    const [descriptionInput, setDescriptionInput] = useState('')
    const [isSavingDesc, setIsSavingDesc] = useState(false)
    const whitelistBlocked = errorCode === 'WHITELIST_BLOCKED' || testMessage?.code === 'WHITELIST_BLOCKED'

    // 侧边栏调整（使用 useRef 优化性能）
    const sidebarRef = React.useRef<HTMLDivElement>(null)
    const isResizingRef = React.useRef(false)
    const startXRef = React.useRef(0)
    const startWidthRef = React.useRef(0)

    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (!isResizingRef.current || !sidebarRef.current) return
            e.preventDefault() // 防止选中文本

            const deltaX = e.clientX - startXRef.current
            const newWidth = Math.max(200, Math.min(800, startWidthRef.current + deltaX))

            sidebarRef.current.style.width = `${newWidth}px`
        }

        const handleMouseUp = () => {
            isResizingRef.current = false
            document.body.style.cursor = ''
            document.body.style.userSelect = ''
        }

        document.addEventListener('mousemove', handleMouseMove)
        document.addEventListener('mouseup', handleMouseUp)

        return () => {
            document.removeEventListener('mousemove', handleMouseMove)
            document.removeEventListener('mouseup', handleMouseUp)
        }
    }, [])

    const handleMouseDown = (e: React.MouseEvent) => {
        isResizingRef.current = true
        startXRef.current = e.clientX
        startWidthRef.current = sidebarRef.current?.getBoundingClientRect().width || 288

        document.body.style.cursor = 'col-resize'
        document.body.style.userSelect = 'none'
    }

    // 初始化时获取状态
    useEffect(() => {
        fetchStatus(true)
    }, [fetchStatus, user?.id])

    // 连接成功后获取 Schema
    useEffect(() => {
        if (isConnected && !hasFetchedSchema && !isSchemaLoading) {
            fetchSchema()
        }
    }, [isConnected, hasFetchedSchema, isSchemaLoading, fetchSchema])

    const visibleTabs = useMemo<DatabaseTabItem[]>(() => {
        const tabs: DatabaseTabItem[] = [
            { id: 'schema', label: '结构', icon: Table2 },
            { id: 'data', label: '数据', icon: Eye },
        ]

        if (canQuerySql) {
            tabs.push({ id: 'query', label: 'SQL', icon: Code2 })
        }
        if (canManageSemantic) {
            tabs.push({ id: 'semantic', label: '语义模型', icon: Network })
        }

        return tabs
    }, [canManageSemantic, canQuerySql])

    useEffect(() => {
        if (!visibleTabs.some(tab => tab.id === activeTab)) {
            setActiveTab(visibleTabs[0]?.id || 'schema')
        }
    }, [activeTab, visibleTabs])

    const handleConnect = async () => {
        await connect({
            ...config,
            port: Number(config.port),
        })
    }

    const handleTestConnection = useCallback(async () => {
        setIsTesting(true)
        setTestMessage(null)
        clearError()
        const res = await testConnection({ ...config, port: Number(config.port) })
        setTestMessage({
            success: res.success,
            message: res.success ? '测试连接成功' : res.message,
            code: res.code,
        })
        setIsTesting(false)
    }, [clearError, config, testConnection])

    const handleDisconnect = async () => {
        await disconnect()
        setSelectedTable(null)
        setTableData(null)
        setQueryResult(null)
    }

    const updateConfig = (field: keyof ConnectionConfig, value: string | number) => {
        setConfig(prev => ({ ...prev, [field]: value }))
        clearError()
    }

    // 获取表数据
    const fetchTableData = useCallback(async (tableName: string, page: number = 1) => {
        setIsLoadingData(true)
        try {
            // 获取表主键用于默认倒序排序
            const currentTableSchema = schema.find(t => t.name === tableName)
            const pkColumn = currentTableSchema?.columns.find(c => c.primary_key)?.name

            let url = `${API_BASE_URL}/db/tables/${encodeURIComponent(tableName)}/data?page=${page}&page_size=50`

            // 如果有主键，默认使用倒序
            if (pkColumn) {
                url += `&sort_by=${pkColumn}&sort_order=DESC`
            }

            const response = await fetch(
                url,
                { headers: getAuthHeader() }
            )
            const data = await parseJsonSafe(response)
            if (response.ok) {
                setTableData({
                    columns: data.columns,
                    rows: data.rows,
                    totalCount: data.total_count,
                    page: data.page,
                    pageSize: data.page_size,
                    hasMore: data.has_more
                })
                setDataPage(page)
            }
        } catch (error) {
            console.error('获取表数据失败:', error)
        }
        setIsLoadingData(false)
    }, [schema]) // schema 变化时更新依赖

    // 选择表
    const handleSelectTable = (tableName: string) => {
        setSelectedTable(tableName)
        setActiveTab('data')
        fetchTableData(tableName, 1)
    }

    const openDescriptionEditor = useCallback((tableName: string) => {
        setSelectedTable(tableName)
        setDescriptionInput(tableDescriptions[tableName] || '')
        setShowDescDialog(true)
    }, [tableDescriptions])

    // 执行 SQL 查询
    const executeQuery = async () => {
        if (!sqlQuery.trim()) return

        setIsExecuting(true)
        setQueryResult(null)

        try {
            const response = await fetch(`${API_BASE_URL}/db/query`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader()
                },
                body: JSON.stringify({
                    sql: sqlQuery,
                    timeout_sec: 5,
                    max_rows: 1000
                })
            })

            const data = await parseJsonSafe(response)

            if (response.ok) {
                setQueryResult({
                    columns: data.columns,
                    rows: data.rows,
                    rowCount: data.row_count,
                    truncated: data.truncated,
                    executionTimeMs: data.execution_time_ms,
                    error: data.error
                })
            } else {
                setQueryResult({
                    columns: [],
                    rows: [],
                    rowCount: 0,
                    truncated: false,
                    executionTimeMs: 0,
                    error: data.detail || '查询失败'
                })
            }
        } catch (error) {
            setQueryResult({
                columns: [],
                rows: [],
                rowCount: 0,
                truncated: false,
                executionTimeMs: 0,
                error: '网络错误'
            })
        }

        setIsExecuting(false)
    }

    if (!hasFetchedStatus || (isStatusLoading && !isConnected)) {
        return (
            <div className="flex-1 flex flex-col h-full bg-manus p-6" aria-busy="true">
                <div className="m-auto w-full max-w-md text-center">
                    <div className="relative mx-auto mb-5 h-14 w-14">
                        <div className="absolute inset-0 rounded-2xl bg-accent/10" />
                        <Database className="absolute inset-0 m-auto h-7 w-7 text-accent" />
                        <Loader2 className="absolute -inset-1 h-16 w-16 animate-spin text-accent/35" />
                    </div>
                    <h1 className="text-lg font-medium text-manus-text">正在确认数据库连接</h1>
                    <p className="mt-2 text-sm leading-6 text-manus-muted">
                        正在读取已保存的连接状态，请稍候。大型数据库不会影响连接是否有效。
                    </p>
                    <div className="mx-auto mt-5 h-1 w-40 overflow-hidden rounded-full bg-manus-tertiary">
                        <div className="h-full w-2/3 animate-pulse rounded-full bg-accent/70" />
                    </div>
                </div>
            </div>
        )
    }

    if (isConnected && hasFetchedSchema && !isSchemaLoading && !canConfigureConnection && tables.length === 0) {
        return (
            <div className="flex-1 flex flex-col h-full bg-manus p-6">
                <div className="m-auto max-w-md text-center text-manus-muted">
                    <Database className="h-12 w-12 mx-auto mb-4 text-manus-subtle" />
                    <h1 className="text-lg font-medium text-manus-text mb-2">暂无可访问的数据范围</h1>
                    <p className="text-sm leading-6">
                        管理员连接的数据库暂时没有可展示的数据表，请联系管理员检查数据库连接或表权限。
                    </p>
                </div>
            </div>
        )
    }

    // 渲染已连接的界面
    if (isConnected) {
        return (
            <>
                <div className="flex-1 flex flex-col h-full bg-manus overflow-hidden">
                    {/* 顶部工具栏*/}
                    <div className="flex items-center justify-between p-4 border-b border-manus-border bg-manus-secondary/50 backdrop-blur-sm">
                        <div className="flex items-center gap-3">
                            <div className="flex items-center gap-2">
                                <div className="relative">
                                    <Database className="h-5 w-5 text-success" />
                                    <div className="absolute -bottom-0.5 -right-0.5 w-2 h-2 bg-success rounded-full animate-pulse" />
                                </div>
                                <span className="font-medium text-manus-text">{database}</span>
                            </div>
                            <span className="text-manus-subtle text-sm">@{host}</span>
                            <span className="px-2 py-0.5 text-xs bg-success/20 text-success rounded-full">
                                {source === 'workspace' ? '共享连接' : '已连接'}
                            </span>
                        </div>

                        <div className="flex items-center gap-2">
                            {/* Tab切换 */}
                            <div className="flex bg-manus-tertiary rounded-lg p-1 mr-4">
                                {visibleTabs.map(tab => (
                                    <button
                                        key={tab.id}
                                        onClick={() => setActiveTab(tab.id)}
                                        className={cn(
                                            "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-all duration-200",
                                            activeTab === tab.id
                                                ? "bg-accent text-white shadow-lg shadow-accent/20"
                                                : "text-manus-muted hover:text-manus-text hover:bg-manus-hover"
                                        )}
                                    >
                                        <tab.icon className="h-4 w-4" />
                                        {tab.label}
                                    </button>
                                ))}
                            </div>

                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                    fetchStatus()
                                    fetchSchema()
                                }}
                                disabled={isLoading}
                                className="bg-manus-tertiary border-manus-border hover:bg-manus-hover text-manus-text"
                            >
                                <RefreshCw className={cn("h-4 w-4 mr-1.5", isLoading && "animate-spin")} />
                                刷新
                            </Button>
                            {canConfigureConnection && (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={handleDisconnect}
                                    className="bg-error/10 border-error/30 hover:bg-error/20 text-error"
                                >
                                    <Link2Off className="h-4 w-4 mr-1.5" />
                                    断开
                                </Button>
                            )}
                        </div>
                    </div>

                    {/* 主内容区 */}
                    <div className="flex-1 flex overflow-hidden">
                        {isSchemaLoading && schema.length === 0 ? (
                            <div className="flex flex-1 items-center justify-center p-8" aria-busy="true">
                                <div className="w-full max-w-lg rounded-xl border border-manus-border bg-manus-secondary p-7 shadow-sm">
                                    <div className="flex items-start gap-4">
                                        <div className="relative mt-0.5 h-11 w-11 shrink-0 rounded-xl bg-success/10">
                                            <Database className="absolute inset-0 m-auto h-5 w-5 text-success" />
                                            <Loader2 className="absolute -inset-1 h-[52px] w-[52px] animate-spin text-success/35" />
                                        </div>
                                        <div>
                                            <div className="flex items-center gap-2">
                                                <h2 className="font-medium text-manus-text">数据库已连接</h2>
                                                <span className="rounded-full bg-success/15 px-2 py-0.5 text-xs text-success">连接正常</span>
                                            </div>
                                            <p className="mt-2 text-sm leading-6 text-manus-muted">
                                                正在后台读取表和字段结构。数据库较大时可能需要一些时间，期间可以安全离开此页面，其他功能不受影响。
                                            </p>
                                            <div className="mt-4 flex items-center gap-2 text-xs text-manus-subtle">
                                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                正在加载 {database}@{host} 的元数据
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        ) : schemaError && schema.length === 0 ? (
                            <div className="flex flex-1 items-center justify-center p-8">
                                <div className="max-w-md text-center">
                                    <Database className="mx-auto h-10 w-10 text-success" />
                                    <h2 className="mt-4 font-medium text-manus-text">数据库已连接，表结构暂未加载</h2>
                                    <p className="mt-2 text-sm leading-6 text-manus-muted">{schemaError}</p>
                                    <Button className="mt-5" onClick={() => fetchSchema()}>
                                        <RefreshCw className="mr-2 h-4 w-4" />
                                        重新加载表结构
                                    </Button>
                                </div>
                            </div>
                        ) : (
                            <>
                        {activeTab !== 'semantic' && (
                            <DatabaseTableSidebar
                                schema={schema}
                                tableNames={tables}
                                tablesCount={tables.length}
                                searchQuery={searchQuery}
                                selectedTable={selectedTable}
                                tableDescriptions={tableDescriptions}
                                sidebarRef={sidebarRef}
                                onSearchChange={setSearchQuery}
                                onSelectTable={handleSelectTable}
                                onResizeMouseDown={handleMouseDown}
                            />
                        )}

                        {/* 右侧内容区*/}
                        <div className="flex-1 flex flex-col overflow-hidden">
                            {activeTab === 'schema' && (
                                <DatabaseSchemaTab
                                    schema={schema}
                                    selectedTable={selectedTable}
                                    expandedTable={expandedTable}
                                    tableDescriptions={tableDescriptions}
                                    onExpandedTableChange={setExpandedTable}
                                    onOpenDescription={openDescriptionEditor}
                                />
                            )}

                            {activeTab === 'data' && (
                                <DatabaseDataTab
                                    selectedTable={selectedTable}
                                    tableData={tableData}
                                    isLoadingData={isLoadingData}
                                    dataPage={dataPage}
                                    onFetchPage={fetchTableData}
                                />
                            )}

                            {/* SQL 查询视图 */}
                            {activeTab === 'query' && (
                                <DatabaseQueryTab
                                    sqlQuery={sqlQuery}
                                    queryResult={queryResult}
                                    isExecuting={isExecuting}
                                    onSqlQueryChange={setSqlQuery}
                                    onExecuteQuery={executeQuery}
                                />
                            )}

                            {activeTab === 'semantic' && (
                                <div className="flex-1 min-h-0 overflow-hidden p-3">
                                    <SemanticModelManager />
                                </div>
                            )}
                        </div>
                            </>
                        )}
                    </div>
                </div>

                {/* 表描述编辑 Dialog */}
                <Dialog open={showDescDialog} onOpenChange={setShowDescDialog}>
                    <DialogContent className="bg-manus-secondary border-manus-border text-manus-text max-w-md">
                        <DialogHeader>
                            <DialogTitle>表描述 - {selectedTable}</DialogTitle>
                        </DialogHeader>
                        <div className="py-4">
                            <Textarea
                                value={descriptionInput}
                                onChange={(e) => setDescriptionInput(e.target.value)}
                                placeholder="请输入表的用途描述..."
                                className="bg-manus-tertiary border-manus-border text-manus-text min-h-[100px]"
                            />
                        </div>
                        <DialogFooter>
                            <Button
                                variant="outline"
                                onClick={() => setShowDescDialog(false)}
                                className="bg-transparent border-manus-border text-manus-text hover:bg-manus-tertiary"
                            >
                                取消
                            </Button>
                            <Button
                                onClick={async () => {
                                    if (!selectedTable || isSavingDesc) return
                                    setIsSavingDesc(true)
                                    try {
                                        const saveRes = await fetch(`${API_BASE_URL}/db/tables/${encodeURIComponent(selectedTable)}/description`, {
                                            method: 'POST',
                                            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
                                            body: JSON.stringify({ table_name: selectedTable, description: descriptionInput })
                                        })
                                        if (!saveRes.ok) {
                                            const err = await parseJsonSafe(saveRes)
                                            throw new Error(err.detail || '保存失败')
                                        }
                                        setTableDescriptions(prev => ({ ...prev, [selectedTable]: descriptionInput }))
                                        setShowDescDialog(false)
                                    } catch (e: any) { console.error(e); alert(e.message || '保存描述失败') }
                                    finally { setIsSavingDesc(false) }
                                }}
                                disabled={isSavingDesc}
                                className="bg-accent hover:bg-accent/90 text-white"
                            >
                                {isSavingDesc && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                保存
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>
            </>
        )
    }

    if (!canConfigureConnection) {
        return (
            <div className="flex-1 flex flex-col h-full bg-manus p-6">
                <div className="m-auto max-w-md text-center text-manus-muted">
                    <Database className="h-12 w-12 mx-auto mb-4 text-manus-subtle" />
                    <h1 className="text-lg font-medium text-manus-text mb-2">暂无可用数据库</h1>
                    <p className="text-sm leading-6">
                        请联系管理员在数据库页面连接数据库，连接成功后你可以直接查看可用的数据表。
                    </p>
                </div>
            </div>
        )
    }

    // 渲染连接表单
    return (
        <DatabaseConnectionForm
            config={config}
            isLoading={isLoading}
            isTesting={isTesting}
            error={error}
            whitelistBlocked={whitelistBlocked}
            testMessage={testMessage}
            onConfigChange={updateConfig}
            onTestConnection={handleTestConnection}
            onConnect={handleConnect}
        />
    )
}


