/**
 * 文件上传对话框
 */
import { useRef } from 'react'
import { FileText, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '@/components/ui/dialog'
import { validateFiles } from '@/hooks/knowledge'
import type { UploadState, UploadActions } from '@/hooks/knowledge'
import type { DepartmentOption, VisibilityType } from '@/types/knowledge'

export interface UploadDialogProps {
    state: UploadState
    actions: UploadActions
    departments: DepartmentOption[]
    currentUserRole: string
    currentUserDeptId: number | null
    maxUploadFileSizeBytes?: number | null
    onFilesValidated?: (valid: File[], invalid: File[]) => void
}

export function UploadDialog({
    state,
    actions,
    departments,
    currentUserRole,
    currentUserDeptId,
    maxUploadFileSizeBytes,
    onFilesValidated,
}: UploadDialogProps) {
    const fileInputRef = useRef<HTMLInputElement>(null)

    const formatBytes = (bytes: number) => {
        if (bytes <= 0) return '0 B'
        const units = ['B', 'KB', 'MB', 'GB', 'TB']
        let value = bytes
        let unitIndex = 0
        while (value >= 1024 && unitIndex < units.length - 1) {
            value /= 1024
            unitIndex += 1
        }
        return unitIndex === 0 ? `${Math.round(value)} ${units[unitIndex]}` : `${value.toFixed(1)} ${units[unitIndex]}`
    }

    const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files || [])
        if (files.length > 0) {
            const { valid, invalid } = validateFiles(files)
            onFilesValidated?.(valid, invalid)
            if (valid.length > 0) {
                actions.addFiles(valid)
            }
        }
        e.target.value = ''
    }

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault()
        e.currentTarget.classList.remove('border-accent', 'bg-accent/10')
        const files = Array.from(e.dataTransfer.files)
        if (files.length > 0) {
            const { valid, invalid } = validateFiles(files)
            onFilesValidated?.(valid, invalid)
            if (valid.length > 0) {
                actions.selectFiles(valid)
            }
        }
    }

    return (
        <>
            {/* Hidden Input */}
            <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                accept=".pdf,.doc,.docx,.md,.txt"
                multiple
                onChange={handleFileInputChange}
            />

            <Dialog open={state.showDialog} onOpenChange={(open) => !open && actions.closeDialog()}>
                <DialogContent className="bg-manus-secondary border-manus-border text-manus-text max-w-md w-full overflow-hidden">
                    <DialogHeader>
                        <DialogTitle>上传文档</DialogTitle>
                        <DialogDescription>
                            {state.selectedFiles.length > 0
                                ? `已选择 ${state.selectedFiles.length} 个文件`
                                : '点击或拖拽文件到下方区域上传'}
                            {maxUploadFileSizeBytes ? `，单文件上限 ${formatBytes(maxUploadFileSizeBytes)}` : ''}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-4 w-full overflow-hidden">
                        {/* 拖拽上传区域 */}
                        {state.selectedFiles.length === 0 ? (
                            <div
                                className="border-2 border-dashed border-manus-border rounded-lg p-8 text-center cursor-pointer hover:border-accent/50 hover:bg-accent/5 transition-colors"
                                onClick={() => fileInputRef.current?.click()}
                                onDragOver={(e) => {
                                    e.preventDefault()
                                    e.currentTarget.classList.add('border-accent', 'bg-accent/10')
                                }}
                                onDragLeave={(e) => {
                                    e.preventDefault()
                                    e.currentTarget.classList.remove('border-accent', 'bg-accent/10')
                                }}
                                onDrop={handleDrop}
                            >
                                <FileText className="h-12 w-12 mx-auto mb-3 text-manus-subtle" />
                                <p className="text-manus-text font-medium mb-1">
                                    点击选择文件或拖拽到此处
                                </p>
                                <p className="text-sm text-manus-subtle">
                                    支持 PDF、Word、Markdown、TXT 格式
                                </p>
                                {maxUploadFileSizeBytes ? (
                                    <p className="text-xs text-manus-subtle mt-2">
                                        单文件上传上限：{formatBytes(maxUploadFileSizeBytes)}
                                    </p>
                                ) : null}
                            </div>
                        ) : (
                            <>
                                {/* 单文件：显示文件名编辑和描述 */}
                                {state.selectedFiles.length === 1 ? (
                                    <div className="space-y-4">
                                        <div className="space-y-1">
                                            <label className="text-sm text-manus-subtle">文件名</label>
                                            <Input
                                                value={state.customFileName}
                                                onChange={(e) => actions.setCustomFileName(e.target.value)}
                                                placeholder={state.selectedFiles[0].name}
                                                title={state.customFileName || state.selectedFiles[0].name}
                                                className="bg-manus-tertiary border-manus-border text-manus-text w-full"
                                            />
                                            <p
                                                className="text-xs text-manus-subtle truncate"
                                                title={state.selectedFiles[0].name}
                                            >
                                                原始文件: {state.selectedFiles[0].name} (
                                                {(state.selectedFiles[0].size / 1024).toFixed(1)} KB)
                                            </p>
                                        </div>

                                        <div className="space-y-1">
                                            <label className="text-sm text-manus-subtle">文档描述</label>
                                            <Textarea
                                                placeholder="请输入文档描述..."
                                                value={state.docDescription}
                                                onChange={(e) => actions.setDescription(e.target.value)}
                                                className="bg-manus-tertiary border-manus-border text-manus-text"
                                            />
                                        </div>
                                    </div>
                                ) : (
                                    /* 批量上传：只显示文件列表 */
                                    <div className="space-y-3">
                                        <div className="max-h-40 overflow-y-auto space-y-2">
                                            {state.selectedFiles.map((file, i) => (
                                                <div
                                                    key={i}
                                                    className="flex items-center gap-2 p-2 bg-manus-tertiary rounded-lg border border-manus-border min-w-0 overflow-hidden"
                                                >
                                                    <FileText className="h-4 w-4 text-accent shrink-0" />
                                                    <span className="text-sm truncate text-manus-text flex-1 min-w-0">
                                                        {file.name}
                                                    </span>
                                                    <span className="text-xs text-manus-subtle whitespace-nowrap shrink-0">
                                                        {(file.size / 1024).toFixed(1)} KB
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                        <p className="text-xs text-manus-subtle">
                                            批量上传 {state.selectedFiles.length} 个文件（不支持单独设置描述）
                                        </p>
                                    </div>
                                )}

                                {/* 添加更多文件按钮 */}
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => fileInputRef.current?.click()}
                                    className="w-full border-manus-border text-manus-muted hover:text-manus-text"
                                >
                                    + 添加更多文件
                                </Button>

                                {/* 上传进度 */}
                                {state.isUploading && (
                                    <div className="space-y-2">
                                        <div className="flex justify-between text-sm text-manus-muted">
                                            <span>上传进度</span>
                                            <span>{state.uploadProgress}%</span>
                                        </div>
                                        <div className="h-2 bg-manus-tertiary rounded-full overflow-hidden">
                                            <div
                                                className="h-full bg-accent transition-all duration-300"
                                                style={{ width: `${state.uploadProgress}%` }}
                                            />
                                        </div>
                                    </div>
                                )}
                            </>
                        )}

                        {/* 可见范围选择 */}
                        <div className="space-y-1">
                            <label className="text-sm text-manus-subtle">可见范围</label>
                            <select
                                className="w-full bg-manus-tertiary border border-manus-border rounded-md p-2 text-sm text-manus-text"
                                value={state.visibility}
                                onChange={(e) => actions.setVisibility(e.target.value as VisibilityType)}
                            >
                                {/* 管理员始终可选部门可见，普通用户需有部门 */}
                                <option value="dept" disabled={currentUserRole !== 'knowledge_manager' && !currentUserDeptId}>
                                    {currentUserRole === 'knowledge_manager' ? '指定部门可见' : (
                                        currentUserDeptId ? '本部门可见' : '本部门可见 (您尚未加入部门)'
                                    )}
                                </option>
                                <option value="public">全局共享</option>
                                <option value="private">仅自己可见</option>
                            </select>
                        </div>

                        {/* 管理员部门选择 - visibility 为 dept 时显示 */}
                        {currentUserRole === 'knowledge_manager' && state.visibility === 'dept' && (
                            <div className="space-y-1">
                                <label className="text-sm text-manus-subtle">
                                    归属部门 (仅管理员可选)
                                </label>
                                <select
                                    className="w-full bg-manus-tertiary border border-manus-border rounded-md p-2 text-sm text-manus-text"
                                    value={state.selectedDeptId}
                                    onChange={(e) => actions.setDeptId(e.target.value)}
                                >
                                    <option value="">默认 (当前部门)</option>
                                    {departments.map((dept) => (
                                        <option key={dept.id} value={dept.id}>
                                            {dept.name}
                                        </option>
                                    ))}
                                </select>
                            </div>
                        )}
                    </div>

                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={actions.closeDialog}
                            className="bg-transparent border-manus-border text-manus-text hover:bg-manus-tertiary"
                        >
                            取消
                        </Button>
                        <Button
                            onClick={() => actions.submit(currentUserRole)}
                            disabled={state.isUploading || state.selectedFiles.length === 0}
                            className="bg-accent hover:bg-accent/90 text-white"
                        >
                            {state.isUploading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            上传 {state.selectedFiles.length > 1 ? `(${state.selectedFiles.length}个)` : ''}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    )
}
