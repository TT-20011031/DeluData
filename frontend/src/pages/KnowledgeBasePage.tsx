/**
 * 知识库页面 (重构版 - 模块化架构)
 *
 * 采用 "Service - Hook - Component" 分层架构
 * 主文件仅负责组装和路由，业务逻辑下沉到 Hooks
 */
import { useState, useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useToast } from '@/components/ui/toast'
import { useAuthStore } from '@/stores/authStore'

// Hooks
import {
    useKnowledgeData,
    useFileUpload,
    useFileOperations,
} from '@/hooks/knowledge'

// Components
import {
    KnowledgeHeader,
    KnowledgeOverview,
    RepositoryView,
    UploadDialog,
    DeleteConfirmDialog,
    OriginInfoDialog,
} from '@/components/knowledge'
import { WikiGraphView, WikiView } from '@/components/wiki'

// Services
import { knowledgeService } from '@/services/knowledgeService'

// Types
import type {
    PreviewFile,
    OriginInfo,
    KnowledgePermissionSet,
} from '@/types/knowledge'

export default function KnowledgeBasePage() {
    // =========================================================================
    // 路由状态
    // =========================================================================
    const [searchParams, setSearchParams] = useSearchParams()
    const currentScope = searchParams.get('scope') || 'overview'
    const urlView = searchParams.get('view')
    const viewMode: 'repository' | 'graph' | 'wiki' =
        urlView === 'graph' || urlView === 'wiki' ? urlView : 'repository'
    const selectedWikiSlug = searchParams.get('wiki_slug')
    const user = useAuthStore((state) => state.user)

    const { toast } = useToast()

    // =========================================================================
    // 数据 Hook
    // =========================================================================
    const knowledgeData = useKnowledgeData({ viewMode, currentScope })

    // =========================================================================
    // 业务 Hooks
    // =========================================================================
    const fileOps = useFileOperations({
        currentScope,
        onSuccess: () => knowledgeData.refresh(false),
    })

    // =========================================================================
    // 预览状态（简单状态保留在页面）
    // =========================================================================
    const [selectedPreviewFile, setSelectedPreviewFile] = useState<PreviewFile | null>(null)

    // =========================================================================
    // 溯源信息状态
    // =========================================================================
    const [showOriginDialog, setShowOriginDialog] = useState(false)
    const [originInfo, setOriginInfo] = useState<OriginInfo | null>(null)
    const [isLoadingOrigin, setIsLoadingOrigin] = useState(false)

    const workspaceFeatures = user?.workspace_features || {}
    const maxUploadFileSizeBytes = workspaceFeatures.knowledge_max_upload_file_size_bytes ?? null
    const knowledgePermissions: KnowledgePermissionSet = {
        canUpload: workspaceFeatures.knowledge_upload_enabled !== false,
        canDelete: workspaceFeatures.knowledge_delete_enabled !== false,
        canRename: workspaceFeatures.knowledge_rename_enabled !== false,
        canMove: workspaceFeatures.knowledge_move_enabled !== false,
        canCreateFolder: workspaceFeatures.knowledge_create_folder_enabled !== false,
    }

    const fileUpload = useFileUpload({
        onSuccess: () => knowledgeData.refresh(false),
        maxUploadFileSizeBytes,
    })

    const notifyPermissionDenied = useCallback(
        (message: string) => {
            toast({
                type: 'warning',
                title: '当前租户无此权限',
                description: message,
                duration: 4000,
            })
        },
        [toast]
    )

    const handleUpload = useCallback(
        (folderId?: string) => {
            if (!knowledgePermissions.canUpload) {
                notifyPermissionDenied('如需开放上传，请在平台端知识库治理页调整权限。')
                return
            }
            fileUpload.actions.openDialog(folderId)
        },
        [fileUpload.actions, knowledgePermissions.canUpload, notifyPermissionDenied]
    )

    const guardedFileOperations = useMemo(() => ({
        createFolder: async (name: string, parentId?: string) => {
            if (!knowledgePermissions.canCreateFolder) {
                notifyPermissionDenied('当前租户不允许新建知识库文件夹。')
                return
            }
            await fileOps.handlers.createFolder(name, parentId)
        },
        deleteFolder: (id: string) => {
            if (!knowledgePermissions.canDelete) {
                notifyPermissionDenied('当前租户不允许删除知识库内容。')
                return
            }
            fileOps.handlers.deleteFolder(id)
        },
        deleteFile: (id: string) => {
            if (!knowledgePermissions.canDelete) {
                notifyPermissionDenied('当前租户不允许删除知识库内容。')
                return
            }
            fileOps.handlers.deleteFile(id)
        },
        batchDelete: (items: { id: string; type: 'file' | 'folder' }[]) => {
            if (!knowledgePermissions.canDelete) {
                notifyPermissionDenied('当前租户不允许删除知识库内容。')
                return
            }
            fileOps.handlers.batchDelete(items)
        },
        rename: async (id: string, type: 'file' | 'folder', newName: string) => {
            if (!knowledgePermissions.canRename) {
                notifyPermissionDenied('当前租户不允许重命名知识库内容。')
                return
            }
            await fileOps.handlers.rename(id, type, newName)
        },
        move: async (sourceId: string, type: 'file' | 'folder', targetFolderId: string | null) => {
            if (!knowledgePermissions.canMove) {
                notifyPermissionDenied('当前租户不允许移动知识库内容。')
                return
            }
            await fileOps.handlers.move(sourceId, type, targetFolderId)
        },
    }), [
        fileOps.handlers,
        knowledgePermissions.canCreateFolder,
        knowledgePermissions.canDelete,
        knowledgePermissions.canMove,
        knowledgePermissions.canRename,
        notifyPermissionDenied,
    ])

    const handleShowOrigin = useCallback(async (fileId: string) => {
        setIsLoadingOrigin(true)
        setShowOriginDialog(true)
        try {
            const data = await knowledgeService.fetchOriginInfo(fileId)
            setOriginInfo(data)
        } catch (e) {
            console.error(e)
        } finally {
            setIsLoadingOrigin(false)
        }
    }, [])

    const handleViewModeChange = useCallback((mode: 'repository' | 'graph' | 'wiki') => {
        const next = new URLSearchParams(searchParams)
        if (mode === 'repository') {
            next.delete('view')
            next.delete('wiki_slug')
        } else {
            next.set('view', mode)
            if (mode !== 'wiki') {
                next.delete('wiki_slug')
            }
        }
        setSearchParams(next)
    }, [searchParams, setSearchParams])

    const handleOpenWikiFromGraph = useCallback((slug: string) => {
        const next = new URLSearchParams(searchParams)
        next.set('view', 'wiki')
        next.set('wiki_slug', slug)
        setSearchParams(next)
    }, [searchParams, setSearchParams])

    // =========================================================================
    // 文件验证回调（给 UploadDialog 使用）
    // =========================================================================
    const handleFilesValidated = useCallback(
        (_valid: File[], invalid: File[]) => {
            if (invalid.length > 0) {
                toast({
                    type: 'warning',
                    title: `${invalid.length} 个文件类型不支持`,
                    description: `已过滤: ${invalid
                        .map((f) => f.name)
                        .slice(0, 3)
                        .join(', ')}${invalid.length > 3 ? '...' : ''}\n\n支持格式: PDF, Word, Markdown, TXT`,
                    duration: 6000,
                })
            }
        },
        [toast]
    )

    // =========================================================================
    // 渲染
    // =========================================================================
    return (
        <div className="flex h-full flex-col bg-manus text-manus-text">
            {/* Header */}
            <KnowledgeHeader
                viewMode={viewMode}
                onViewModeChange={handleViewModeChange}
                isLoading={knowledgeData.isLoading}
                isRefreshing={knowledgeData.isRefreshing}
                onRefresh={() => knowledgeData.refresh(true)}
            />

            {/* Main Content */}
            <main className="flex-1 overflow-hidden relative">
                {viewMode === 'repository' ? (
                    currentScope === 'overview' ? (
                        <KnowledgeOverview
                            statsData={knowledgeData.statsData}
                            isLoading={knowledgeData.isLoading}
                            onScopeChange={(scope) => setSearchParams({ scope })}
                            onUpload={() => handleUpload()}
                        />
                    ) : (
                        <RepositoryView
                            currentScope={currentScope}
                            folderStructure={knowledgeData.folderStructure}
                            isLoading={knowledgeData.isLoading}
                            selectedPreviewFile={selectedPreviewFile}
                            onFileSelect={setSelectedPreviewFile}
                            onBack={() => {
                                searchParams.delete('scope')
                                setSearchParams(searchParams)
                            }}
                            onUpload={handleUpload}
                            fileOperations={guardedFileOperations}
                            onShowOrigin={handleShowOrigin}
                        />
                    )
                ) : viewMode === 'graph' ? (
                    <div className="w-full h-full bg-white">
                        <WikiGraphView onOpenWikiPage={handleOpenWikiFromGraph} />
                    </div>
                ) : (
                    <WikiView selectedSlugFromUrl={selectedWikiSlug} />
                )}
            </main>

            {/* ================================================================= */}
            {/* Dialogs */}
            {/* ================================================================= */}

            {/* Upload Dialog */}
            <UploadDialog
                state={fileUpload.state}
                actions={fileUpload.actions}
                departments={knowledgeData.departments}
                currentUserRole={knowledgeData.currentUserRole}
                currentUserDeptId={knowledgeData.currentUserDeptId}
                maxUploadFileSizeBytes={maxUploadFileSizeBytes}
                onFilesValidated={handleFilesValidated}
            />

            {/* Delete Confirm Dialog */}
            <DeleteConfirmDialog
                state={fileOps.deleteState}
                onConfirm={fileOps.handlers.confirmDelete}
                onCancel={fileOps.handlers.cancelDelete}
            />

            {/* Origin Info Dialog */}
            <OriginInfoDialog
                open={showOriginDialog}
                onOpenChange={setShowOriginDialog}
                isLoading={isLoadingOrigin}
                originInfo={originInfo}
            />

        </div>
    )
}
