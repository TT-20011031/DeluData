/**
 * 文件列表视图容器
 * 
 * 封装 FileExplorer 和 FilePreview 的布局
 */
import { ArrowLeft, Upload, LayoutGrid, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { FileExplorer } from '@/components/knowledge/FileExplorer'
import { FilePreview } from '@/components/knowledge/FilePreview'
import { knowledgeService } from '@/services/knowledgeService'
import type { FolderNode, PreviewFile, DeleteTarget } from '@/types/knowledge'

export interface FileOperationHandlers {
    createFolder: (name: string, parentId?: string) => Promise<void>
    deleteFolder: (id: string) => void
    deleteFile: (id: string) => void
    batchDelete: (items: DeleteTarget[]) => void
    rename: (id: string, type: 'file' | 'folder', newName: string) => Promise<void>
    move: (sourceId: string, type: 'file' | 'folder', targetFolderId: string | null) => Promise<void>
}

export interface RepositoryViewProps {
    currentScope: string
    folderStructure: FolderNode[]
    isLoading: boolean
    selectedPreviewFile: PreviewFile | null
    onFileSelect: (file: PreviewFile | null) => void
    onBack: () => void
    onUpload: (folderId?: string) => void
    fileOperations: FileOperationHandlers
    onShowOrigin: (fileId: string) => void
}

/**
 * 获取 scope 的显示名称
 */
function getScopeName(scope: string): string {
    if (scope === 'public') return '全局共享'
    if (scope === 'private') return '我的文档'
    if (scope.startsWith('dept_')) return '部门文档'
    return scope
}

export function RepositoryView({
    currentScope,
    folderStructure,
    isLoading,
    selectedPreviewFile,
    onFileSelect,
    onBack,
    onUpload,
    fileOperations,
    onShowOrigin,
}: RepositoryViewProps) {
    return (
        <div className="h-full flex flex-col">
            {/* 返回按钮和面包屑 */}
            <div className="px-4 py-3 border-b border-manus-border bg-manus-secondary flex items-center gap-3">
                <Button
                    variant="ghost"
                    size="sm"
                    onClick={onBack}
                    className="text-manus-subtle hover:text-manus-text hover:bg-manus-border/30"
                >
                    <ArrowLeft className="h-4 w-4 mr-1" />
                    返回概览
                </Button>
                <span className="text-manus-border">|</span>
                <span className="text-sm text-manus-text flex-1">{getScopeName(currentScope)}</span>
                {/* 上传按钮 */}
                <Button
                    variant="default"
                    size="sm"
                    onClick={() => onUpload()}
                    className="gap-1"
                >
                    <Upload className="h-4 w-4" />
                    上传
                </Button>
            </div>

            {/* 文件浏览区 */}
            <div className="flex-1 flex overflow-hidden">
                {/* 左侧文件树 */}
                <div className="w-80 border-r border-manus-border bg-manus-secondary h-full overflow-y-auto p-4 flex-shrink-0">
                    {isLoading ? (
                        <div className="flex items-center justify-center py-16">
                            <Loader2 className="h-6 w-6 animate-spin text-accent" />
                        </div>
                    ) : (
                        <FileExplorer
                            data={folderStructure}
                            onFileSelect={(node) => {
                                if (node.type === 'file') {
                                    onFileSelect({
                                        id: node.id,
                                        name: node.name,
                                        type: node.file_type || 'txt',
                                    })
                                }
                            }}
                            onCreateFolder={(name, parentId) =>
                                fileOperations.createFolder(name, parentId)
                            }
                            onDeleteFolder={fileOperations.deleteFolder}
                            onDeleteFile={fileOperations.deleteFile}
                            onRename={fileOperations.rename}
                            onUpload={onUpload}
                            onMove={fileOperations.move}
                            onBatchDelete={fileOperations.batchDelete}
                            onShowOrigin={onShowOrigin}
                            onLoadChildren={(folderId) => knowledgeService.fetchFolderChildren(folderId, currentScope)}
                        />
                    )}
                </div>

                {/* 右侧预览区 */}
                <div className="flex-1 bg-manus p-4 overflow-hidden">
                    {selectedPreviewFile ? (
                        <FilePreview
                            fileId={selectedPreviewFile.id}
                            fileName={selectedPreviewFile.name}
                            fileType={selectedPreviewFile.type}
                        />
                    ) : (
                        <div className="h-full flex flex-col items-center justify-center text-manus-subtle">
                            <LayoutGrid className="h-16 w-16 mx-auto mb-4 opacity-20" />
                            <p>选择左侧文件查看详情或预览</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
