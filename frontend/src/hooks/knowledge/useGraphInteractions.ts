/**
 * 图谱交互管理 Hook
 * 
 * 职责：
 * - 管理连线创建对话框状态
 * - 处理边右键菜单
 * - 节点拖拽位置保存
 */
import { useState, useCallback, useEffect } from 'react'
import { knowledgeService } from '@/services/knowledgeService'
import type { ConnectParams, RelationType } from '@/types/knowledge'
import type { Edge } from '@xyflow/react'

export interface UseGraphInteractionsOptions {
    onSuccess?: () => void
}

export interface ConnectState {
    params: ConnectParams | null
    relationType: RelationType
    customRelation: string
}

export interface EdgeMenuState {
    position: { x: number; y: number } | null
    edge: Edge | null
}

export interface GraphHandlers {
    openConnectDialog: (params: { source: string; target: string }) => void
    setRelationType: (type: RelationType) => void
    setCustomRelation: (val: string) => void
    confirmConnect: () => Promise<void>
    cancelConnect: () => void
    handleEdgeContextMenu: (event: React.MouseEvent, edge: Edge) => void
    handleEditEdge: () => void
    handleDeleteEdge: () => Promise<void>
    closeEdgeMenu: () => void
    handleNodeDragStop: (nodeId: string, position: { x: number; y: number }) => Promise<void>
}

export interface UseGraphInteractionsReturn {
    connectState: ConnectState
    edgeMenuState: EdgeMenuState
    handlers: GraphHandlers
}

export function useGraphInteractions(
    options: UseGraphInteractionsOptions = {}
): UseGraphInteractionsReturn {
    const { onSuccess } = options

    // 连接对话框状态
    const [connectingParams, setConnectingParams] = useState<ConnectParams | null>(null)
    const [relationType, setRelationType] = useState<RelationType>('关联')
    const [customRelation, setCustomRelation] = useState('')

    // 边右键菜单状态
    const [edgeMenu, setEdgeMenu] = useState<{
        x: number
        y: number
        edge: Edge
    } | null>(null)

    // 打开连接对话框
    const openConnectDialog = useCallback((params: { source: string; target: string }) => {
        setConnectingParams(params)
        setRelationType('关联')
        setCustomRelation('')
    }, [])

    // 确认创建连接
    const confirmConnect = useCallback(async () => {
        if (!connectingParams) return

        const finalType = relationType === 'custom' ? customRelation : relationType
        if (!finalType) {
            alert('请输入关联类型')
            return
        }

        try {
            await knowledgeService.createRelationship({
                source_id: connectingParams.source,
                target_id: connectingParams.target,
                relation_type: finalType,
            })
            onSuccess?.()
        } catch (e) {
            console.error('创建关联失败:', e)
        } finally {
            setConnectingParams(null)
        }
    }, [connectingParams, relationType, customRelation, onSuccess])

    // 取消连接
    const cancelConnect = useCallback(() => {
        setConnectingParams(null)
    }, [])

    // 边右键菜单
    const handleEdgeContextMenu = useCallback((event: React.MouseEvent, edge: Edge) => {
        event.preventDefault()
        setEdgeMenu({ x: event.clientX, y: event.clientY, edge })
    }, [])

    // 编辑边（重新打开连接对话框）
    const handleEditEdge = useCallback(() => {
        if (!edgeMenu) return

        setConnectingParams({
            source: edgeMenu.edge.source,
            target: edgeMenu.edge.target,
        })

        // 尝试匹配现有标签到预设或自定义
        const label = edgeMenu.edge.label as string
        const presetTypes = ['关联', '引用', '补充', '冲突', '前置']
        if (presetTypes.includes(label)) {
            setRelationType(label as RelationType)
            setCustomRelation('')
        } else {
            setRelationType('custom')
            setCustomRelation(label || '')
        }

        setEdgeMenu(null)
    }, [edgeMenu])

    // 删除边
    const handleDeleteEdge = useCallback(async () => {
        if (!edgeMenu) return

        try {
            await knowledgeService.deleteRelationship(edgeMenu.edge.source, edgeMenu.edge.target)
            onSuccess?.()
        } catch (e) {
            console.error('删除边失败:', e)
        } finally {
            setEdgeMenu(null)
        }
    }, [edgeMenu, onSuccess])

    // 关闭边菜单
    const closeEdgeMenu = useCallback(() => {
        setEdgeMenu(null)
    }, [])

    // 节点拖拽结束
    const handleNodeDragStop = useCallback(
        async (nodeId: string, position: { x: number; y: number }) => {
            try {
                await knowledgeService.updateNodePosition(nodeId, position)
            } catch (e) {
                console.error('保存节点位置失败:', e)
            }
        },
        []
    )

    // 点击其他地方关闭菜单
    useEffect(() => {
        const closeMenu = () => setEdgeMenu(null)
        window.addEventListener('click', closeMenu)
        return () => window.removeEventListener('click', closeMenu)
    }, [])

    return {
        connectState: {
            params: connectingParams,
            relationType,
            customRelation,
        },
        edgeMenuState: {
            position: edgeMenu ? { x: edgeMenu.x, y: edgeMenu.y } : null,
            edge: edgeMenu?.edge ?? null,
        },
        handlers: {
            openConnectDialog,
            setRelationType,
            setCustomRelation,
            confirmConnect,
            cancelConnect,
            handleEdgeContextMenu,
            handleEditEdge,
            handleDeleteEdge,
            closeEdgeMenu,
            handleNodeDragStop,
        },
    }
}
