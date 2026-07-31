/**
 * SQL 示例数据管理 Hook
 * 
 * 职责：
 * - 管理 SQL 示例和分组数据
 * - 处理 CRUD 操作和批量操作
 * - 管理筛选和选择状态
 */
import { useState, useCallback, useEffect, useMemo } from 'react'
import { useToast } from '@/components/ui/toast'
import { extendConfigService } from '@/services/extendConfigService'
import type {
    SqlExample,
    SqlGroup,
    SqlExampleForm,
    SqlGroupForm,
} from '@/types/extendConfig'

export interface UseSqlExamplesReturn {
    // 数据
    examples: SqlExample[]
    groups: SqlGroup[]
    filteredExamples: SqlExample[]

    // 加载状态
    isLoading: boolean
    isSaving: boolean

    // 筛选
    filterGroupId: number | 'all'
    setFilterGroupId: (id: number | 'all') => void

    // 批量选择
    selectedIds: Set<number>
    toggleSelect: (id: number) => void
    selectAll: () => void
    clearSelection: () => void

    // CRUD 操作
    refresh: () => void
    saveExample: (id: number | null, data: SqlExampleForm) => Promise<boolean>
    deleteExample: (id: number) => Promise<boolean>
    toggleActive: (example: SqlExample) => Promise<void>
    batchDelete: () => Promise<boolean>
    batchMove: (targetGroupId: number | null) => Promise<boolean>

    // 分组操作
    saveGroup: (id: number | null, data: SqlGroupForm) => Promise<boolean>
}

export function useSqlExamples(): UseSqlExamplesReturn {
    const { toast } = useToast()

    // 数据状态
    const [examples, setExamples] = useState<SqlExample[]>([])
    const [groups, setGroups] = useState<SqlGroup[]>([])

    // 加载状态
    const [isLoading, setIsLoading] = useState(true)
    const [isSaving, setIsSaving] = useState(false)

    // 筛选状态
    const [filterGroupId, setFilterGroupId] = useState<number | 'all'>('all')

    // 批量选择状态
    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())

    // 计算过滤后的示例
    const filteredExamples = useMemo(() => {
        if (filterGroupId === 'all') return examples
        return examples.filter(e => e.group_id === filterGroupId)
    }, [examples, filterGroupId])

    // 加载数据
    const loadData = useCallback(async () => {
        setIsLoading(true)
        try {
            const [examplesData, groupsData] = await Promise.all([
                extendConfigService.getSqlExamples(),
                extendConfigService.getSqlGroups(),
            ])
            setExamples(examplesData)
            setGroups(groupsData)
        } catch (error) {
            console.error('加载 SQL 示例失败:', error)
            toast({
                type: 'error',
                title: '加载失败',
                description: error instanceof Error ? error.message : '加载 SQL 示例失败',
            })
        } finally {
            setIsLoading(false)
        }
    }, [toast])

    // 初始化加载
    useEffect(() => {
        loadData()
    }, [loadData])

    // 切换选择
    const toggleSelect = useCallback((id: number) => {
        setSelectedIds(prev => {
            const newSet = new Set(prev)
            if (newSet.has(id)) {
                newSet.delete(id)
            } else {
                newSet.add(id)
            }
            return newSet
        })
    }, [])

    // 全选/取消全选
    const selectAll = useCallback(() => {
        if (selectedIds.size === filteredExamples.length && filteredExamples.length > 0) {
            setSelectedIds(new Set())
        } else {
            setSelectedIds(new Set(filteredExamples.map(e => e.id)))
        }
    }, [selectedIds.size, filteredExamples])

    // 清空选择
    const clearSelection = useCallback(() => {
        setSelectedIds(new Set())
    }, [])

    // 保存示例
    const saveExample = useCallback(async (id: number | null, data: SqlExampleForm): Promise<boolean> => {
        setIsSaving(true)
        try {
            await extendConfigService.saveSqlExample(id, data)
            await loadData()
            toast({
                type: 'success',
                title: id ? 'SQL 示例已更新' : 'SQL 示例已创建',
            })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '保存失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [loadData, toast])

    // 删除示例
    const deleteExample = useCallback(async (id: number): Promise<boolean> => {
        try {
            await extendConfigService.deleteSqlExample(id)
            // 乐观更新：立即从本地状态移除
            setExamples(prev => prev.filter(e => e.id !== id))
            toast({
                type: 'success',
                title: 'SQL 示例已删除',
            })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '删除失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        }
    }, [toast])

    // 切换启用状态
    const toggleActive = useCallback(async (example: SqlExample): Promise<void> => {
        try {
            await extendConfigService.toggleSqlExampleActive(example.id, !example.is_active)
            // 乐观更新
            setExamples(prev => prev.map(e =>
                e.id === example.id ? { ...e, is_active: !e.is_active } : e
            ))
        } catch (error) {
            toast({
                type: 'error',
                title: '更新状态失败',
            })
        }
    }, [toast])

    // 批量删除
    const batchDelete = useCallback(async (): Promise<boolean> => {
        if (selectedIds.size === 0) return false

        try {
            const result = await extendConfigService.batchDeleteSqlExamples(Array.from(selectedIds))
            // 乐观更新：立即从本地状态移除
            setExamples(prev => prev.filter(e => !selectedIds.has(e.id)))
            setSelectedIds(new Set())
            // 刷新分组计数
            const groupsData = await extendConfigService.getSqlGroups()
            setGroups(groupsData)
            toast({
                type: 'success',
                title: result.message || `成功删除 ${selectedIds.size} 条示例`,
            })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '批量删除失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        }
    }, [selectedIds, toast])

    // 批量移动
    const batchMove = useCallback(async (targetGroupId: number | null): Promise<boolean> => {
        if (selectedIds.size === 0) return false

        try {
            const result = await extendConfigService.batchMoveSqlExamples(
                Array.from(selectedIds),
                targetGroupId
            )
            await loadData()
            setSelectedIds(new Set())
            toast({
                type: 'success',
                title: result.message || `成功移动 ${selectedIds.size} 条示例`,
            })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '批量移动失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        }
    }, [selectedIds, loadData, toast])

    // 保存分组
    const saveGroup = useCallback(async (id: number | null, data: SqlGroupForm): Promise<boolean> => {
        setIsSaving(true)
        try {
            await extendConfigService.saveSqlGroup(id, data)
            const groupsData = await extendConfigService.getSqlGroups()
            setGroups(groupsData)
            toast({
                type: 'success',
                title: id ? '分组已更新' : '分组已创建',
            })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '保存分组失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [toast])

    return {
        examples,
        groups,
        filteredExamples,
        isLoading,
        isSaving,
        filterGroupId,
        setFilterGroupId,
        selectedIds,
        toggleSelect,
        selectAll,
        clearSelection,
        refresh: loadData,
        saveExample,
        deleteExample,
        toggleActive,
        batchDelete,
        batchMove,
        saveGroup,
    }
}
