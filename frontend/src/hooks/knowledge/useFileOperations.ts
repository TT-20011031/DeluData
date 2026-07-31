/**
 * 文件操作管理 Hook
 * 
 * 职责：
 * - 创建文件夹
 * - 删除文件/文件夹（单个和批量）
 * - 重命名
 * - 移动
 */
import { useState, useCallback } from 'react'
import { useToast } from '@/components/ui/toast'
import { knowledgeService } from '@/services/knowledgeService'
import type { DeleteTarget } from '@/types/knowledge'

export interface UseFileOperationsOptions {
    currentScope: string
    onSuccess?: () => void
}

export interface DeleteState {
    target: DeleteTarget | null
    batchTargets: DeleteTarget[]
    isDeleting: boolean
}

export interface FileOperationHandlers {
    createFolder: (name: string, parentId?: string) => Promise<void>
    deleteFolder: (id: string) => void
    deleteFile: (id: string) => void
    batchDelete: (items: DeleteTarget[]) => void
    confirmDelete: () => Promise<void>
    cancelDelete: () => void
    rename: (id: string, type: 'file' | 'folder', newName: string) => Promise<void>
    move: (sourceId: string, type: 'file' | 'folder', targetFolderId: string | null) => Promise<void>
}

export interface UseFileOperationsReturn {
    deleteState: DeleteState
    handlers: FileOperationHandlers
}

/**
 * 从 scope 推导 visibility 和 dept_id
 */
function deriveVisibilityFromScope(scope: string): { visibility: string; deptId: number | null } {
    if (scope === 'private') {
        return { visibility: 'private', deptId: null }
    } else if (scope.startsWith('dept_')) {
        const parsedId = parseInt(scope.replace('dept_', ''), 10)
        if (!isNaN(parsedId)) {
            return { visibility: 'dept', deptId: parsedId }
        }
    }
    return { visibility: 'public', deptId: null }
}

export function useFileOperations(options: UseFileOperationsOptions): UseFileOperationsReturn {
    const { currentScope, onSuccess } = options
    const { toast } = useToast()

    // 删除状态
    const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null)
    const [batchDeleteTargets, setBatchDeleteTargets] = useState<DeleteTarget[]>([])
    const [isDeleting, setIsDeleting] = useState(false)

    // 创建文件夹
    const createFolder = useCallback(
        async (name: string, parentId?: string) => {
            const { visibility, deptId } = deriveVisibilityFromScope(currentScope)

            try {
                await knowledgeService.createFolder({
                    name,
                    parent_id: parentId,
                    visibility,
                    dept_id: deptId,
                })
                onSuccess?.()
            } catch (e) {
                console.error('创建文件夹失败:', e)
                toast({
                    type: 'error',
                    title: '创建文件夹失败',
                    description: e instanceof Error ? e.message : '请稍后重试',
                })
            }
        },
        [currentScope, onSuccess, toast]
    )

    // 触发删除文件夹对话框
    const deleteFolder = useCallback((id: string) => {
        setDeleteTarget({ id, type: 'folder' })
    }, [])

    // 触发删除文件对话框
    const deleteFile = useCallback((id: string) => {
        setDeleteTarget({ id, type: 'file' })
    }, [])

    // 触发批量删除对话框
    const batchDelete = useCallback((items: DeleteTarget[]) => {
        setBatchDeleteTargets(items)
    }, [])

    // 确认删除
    const confirmDelete = useCallback(async () => {
        setIsDeleting(true)

        try {
            // 单个删除
            if (deleteTarget) {
                if (deleteTarget.type === 'folder') {
                    await knowledgeService.deleteFolder(deleteTarget.id)
                } else {
                    await knowledgeService.deleteFile(deleteTarget.id)
                }
                setDeleteTarget(null)
                onSuccess?.()
                return
            }

            // 批量删除
            if (batchDeleteTargets.length > 0) {
                const fileIds = batchDeleteTargets
                    .filter((item) => item.type === 'file')
                    .map((item) => item.id)

                const folderIds = batchDeleteTargets
                    .filter((item) => item.type === 'folder')
                    .map((item) => item.id)

                // 批量删除文件
                if (fileIds.length > 0) {
                    await knowledgeService.batchDeleteFiles(fileIds)
                }

                // 批量删除文件夹
                if (folderIds.length > 0) {
                    await knowledgeService.batchDeleteFolders(folderIds)
                }

                setBatchDeleteTargets([])
                onSuccess?.()
            }
        } catch (e) {
            console.error('删除失败:', e)
            toast({
                type: 'error',
                title: '删除失败',
                description: e instanceof Error ? e.message : '请稍后重试',
            })
        } finally {
            setIsDeleting(false)
        }
    }, [deleteTarget, batchDeleteTargets, onSuccess, toast])

    // 取消删除
    const cancelDelete = useCallback(() => {
        setDeleteTarget(null)
        setBatchDeleteTargets([])
    }, [])

    // 重命名
    const rename = useCallback(
        async (id: string, type: 'file' | 'folder', newName: string) => {
            try {
                if (type === 'folder') {
                    await knowledgeService.renameFolder(id, newName)
                } else {
                    await knowledgeService.renameFile(id, newName)
                }
                onSuccess?.()
            } catch (e) {
                console.error('重命名失败:', e)
                toast({
                    type: 'error',
                    title: '重命名失败',
                    description: e instanceof Error ? e.message : '请稍后重试',
                })
            }
        },
        [onSuccess, toast]
    )

    // 移动
    const move = useCallback(
        async (sourceId: string, type: 'file' | 'folder', targetFolderId: string | null) => {
            try {
                if (type === 'folder') {
                    await knowledgeService.moveFolder(sourceId, targetFolderId)
                } else {
                    await knowledgeService.moveFile(sourceId, targetFolderId)
                }
                onSuccess?.()
            } catch (e) {
                console.error('移动失败:', e)
                toast({
                    type: 'error',
                    title: '移动失败',
                    description: e instanceof Error ? e.message : '请稍后重试',
                })
            }
        },
        [onSuccess, toast]
    )

    return {
        deleteState: {
            target: deleteTarget,
            batchTargets: batchDeleteTargets,
            isDeleting,
        },
        handlers: {
            createFolder,
            deleteFolder,
            deleteFile,
            batchDelete,
            confirmDelete,
            cancelDelete,
            rename,
            move,
        },
    }
}
