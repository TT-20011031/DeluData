
import React, { useState, useRef, useEffect, useMemo } from 'react'
import {
    ChevronRight,
    ChevronDown,
    Folder,
    Trash2,
    FolderPlus,
    CheckCircle2,
    XCircle,
    Loader2,
    Pencil,
    FilePlus,
    CheckSquare,
    Square,
    Info
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
    ContextMenu,
    ContextMenuContent,
    ContextMenuItem,
    ContextMenuTrigger,
    ContextMenuSeparator,
} from "@/components/ui/context-menu"
import { FileTypeIcon } from './FileTypeIcons'

export interface FileNode {
    id: string
    name: string
    type: 'folder' | 'file'
    children?: FileNode[]
    status?: string
    description?: string
    file_type?: string
    pageindex_status?: string | null
    has_children?: boolean
}

interface FileExplorerProps {
    data: FileNode[]
    onFileSelect: (file: FileNode) => void
    onCreateFolder: (name: string, parentId?: string) => void
    onDeleteFolder: (id: string) => void
    onDeleteFile: (id: string) => void
    onRename: (id: string, type: 'file' | 'folder', newName: string) => void
    onUpload: (folderId?: string) => void
    onMove: (sourceId: string, type: 'file' | 'folder', targetFolderId: string | null) => void
    onBatchDelete?: (ids: { id: string, type: 'file' | 'folder' }[]) => void
    onShowOrigin?: (fileId: string) => void
    uploadingIds?: Set<string>
    onLoadChildren?: (folderId: string) => Promise<FileNode[]>  // [新增] 正在上传的文件 ID 集合
}

const StatusIcon = ({ status }: { status?: string }) => {
    if (!status) return null
    switch (status) {
        case 'processing': return <Loader2 size={14} className="animate-spin text-blue-400" />
        case 'indexed': return <CheckCircle2 size={14} className="text-green-500" />
        case 'error': return <XCircle size={14} className="text-red-500" />
        default: return null
    }
}


const FileItem = ({ node, level, onSelect, onUpload, onDeleteFolder, onDeleteFile, onRename, onCreateFolder, onMove, selectionMode, selectedItems, onToggleSelect, onShowOrigin, uploadingIds, onLoadChildren }: {
    node: FileNode,
    level: number,
    onSelect: any,
    onUpload: any,
    onDeleteFolder: any,
    onDeleteFile: any,
    onRename: any,
    onCreateFolder: any,
    onMove: any,
    selectionMode: boolean,
    selectedItems: Map<string, 'file' | 'folder'>,
    onToggleSelect: (id: string, type: 'file' | 'folder') => void,
    onShowOrigin?: (fileId: string) => void,
    uploadingIds?: Set<string>,
    onLoadChildren?: (folderId: string) => Promise<FileNode[]>  // [新增] 上传中的文件 ID
}) => {
    const [isOpen, setIsOpen] = useState(false)
    const [isRenaming, setIsRenaming] = useState(false)
    const [renameValue, setRenameValue] = useState(node.name)
    const inputRef = useRef<HTMLInputElement>(null)
    const isFolder = node.type === 'folder'

    // 懒加载子节点状态
    const [localChildren, setLocalChildren] = useState<FileNode[] | undefined>(node.children)
    useEffect(() => { if (node.children !== undefined) setLocalChildren(node.children) }, [node.children])

    const handleToggleOpen = async () => {
        if (!isFolder) return
        if (!isOpen) {
            if (!localChildren && onLoadChildren) {
                try {
                    const loaded = await onLoadChildren(node.id)
                    setLocalChildren(loaded)
                } catch (err) {
                    console.error('加载子节点失败', err)
                }
            }
            setIsOpen(true)
        } else {
            setIsOpen(false)
        }
    }

    const handleDragStart = (e: React.DragEvent) => {
        e.dataTransfer.setData('nodeId', node.id)
        e.dataTransfer.setData('type', node.type)
        e.stopPropagation()
    }

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        // Highlight folder if needed
    }

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        const sourceId = e.dataTransfer.getData('nodeId')
        const sourceType = e.dataTransfer.getData('type')

        // Prevent dropping onto self
        if (sourceId === node.id) return

        // Only allow dropping into folders
        if (isFolder) {
            onMove(sourceId, sourceType, node.id)
            setIsOpen(true) // Open folder on drop
        }
    }

    useEffect(() => {
        if (isRenaming && inputRef.current) {
            inputRef.current.focus()
            inputRef.current.select()
        }
    }, [isRenaming])

    // ... (existing handlers)

    const handleRenameSubmit = () => {
        if (renameValue.trim() && renameValue !== node.name) {
            onRename(node.id, node.type, renameValue)
        }
        setIsRenaming(false)
    }

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') handleRenameSubmit()
        if (e.key === 'Escape') {
            setRenameValue(node.name)
            setIsRenaming(false)
        }
    }

    return (
        <div className="select-none">
            <ContextMenu>
                <ContextMenuTrigger>
                    <div
                        draggable={!selectionMode}
                        onDragStart={selectionMode ? undefined : handleDragStart}
                        onDragOver={isFolder && !selectionMode ? handleDragOver : undefined}
                        onDrop={isFolder && !selectionMode ? handleDrop : undefined}
                        className={cn(
                            "flex items-center gap-3 py-2.5 px-3 hover:bg-manus-tertiary/80 rounded-lg cursor-pointer text-sm transition-all duration-150 group border border-transparent hover:border-manus-border/50",
                            level > 0 && "ml-5"
                        )}
                        onClick={() => {
                            if (selectionMode) {
                                onToggleSelect(node.id, node.type)
                            } else {
                                isFolder ? handleToggleOpen() : onSelect(node)
                            }
                        }}
                    >
                        {/* Selection checkbox */}
                        {selectionMode && (
                            <span className="text-accent flex-shrink-0" onClick={(e) => { e.stopPropagation(); onToggleSelect(node.id, node.type) }}>
                                {selectedItems.has(node.id) ? <CheckSquare size={18} /> : <Square size={18} className="text-manus-subtle" />}
                            </span>
                        )}
                        {/* ... content ... */}
                        <span className="text-manus-subtle w-5 flex justify-center flex-shrink-0">
                            {isFolder && (
                                isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />
                            )}
                        </span>

                        {/* 文件/文件夹图标 */}
                        <span className="flex-shrink-0">
                            {isFolder ? (
                                <Folder size={20} className="text-accent/90 group-hover:text-accent transition-colors" />
                            ) : (
                                <FileTypeIcon type={node.file_type} size={20} />
                            )}
                        </span>

                        {isRenaming ? (
                            <input
                                ref={inputRef}
                                type="text"
                                value={renameValue}
                                onChange={(e) => setRenameValue(e.target.value)}
                                onBlur={handleRenameSubmit}
                                onKeyDown={handleKeyDown}
                                onClick={(e) => e.stopPropagation()}
                                className="flex-1 bg-manus-primary border border-manus-border rounded px-1 text-sm h-6 focus:outline-none focus:border-accent"
                            />
                        ) : (
                            <span className={cn(
                                "flex-1 truncate",
                                !isFolder && node.status === 'processing' && "text-manus-subtle italic"
                            )}>
                                {node.name}
                            </span>
                        )}

                        {/* PageIndex DEEP 标记 - Shadcn 黑白极简风格 */}
                        {!isFolder && node.pageindex_status === 'ready' && (
                            <span className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-bold tracking-wider bg-zinc-900 text-zinc-50 border border-zinc-800 shadow-sm">
                                DEEP
                            </span>
                        )}
                        {!isFolder && node.pageindex_status === 'building' && (
                            <span className="inline-flex items-center gap-0.5 text-amber-400 text-[10px]">
                                <Loader2 size={10} className="animate-spin" /> 索引中
                            </span>
                        )}

                        {!isFolder && (
                            <div className="px-1">
                                {uploadingIds?.has(node.id)
                                    ? <Loader2 size={14} className="animate-spin text-blue-400" />
                                    : <StatusIcon status={node.status} />}
                            </div>
                        )}
                    </div>
                </ContextMenuTrigger>
                {/* ... ContextMenuContent ... */}
                <ContextMenuContent className="w-48 bg-manus-secondary border-manus-border text-manus-text">
                    {isFolder && (
                        <>
                            <ContextMenuItem onClick={() => onCreateFolder("新建文件夹", node.id)}>
                                <FolderPlus className="mr-2 h-4 w-4" /> 新建子文件夹
                            </ContextMenuItem>
                            <ContextMenuItem onClick={() => onUpload(node.id)}>
                                <FilePlus className="mr-2 h-4 w-4" /> 上传文件
                            </ContextMenuItem>
                            <ContextMenuSeparator className="bg-manus-border" />
                        </>
                    )}

                    <ContextMenuItem onClick={() => setIsRenaming(true)}>
                        <Pencil className="mr-2 h-4 w-4" /> 重命名
                    </ContextMenuItem>

                    {/* 溯源信息 - 仅对文件显示 */}
                    {!isFolder && onShowOrigin && (
                        <ContextMenuItem onClick={() => onShowOrigin(node.id)}>
                            <Info className="mr-2 h-4 w-4" /> 溯源信息
                        </ContextMenuItem>
                    )}

                    <ContextMenuItem
                        onClick={() => isFolder ? onDeleteFolder(node.id) : onDeleteFile(node.id)}
                        className="text-red-400 focus:text-red-400"
                    >
                        <Trash2 className="mr-2 h-4 w-4" /> 删除
                    </ContextMenuItem>
                </ContextMenuContent>
            </ContextMenu>

            {isOpen && localChildren && (
                <div className="border-l border-manus-border ml-3 pl-1">
                    {localChildren.map(child => (
                        <FileItem
                            key={child.id}
                            node={child}
                            level={level + 1}
                            onSelect={onSelect}
                            onUpload={onUpload}
                            onDeleteFolder={onDeleteFolder}
                            onDeleteFile={onDeleteFile}
                            onRename={onRename}
                            onCreateFolder={onCreateFolder}
                            onMove={onMove}
                            selectionMode={selectionMode}
                            selectedItems={selectedItems}
                            onToggleSelect={onToggleSelect}
                            onShowOrigin={onShowOrigin}
                            uploadingIds={uploadingIds}
                            onLoadChildren={onLoadChildren}
                        />
                    ))}
                </div>
            )}
        </div>
    )
}

export const FileExplorer: React.FC<FileExplorerProps> = ({
    data,
    onFileSelect,
    onCreateFolder,
    onDeleteFolder,
    onDeleteFile,
    onRename,
    onUpload,
    onMove,
    onBatchDelete,
    onShowOrigin,
    uploadingIds,
    onLoadChildren
}) => {
    const [selectionMode, setSelectionMode] = useState(false)
    const [selectedItems, setSelectedItems] = useState<Map<string, 'file' | 'folder'>>(new Map())

    const toggleSelect = (id: string, type: 'file' | 'folder') => {
        setSelectedItems(prev => {
            const next = new Map(prev)
            if (next.has(id)) {
                next.delete(id)
            } else {
                next.set(id, type)
            }
            return next
        })
    }

    const allSelectableItems = useMemo(() => {
        const flatten = (nodes: FileNode[]): Array<{ id: string; type: 'file' | 'folder' }> => {
            const items: Array<{ id: string; type: 'file' | 'folder' }> = []
            for (const node of nodes) {
                items.push({ id: node.id, type: node.type })
                if (node.children && node.children.length > 0) {
                    items.push(...flatten(node.children))
                }
            }
            return items
        }
        return flatten(data)
    }, [data])

    const isAllSelected = useMemo(() => {
        if (allSelectableItems.length === 0) return false
        return allSelectableItems.every((item) => selectedItems.has(item.id))
    }, [allSelectableItems, selectedItems])

    const toggleSelectAll = () => {
        if (isAllSelected) {
            setSelectedItems(new Map())
            return
        }
        const next = new Map<string, 'file' | 'folder'>()
        allSelectableItems.forEach((item) => next.set(item.id, item.type))
        setSelectedItems(next)
    }

    const handleBatchDelete = () => {
        if (onBatchDelete && selectedItems.size > 0) {
            const items = Array.from(selectedItems.entries()).map(([id, type]) => ({ id, type }))
            onBatchDelete(items)
            setSelectedItems(new Map())
            setSelectionMode(false)
        }
    }

    const exitSelectionMode = () => {
        setSelectionMode(false)
        setSelectedItems(new Map())
    }
    const handleRootDrop = (e: React.DragEvent) => {
        e.preventDefault()
        const sourceId = e.dataTransfer.getData('nodeId')
        const sourceType = e.dataTransfer.getData('type')
        // Move to root (null parent)
        if (sourceId) onMove(sourceId, sourceType as any, null)
    }

    return (
        <div
            className="text-manus-text h-full flex flex-col"
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleRootDrop}
        >
            <div className="flex items-center justify-between mb-2 px-2 py-2 border-b border-manus-border bg-manus-secondary sticky top-0 z-10">
                <span className="font-semibold text-sm">
                    {selectionMode ? `已选择 ${selectedItems.size} 项` : '资源管理'}
                </span>
                <div className="flex gap-1">
                    {selectionMode ? (
                        <>
                            <button
                                onClick={toggleSelectAll}
                                disabled={allSelectableItems.length === 0}
                                className={cn(
                                    "p-1.5 rounded-md transition-colors",
                                    allSelectableItems.length > 0
                                        ? "text-manus-subtle hover:bg-manus-tertiary hover:text-manus-text"
                                        : "text-manus-subtle opacity-50 cursor-not-allowed"
                                )}
                                title={isAllSelected ? "取消全选" : "全选"}
                            >
                                {isAllSelected ? <Square size={16} /> : <CheckSquare size={16} />}
                            </button>
                            <button
                                onClick={handleBatchDelete}
                                disabled={selectedItems.size === 0}
                                className={cn(
                                    "p-1.5 rounded-md transition-colors",
                                    selectedItems.size > 0
                                        ? "bg-red-500/20 text-red-400 hover:bg-red-500/30"
                                        : "text-manus-subtle opacity-50 cursor-not-allowed"
                                )}
                                title="删除选中"
                            >
                                <Trash2 size={16} />
                            </button>
                            <button
                                onClick={exitSelectionMode}
                                className="p-1.5 hover:bg-manus-tertiary rounded-md text-manus-subtle hover:text-manus-text transition-colors"
                                title="取消选择"
                            >
                                <XCircle size={16} />
                            </button>
                        </>
                    ) : (
                        <>
                            {onBatchDelete && (
                                <button
                                    onClick={() => setSelectionMode(true)}
                                    className="p-1.5 hover:bg-manus-tertiary rounded-md text-manus-subtle hover:text-manus-text transition-colors"
                                    title="批量选择"
                                >
                                    <CheckSquare size={16} />
                                </button>
                            )}
                            <button
                                onClick={() => onCreateFolder("新建文件夹")}
                                className="p-1.5 hover:bg-manus-tertiary rounded-md text-manus-subtle hover:text-manus-text transition-colors"
                                title="新建根文件夹"
                            >
                                <FolderPlus size={16} />
                            </button>
                            <button
                                onClick={() => onUpload(undefined)}
                                className="p-1.5 hover:bg-manus-tertiary rounded-md text-manus-subtle hover:text-manus-text transition-colors"
                                title="上传到根目录"
                            >
                                <FilePlus size={16} />
                            </button>
                        </>
                    )}
                </div>
            </div>

            <div className="space-y-1 flex-1 overflow-y-auto overflow-x-hidden pb-10 min-h-[580px] px-1">
                {data.length === 0 ? (
                    <div className="p-4 text-center text-xs text-manus-subtle italic">
                        暂无文件，点击上方按钮创建或上传
                    </div>
                ) : (
                    data.map(node => (
                        <FileItem
                            key={node.id}
                            node={node}
                            level={0}
                            onSelect={onFileSelect}
                            onUpload={onUpload}
                            onDeleteFolder={onDeleteFolder}
                            onDeleteFile={onDeleteFile}
                            onRename={onRename}
                            onCreateFolder={onCreateFolder}
                            onMove={onMove}
                            selectionMode={selectionMode}
                            selectedItems={selectedItems}
                            onToggleSelect={toggleSelect}
                            onShowOrigin={onShowOrigin}
                            uploadingIds={uploadingIds}
                            onLoadChildren={onLoadChildren}
                        />
                    ))
                )}
            </div>
        </div>
    )
}
