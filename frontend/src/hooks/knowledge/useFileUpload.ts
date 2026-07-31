/**
 * 文件上传管理 Hook
 * 
 * 职责：
 * - 管理上传状态机
 * - 文件选择与验证
 */
import { useState, useCallback } from 'react'
import { useToast } from '@/components/ui/toast'
import { useUploadStore } from '@/stores/uploadStore'
import type { VisibilityType } from '@/types/knowledge'

// 支持的文件扩展名
const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.md', '.txt']

// 并发限制 (已被 global store 接管，此常量不再作为主要控制点，但保留结构)

/**
 * 验证文件类型
 */
export function validateFiles(files: File[]): { valid: File[]; invalid: File[] } {
    const valid: File[] = []
    const invalid: File[] = []

    for (const file of files) {
        const ext = '.' + (file.name.split('.').pop()?.toLowerCase() || '')
        if (ALLOWED_EXTENSIONS.includes(ext)) {
            valid.push(file)
        } else {
            invalid.push(file)
        }
    }

    return { valid, invalid }
}

export interface UseFileUploadOptions {
    onSuccess?: () => void
    maxUploadFileSizeBytes?: number | null
}

export interface UploadState {
    selectedFiles: File[]
    isUploading: boolean
    uploadProgress: number
    showDialog: boolean
    docDescription: string
    customFileName: string
    targetFolderId?: string
    visibility: VisibilityType
    selectedDeptId: string
}

export interface UploadActions {
    openDialog: (folderId?: string) => void
    closeDialog: () => void
    selectFiles: (files: File[]) => void
    addFiles: (files: File[]) => void
    removeFile: (index: number) => void
    setDescription: (desc: string) => void
    setCustomFileName: (name: string) => void
    setVisibility: (v: VisibilityType) => void
    setDeptId: (id: string) => void
    submit: (currentUserRole: string) => Promise<void>
}

export interface UseFileUploadReturn {
    state: UploadState
    actions: UploadActions
}

export function useFileUpload(options: UseFileUploadOptions = {}): UseFileUploadReturn {
    const { onSuccess, maxUploadFileSizeBytes = null } = options
    const { toast } = useToast()

    // 上传状态
    const [selectedFiles, setSelectedFiles] = useState<File[]>([])
    const [isUploading, setIsUploading] = useState(false)
    const [uploadProgress, setUploadProgress] = useState(0)
    const [showDialog, setShowDialog] = useState(false)
    const [docDescription, setDocDescription] = useState('')
    const [customFileName, setCustomFileName] = useState('')
    const [targetFolderId, setTargetFolderId] = useState<string | undefined>(undefined)
    const [visibility, setVisibility] = useState<VisibilityType>('private')
    const [selectedDeptId, setSelectedDeptId] = useState('')

    const formatBytes = useCallback((bytes: number) => {
        if (bytes <= 0) return '0 B'
        const units = ['B', 'KB', 'MB', 'GB', 'TB']
        let value = bytes
        let unitIndex = 0
        while (value >= 1024 && unitIndex < units.length - 1) {
            value /= 1024
            unitIndex += 1
        }
        return unitIndex === 0 ? `${Math.round(value)} ${units[unitIndex]}` : `${value.toFixed(1)} ${units[unitIndex]}`
    }, [])

    const splitOversizedFiles = useCallback((files: File[]) => {
        if (!maxUploadFileSizeBytes || maxUploadFileSizeBytes <= 0) {
            return { allowed: files, oversized: [] as File[] }
        }

        const allowed: File[] = []
        const oversized: File[] = []

        for (const file of files) {
            if (file.size > maxUploadFileSizeBytes) {
                oversized.push(file)
            } else {
                allowed.push(file)
            }
        }

        return { allowed, oversized }
    }, [maxUploadFileSizeBytes])

    const notifyOversizedFiles = useCallback((oversized: File[]) => {
        if (oversized.length === 0 || !maxUploadFileSizeBytes || maxUploadFileSizeBytes <= 0) {
            return
        }

        toast({
            type: 'warning',
            title: `${oversized.length} 个文件超出大小限制`,
            description: `单文件上限 ${formatBytes(maxUploadFileSizeBytes)}，已过滤: ${oversized
                .map((file) => file.name)
                .slice(0, 3)
                .join(', ')}${oversized.length > 3 ? '...' : ''}`,
            duration: 6000,
        })
    }, [formatBytes, maxUploadFileSizeBytes, toast])

    // 打开上传对话框
    const openDialog = useCallback((folderId?: string) => {
        setTargetFolderId(folderId)
        setSelectedFiles([])
        setDocDescription('')
        setCustomFileName('')
        setShowDialog(true)
    }, [])

    // 关闭上传对话框
    const closeDialog = useCallback(() => {
        setShowDialog(false)
        setSelectedFiles([])
    }, [])

    // 选择文件（覆盖现有）
    const selectFiles = useCallback(
        (files: File[]) => {
            const { valid, invalid } = validateFiles(files)
            const { allowed, oversized } = splitOversizedFiles(valid)

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

            notifyOversizedFiles(oversized)

            if (allowed.length > 0) {
                setSelectedFiles(allowed)
                // 单文件时初始化自定义文件名
                if (allowed.length === 1) {
                    setCustomFileName(allowed[0].name.replace(/\.[^/.]+$/, ''))
                } else {
                    setCustomFileName('')
                }
            } else {
                setSelectedFiles([])
                setCustomFileName('')
            }
        },
        [notifyOversizedFiles, splitOversizedFiles, toast]
    )

    // 添加更多文件
    const addFiles = useCallback(
        (files: File[]) => {
            const { valid, invalid } = validateFiles(files)
            const { allowed, oversized } = splitOversizedFiles(valid)

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

            notifyOversizedFiles(oversized)

            if (allowed.length > 0) {
                setSelectedFiles((prev) => [...prev, ...allowed])
                // 批量上传不支持自定义文件名
                setCustomFileName('')
            }
        },
        [notifyOversizedFiles, splitOversizedFiles, toast]
    )

    // 移除文件
    const removeFile = useCallback((index: number) => {
        setSelectedFiles((prev) => prev.filter((_, i) => i !== index))
    }, [])

    // 提交上传
    const submit = useCallback(
        async (currentUserRole: string) => {
            if (selectedFiles.length === 0) return

            const { allowed, oversized } = splitOversizedFiles(selectedFiles)
            if (oversized.length > 0) {
                notifyOversizedFiles(oversized)
                setSelectedFiles(allowed)
                if (allowed.length === 1) {
                    setCustomFileName((prev) => prev || allowed[0].name.replace(/\.[^/.]+$/, ''))
                } else if (allowed.length === 0) {
                    setCustomFileName('')
                }
                return
            }

            useUploadStore.getState().enqueueFiles(
                selectedFiles,
                visibility,
                currentUserRole === 'knowledge_manager' ? selectedDeptId : undefined,
                targetFolderId,
                customFileName,
                selectedFiles.length === 1 ? docDescription : undefined
            )

            // 重置状态
            setShowDialog(false)
            setSelectedFiles([])
            setUploadProgress(0)
            setIsUploading(false)
            setCustomFileName('')
            setDocDescription('')

            toast({
                type: 'success',
                title: `已加入后台上传队列`,
                description: `${selectedFiles.length} 个文件正在上传...`,
                duration: 3000,
            })

            // 调用刷新回调 (可由全局监听来刷新，或者保留以刷新可能的无关内容)
            onSuccess?.()
        },
        [
            selectedFiles,
            customFileName,
            docDescription,
            visibility,
            targetFolderId,
            selectedDeptId,
            onSuccess,
            notifyOversizedFiles,
            splitOversizedFiles,
            toast,
        ]
    )

    return {
        state: {
            selectedFiles,
            isUploading,
            uploadProgress,
            showDialog,
            docDescription,
            customFileName,
            targetFolderId,
            visibility,
            selectedDeptId,
        },
        actions: {
            openDialog,
            closeDialog,
            selectFiles,
            addFiles,
            removeFile,
            setDescription: setDocDescription,
            setCustomFileName,
            setVisibility,
            setDeptId: setSelectedDeptId,
            submit,
        },
    }
}
