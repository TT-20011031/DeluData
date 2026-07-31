/**
 * 知识库数据管理 Hook
 * 
 * 职责：
 * - 管理 statsData, folderStructure, graphNodes, graphEdges
 * - 处理数据加载和 SSE 实时连接
 * - 管理用户角色和部门信息
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useAuthStore } from '@/stores/authStore'
import { useToast } from '@/components/ui/toast'
import { knowledgeService } from '@/services/knowledgeService'
import { API_BASE_URL } from '@/config'
import type {
    StatsData,
    FolderNode,
    KnowledgeNode,
    KnowledgeEdge,
    DepartmentOption,
} from '@/types/knowledge'

const KNOWLEDGE_FILE_STATUS_EVENT = 'knowledge:file-status-changed'

export interface UseKnowledgeDataOptions {
    viewMode: 'repository' | 'graph' | 'wiki'
    currentScope: string
}

export interface UseKnowledgeDataReturn {
    // 数据状态
    statsData: StatsData | null
    folderStructure: FolderNode[]
    graphNodes: KnowledgeNode[]
    graphEdges: KnowledgeEdge[]

    // 加载状态
    isLoading: boolean
    isRefreshing: boolean

    // 用户信息
    departments: DepartmentOption[]
    currentUserRole: string
    currentUserDeptId: number | null

    // 方法
    refresh: (silent?: boolean) => void
}

export function useKnowledgeData(options: UseKnowledgeDataOptions): UseKnowledgeDataReturn {
    const { viewMode, currentScope } = options
    const { toast } = useToast()

    // 数据状态
    const [statsData, setStatsData] = useState<StatsData | null>(null)
    const [folderStructure, setFolderStructure] = useState<FolderNode[]>([])
    const [graphNodes, setGraphNodes] = useState<KnowledgeNode[]>([])
    const [graphEdges, setGraphEdges] = useState<KnowledgeEdge[]>([])

    // 加载状态
    const [isLoading, setIsLoading] = useState(false)
    const [isRefreshing, setIsRefreshing] = useState(false)

    // 用户信息
    const [departments, setDepartments] = useState<DepartmentOption[]>([])
    const [currentUserRole, setCurrentUserRole] = useState<string>('user')
    const [currentUserDeptId, setCurrentUserDeptId] = useState<number | null>(null)

    // 用于 SSE 回调中安全调用的 ref
    const refreshRef = useRef<(silent?: boolean) => void>(() => { })

    // 数据加载函数
    const fetchData = useCallback(
        async (showGlobalLoading = true) => {
            // 如果传入的是事件对象，则强制视为 true
            const shouldBlock = typeof showGlobalLoading === 'boolean' ? showGlobalLoading : true

            if (shouldBlock) {
                setIsLoading(true)
            } else {
                setIsRefreshing(true)
            }

            try {
                const { token, user: cachedUser, syncCurrentUser } = useAuthStore.getState()

                // [性能优化] 并行请求用户信息和统计数据
                const parallelRequests: Promise<any>[] = []

                // 请求1: 获取用户信息
                const meRequest = token
                    ? fetch(`${import.meta.env.VITE_API_BASE_URL || '/api'}/auth/me`, {
                        headers: { Authorization: `Bearer ${token}` }
                    }).then(res => res.ok ? res.json() : null).catch(() => null)
                    : Promise.resolve(null)
                parallelRequests.push(meRequest)

                // 请求2: 获取统计数据 (repository 模式)
                const statsRequest = viewMode === 'repository'
                    ? knowledgeService.fetchStats()
                    : Promise.resolve(null)
                parallelRequests.push(statsRequest)

                // Wiki 知识图谱由 WikiGraphView 自行加载；这里不再拉取旧文件编排图。
                const graphRequest = Promise.resolve(null)
                parallelRequests.push(graphRequest)

                // 并行执行所有请求
                const [latestUser, stats, graphData] = await Promise.all(parallelRequests)

                // 处理用户信息
                if (latestUser) {
                    syncCurrentUser(latestUser)
                    const canManageKnowledge = latestUser.permissions?.some(
                        (code: string) => code === '*' || code === 'knowledge:manage'
                    )
                    setCurrentUserRole(canManageKnowledge ? 'knowledge_manager' : 'user')
                    setCurrentUserDeptId(latestUser.department_id ?? cachedUser?.department_id ?? null)

                    // 管理员需要额外加载部门列表（单独请求，因为依赖用户角色）
                    if (canManageKnowledge) {
                        try {
                            const deptList = await knowledgeService.fetchDepartments()
                            setDepartments(deptList)
                        } catch {
                            // 忽略部门列表加载失败
                        }
                    }
                } else if (token) {
                    // 回退到本地缓存的用户信息
                    if (cachedUser) {
                        const canManageKnowledge = cachedUser.permissions?.some(
                            (code: string) => code === '*' || code === 'knowledge:manage'
                        )
                        setCurrentUserRole(canManageKnowledge ? 'knowledge_manager' : 'user')
                        setCurrentUserDeptId(cachedUser.department_id ?? null)
                    }
                }

                // 处理 repository 模式数据
                if (viewMode === 'repository') {
                    if (stats) {
                        setStatsData(stats)
                    }
                    // 只有在非概览模式时才获取文件结构（依赖 scope，无法并行）
                    if (currentScope !== 'overview') {
                        const structure = await knowledgeService.fetchStructure(currentScope)
                        setFolderStructure(structure)
                    }
                } else {
                    // 图谱模式
                    if (graphData) {
                        setGraphNodes(graphData.nodes)
                        setGraphEdges(graphData.edges)
                    }
                }
            } catch (error) {
                console.error('加载数据失败:', error)
                // 如果是静默刷新失败，给用户提示
                if (!shouldBlock) {
                    toast({
                        type: 'error',
                        title: '数据更新失败',
                        description: '请尝试手动刷新页面',
                        duration: 3000,
                    })
                }
            } finally {
                if (shouldBlock) {
                    setIsLoading(false)
                } else {
                    setIsRefreshing(false)
                }
            }
        },
        [viewMode, currentScope, toast]
    )

    // 保持 refreshRef 始终指向最新的 fetchData
    useEffect(() => {
        refreshRef.current = fetchData
    }, [fetchData])

    // 初始化数据加载
    useEffect(() => {
        fetchData()
    }, [fetchData])

    // SSE 连接管理
    useEffect(() => {
        let eventSource: EventSource | null = null
        let reconnectAttempts = 0
        let reconnectTimer: ReturnType<typeof setTimeout> | null = null
        let isMounted = true

        const MAX_RECONNECT_ATTEMPTS = 5
        const BASE_RECONNECT_DELAY = 1000

        const connect = () => {
            // 组件已卸载，不再重连
            if (!isMounted) return

            const sseUrl = `${API_BASE_URL}/events/notifications`
            eventSource = new EventSource(sseUrl)

            eventSource.onopen = () => {
                console.log('[KnowledgeBase] SSE 连接成功')
                reconnectAttempts = 0
            }

            eventSource.onmessage = (event: MessageEvent) => {
                try {
                    const data = JSON.parse(event.data)
                    if (
                        data.payload?.type === 'document_ready' ||
                        data.payload?.type === 'document_error'
                    ) {
                        console.log('[KnowledgeBase] 状态更新，刷新数据')
                        refreshRef.current(false) // 使用 ref 避免闭包问题，并静默刷新
                    }
                } catch {
                    // 忽略解析错误
                }
            }

            eventSource.onerror = () => {
                console.warn('[KnowledgeBase] SSE 连接断开')
                eventSource?.close()
                eventSource = null

                if (isMounted && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                    // 指数退避重连
                    const delay = BASE_RECONNECT_DELAY * Math.pow(2, reconnectAttempts)
                    reconnectAttempts++
                    console.log(
                        `[KnowledgeBase] ${delay / 1000}秒后尝试重连 (${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`
                    )
                    reconnectTimer = setTimeout(connect, delay)
                } else if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
                    console.error('[KnowledgeBase] SSE 重连失败，已达最大重试次数')
                }
            }
        }

        // [性能优化] 延迟建立 SSE 连接，避免阻塞首次渲染
        if ('requestIdleCallback' in window) {
            (window as any).requestIdleCallback(connect, { timeout: 2000 })
        } else {
            setTimeout(connect, 100)
        }

        // 完整的清理函数：关闭连接 + 清除重连定时器
        return () => {
            isMounted = false
            if (reconnectTimer) {
                clearTimeout(reconnectTimer)
                reconnectTimer = null
            }
            if (eventSource) {
                eventSource.close()
                eventSource = null
            }
        }
    }, []) // 空依赖数组，只在组件挂载/卸载时执行

    // 上传任务状态变化时，静默刷新文件树，避免资源管理器状态滞后。
    useEffect(() => {
        let timer: ReturnType<typeof setTimeout> | null = null
        const onFileStatusChanged = () => {
            if (timer) {
                clearTimeout(timer)
            }
            timer = setTimeout(() => {
                refreshRef.current(false)
            }, 200)
        }
        window.addEventListener(KNOWLEDGE_FILE_STATUS_EVENT, onFileStatusChanged as EventListener)
        return () => {
            if (timer) {
                clearTimeout(timer)
                timer = null
            }
            window.removeEventListener(KNOWLEDGE_FILE_STATUS_EVENT, onFileStatusChanged as EventListener)
        }
    }, [])

    return {
        statsData,
        folderStructure,
        graphNodes,
        graphEdges,
        isLoading,
        isRefreshing,
        departments,
        currentUserRole,
        currentUserDeptId,
        refresh: fetchData,
    }
}
