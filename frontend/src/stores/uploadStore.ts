import { create } from 'zustand'
import { generateId } from '@/utils/id'
import { knowledgeService } from '@/services/knowledgeService'
import type { IngestionTaskStatus } from '@/services/knowledgeService'
import type { VisibilityType } from '@/types/knowledge'

export type UploadStatus = 'uploading' | 'processing' | 'success' | 'error' | 'cancelled'

export interface UploadTask {
    id: string
    file?: File
    fileName: string
    status: UploadStatus
    progress: number
    documentId?: string
    taskId?: string
    processingStage?: string
    processingDetail?: Record<string, any>
    error?: string
    abortController?: AbortController
    createdAt: number
}

interface UploadStoreState {
    tasks: UploadTask[]
    isDialogVisible: boolean
}

interface UploadStoreActions {
    openDialog: () => void
    closeDialog: () => void

    enqueueFiles: (
        files: File[],
        visibility: VisibilityType,
        selectedDeptId?: string,
        targetFolderId?: string,
        customFileName?: string,
        docDescription?: string
    ) => void
    cancelTask: (taskId: string) => Promise<void>
    removeTask: (taskId: string) => void
    clearCompletedTasks: () => void
    pollTaskStatus: (taskId: string) => Promise<void>

    initializeActiveTasks: () => Promise<void>
}

export type UploadStore = UploadStoreState & UploadStoreActions

const POLLING_INTERVAL = 3000
const KNOWLEDGE_FILE_STATUS_EVENT = 'knowledge:file-status-changed'

function mapTaskStatusToUploadStatus(status: IngestionTaskStatus['status']): UploadStatus {
    if (status === 'succeeded') return 'success'
    if (status === 'failed') return 'error'
    if (status === 'cancelled') return 'cancelled'
    return 'processing'
}

function extractUploadErrorMessage(response: any): string {
    const detail = response?.detail
    if (typeof detail === 'string' && detail.trim()) {
        return detail
    }
    if (Array.isArray(detail) && detail.length > 0) {
        const firstMsg = detail.find((item: any) => typeof item?.msg === 'string')?.msg
        return firstMsg || JSON.stringify(detail)
    }
    if (detail && typeof detail === 'object') {
        return JSON.stringify(detail)
    }
    return '上传失败'
}

function formatNonJsonUploadError(status: number, responseText: string): string {
    if (status === 413) {
        return '文件过大，超过当前上传限制'
    }
    if (status === 502 || status === 503 || status === 504) {
        return '服务器网关暂时不可用，请稍后重试'
    }
    const text = (responseText || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
    if (text) {
        return `服务器响应异常 (${status || 'network'}): ${text.slice(0, 160)}`
    }
    return `服务器响应异常 (${status || 'network'})`
}

function emitKnowledgeFileStatusChanged(fileId?: string, status?: string): void {
    if (!fileId || typeof window === 'undefined') return
    window.dispatchEvent(
        new CustomEvent(KNOWLEDGE_FILE_STATUS_EVENT, {
            detail: {
                file_id: fileId,
                status: status || 'unknown',
                at: Date.now(),
            },
        })
    )
}

function collectProcessingFiles(nodes: any[]): any[] {
    const result: any[] = []
    const walk = (items: any[]) => {
        for (const item of items || []) {
            if (item?.type === 'file' && item?.status === 'processing') {
                result.push(item)
            }
            if (item?.type === 'folder' && Array.isArray(item.children) && item.children.length > 0) {
                walk(item.children)
            }
        }
    }
    walk(nodes)
    return result
}

export const useUploadStore = create<UploadStore>((set, get) => ({
    tasks: [],
    isDialogVisible: false,

    openDialog: () => set({ isDialogVisible: true }),
    closeDialog: () => set({ isDialogVisible: false }),

    enqueueFiles: async (files, visibility, selectedDeptId, targetFolderId, customFileName, docDescription) => {
        const newTasks: UploadTask[] = files.map((file, index) => {
            const isSingle = files.length === 1
            const fileName = isSingle && customFileName?.trim()
                ? customFileName.trim()
                : file.name.replace(/\.[^/.]+$/, '')

            return {
                id: generateId(),
                file,
                fileName,
                status: 'uploading',
                progress: 0,
                createdAt: Date.now() + index,
                abortController: new AbortController(),
            }
        })

        set((state) => ({ tasks: [...newTasks, ...state.tasks] }))

        for (const task of newTasks) {
            executeUpload(
                task.id,
                visibility,
                selectedDeptId,
                targetFolderId,
                files.length === 1 ? docDescription : undefined,
                set,
                get
            )
        }
    },

    cancelTask: async (taskId) => {
        const task = get().tasks.find((t) => t.id === taskId)
        if (!task) return

        if (task.status === 'uploading' && task.abortController) {
            task.abortController.abort()
            set((state) => ({
                tasks: state.tasks.map((t) =>
                    t.id === taskId ? { ...t, status: 'cancelled', progress: 0 } : t
                ),
            }))
            return
        }

        if (task.status !== 'processing') return

        try {
            if (task.taskId) {
                const snapshot = await knowledgeService.cancelIngestionTask(task.taskId)
                const nextStatus = mapTaskStatusToUploadStatus(snapshot.status)
                set((state) => ({
                    tasks: state.tasks.map((t) =>
                        t.id === taskId
                            ? {
                                ...t,
                                status: nextStatus,
                                processingStage: snapshot.stage,
                                processingDetail: snapshot.detail,
                                progress: Math.max(0, Math.min(100, snapshot.progress ?? t.progress)),
                                error:
                                    nextStatus === 'error'
                                        ? snapshot.error_message || '后端处理失败'
                                        : undefined,
                            }
                            : t
                    ),
                }))
                if (nextStatus === 'processing') {
                    get().pollTaskStatus(taskId)
                }
                return
            }

            if (task.documentId) {
                await knowledgeService.deleteFile(task.documentId)
                set((state) => ({
                    tasks: state.tasks.map((t) =>
                        t.id === taskId ? { ...t, status: 'cancelled' } : t
                    ),
                }))
            }
        } catch (error) {
            console.error('Failed to cancel processing document:', error)
        }
    },

    removeTask: (taskId) => {
        set((state) => ({
            tasks: state.tasks.filter((t) => t.id !== taskId),
        }))
    },

    clearCompletedTasks: () => {
        set((state) => ({
            tasks: state.tasks.filter((t) => t.status === 'uploading' || t.status === 'processing'),
        }))
    },

    initializeActiveTasks: async () => {
        try {
            let processingFiles: any[] = []
            try {
                const structure = await knowledgeService.fetchStructure('all')
                processingFiles = collectProcessingFiles(structure)
            } catch {
                const rootNodes = await knowledgeService.fetchFolderChildren('', 'all', 500)
                processingFiles = rootNodes.filter((f: any) => f.type === 'file' && f.status === 'processing')
            }

            if (processingFiles.length === 0) return

            const recoveredTasks: UploadTask[] = await Promise.all(
                processingFiles.map(async (file: any) => {
                    let fileInfo: any = null
                    try {
                        fileInfo = await knowledgeService.getFileStatus(file.id)
                    } catch {
                        fileInfo = null
                    }

                    return {
                        id: file.id,
                        fileName: file.name,
                        status: 'processing',
                        progress: typeof fileInfo?.processing_progress === 'number' ? fileInfo.processing_progress : 1,
                        documentId: file.id,
                        taskId: fileInfo?.active_task_id,
                        processingStage: fileInfo?.processing_stage,
                        createdAt: new Date(file.created_at || Date.now()).getTime(),
                    }
                })
            )

            set((state) => {
                const existingIds = new Set(state.tasks.map((t) => t.documentId))
                const toAdd = recoveredTasks.filter((t) => !existingIds.has(t.documentId))
                return { tasks: [...toAdd, ...state.tasks] }
            })

            for (const task of recoveredTasks) {
                get().pollTaskStatus(task.id)
            }
        } catch (error) {
            console.error('Failed to initialize active upload tasks:', error)
        }
    },

    pollTaskStatus: async (taskId) => {
        const poll = async () => {
            const task = get().tasks.find((t) => t.id === taskId)
            if (!task || task.status !== 'processing') {
                return
            }

            try {
                if (task.taskId) {
                    const snapshot = await knowledgeService.getIngestionTask(task.taskId)
                    const mappedStatus = mapTaskStatusToUploadStatus(snapshot.status)

                    if (mappedStatus === 'success') {
                        emitKnowledgeFileStatusChanged(task.documentId, 'indexed')
                        set((state) => ({
                            tasks: state.tasks.map((t) =>
                                t.id === taskId
                                    ? {
                                        ...t,
                                        status: 'success',
                                        progress: 100,
                                        processingStage: snapshot.stage,
                                        processingDetail: snapshot.detail,
                                    }
                                    : t
                            ),
                        }))
                        return
                    }

                    if (mappedStatus === 'error' || mappedStatus === 'cancelled') {
                        emitKnowledgeFileStatusChanged(task.documentId, mappedStatus === 'error' ? 'error' : 'cancelled')
                        set((state) => ({
                            tasks: state.tasks.map((t) =>
                                t.id === taskId
                                    ? {
                                        ...t,
                                        status: mappedStatus,
                                        progress: Math.max(0, Math.min(100, snapshot.progress ?? t.progress)),
                                        processingStage: snapshot.stage,
                                        processingDetail: snapshot.detail,
                                        error:
                                            mappedStatus === 'error'
                                                ? snapshot.error_message || '后端处理失败'
                                                : undefined,
                                    }
                                    : t
                            ),
                        }))
                        return
                    }

                    set((state) => ({
                        tasks: state.tasks.map((t) =>
                            t.id === taskId
                                ? {
                                    ...t,
                                    status: 'processing',
                                    progress: Math.max(0, Math.min(100, snapshot.progress ?? t.progress)),
                                    processingStage: snapshot.stage,
                                    processingDetail: snapshot.detail,
                                }
                                : t
                        ),
                    }))
                    setTimeout(poll, POLLING_INTERVAL)
                    return
                }

                if (!task.documentId) {
                    return
                }

                const info = await knowledgeService.getFileStatus(task.documentId)
                if (info.status === 'indexed') {
                    emitKnowledgeFileStatusChanged(task.documentId, 'indexed')
                    set((state) => ({
                        tasks: state.tasks.map((t) =>
                            t.id === taskId ? { ...t, status: 'success', progress: 100 } : t
                        ),
                    }))
                } else if (info.status === 'error') {
                    emitKnowledgeFileStatusChanged(task.documentId, 'error')
                    set((state) => ({
                        tasks: state.tasks.map((t) =>
                            t.id === taskId
                                ? { ...t, status: 'error', error: info.error_message || '后端处理失败' }
                                : t
                        ),
                    }))
                } else {
                    set((state) => ({
                        tasks: state.tasks.map((t) =>
                            t.id === taskId
                                ? {
                                    ...t,
                                    progress:
                                        typeof info.processing_progress === 'number'
                                            ? info.processing_progress
                                            : t.progress,
                                    processingStage: info.processing_stage || t.processingStage,
                                }
                                : t
                        ),
                    }))
                    setTimeout(poll, POLLING_INTERVAL)
                }
            } catch {
                setTimeout(poll, POLLING_INTERVAL)
            }
        }

        poll()
    },
}))

async function executeUpload(
    taskId: string,
    visibility: VisibilityType,
    selectedDeptId: string | undefined,
    targetFolderId: string | undefined,
    docDescription: string | undefined,
    set: any,
    get: any
) {
    const task = get().tasks.find((t: UploadTask) => t.id === taskId)
    if (!task || !task.file) return

    const formData = new FormData()
    formData.append('file', task.file)
    formData.append('name', task.fileName)
    if (docDescription) {
        formData.append('description', docDescription)
    }
    formData.append('visibility', visibility)
    if (targetFolderId) {
        formData.append('folder_id', targetFolderId)
    }
    if (selectedDeptId) {
        formData.append('target_dept_id', selectedDeptId)
    }

    try {
        const uploadPromise = new Promise<{ success: boolean; data?: any; error?: string }>((resolve) => {
            const xhr = new XMLHttpRequest()
            const url = knowledgeService.getUploadUrl()
            const headers = knowledgeService.getAuthHeadersForXHR()

            xhr.open('POST', url)

            Object.entries(headers).forEach(([key, value]) => {
                xhr.setRequestHeader(key, value)
            })

            if (task.abortController) {
                task.abortController.signal.addEventListener('abort', () => {
                    xhr.abort()
                })
            }

            xhr.upload.onprogress = (event) => {
                if (event.lengthComputable) {
                    const progress = Math.round((event.loaded * 100) / event.total)
                    set((state: any) => ({
                        tasks: state.tasks.map((t: UploadTask) =>
                            t.id === taskId && t.status === 'uploading'
                                ? { ...t, progress: Math.min(progress, 99) }
                                : t
                        ),
                    }))
                }
            }

            xhr.onload = () => {
                try {
                    const response = JSON.parse(xhr.responseText)
                    if (xhr.status >= 200 && xhr.status < 300) {
                        resolve({ success: true, data: response })
                    } else {
                        resolve({ success: false, error: extractUploadErrorMessage(response) })
                    }
                } catch {
                    resolve({
                        success: false,
                        error: formatNonJsonUploadError(xhr.status, xhr.responseText),
                    })
                }
            }

            xhr.onerror = () => {
                resolve({ success: false, error: '网络错误、连接中断或文件超过网关限制' })
            }

            xhr.onabort = () => {
                resolve({ success: false, error: 'aborted' })
            }

            xhr.send(formData)
        })

        const result = await uploadPromise

        if (result.error === 'aborted') {
            return
        }

        if (result.success && result.data?.document_id) {
            const hasTaskId = Boolean(result.data.task_id)
            set((state: any) => ({
                tasks: state.tasks.map((t: UploadTask) =>
                    t.id === taskId
                        ? {
                            ...t,
                            status: 'processing',
                            progress: hasTaskId ? 1 : 100,
                            documentId: result.data.document_id,
                            taskId: result.data.task_id,
                            processingStage: hasTaskId ? 'queued' : t.processingStage,
                        }
                        : t
                ),
            }))
            get().pollTaskStatus(taskId)
        } else {
            set((state: any) => ({
                tasks: state.tasks.map((t: UploadTask) =>
                    t.id === taskId ? { ...t, status: 'error', error: result.error } : t
                ),
            }))
        }
    } catch (error: any) {
        set((state: any) => ({
            tasks: state.tasks.map((t: UploadTask) =>
                t.id === taskId && t.status !== 'cancelled'
                    ? { ...t, status: 'error', error: error.message }
                    : t
            ),
        }))
    }
}
