/**
 * 聊天输入区域组件
 * 
 * 包含文件上传按钮、拖拽上传、文本输入和发送按钮
 * 支持图片上传，Excel/Word 文件上传前端暂停使用
 */
import { useRef, useState } from 'react'
import { ArrowUp, Loader2, Plus, X, FileSpreadsheet, Image as ImageIcon, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { getAuthHeader } from '@/stores/authStore'
import type { UploadedFile } from '@/types/chat'
import type { QuickToolMode } from './QuickToolModes'
import type { DocScope } from '@/types/docScope'
import { KnowledgeScopePicker } from './KnowledgeScopePicker'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

// 前端暂停使用：按当前产品要求禁用 chat 文档上传，只保留图片上传入口。
const CHAT_DOCUMENT_UPLOAD_DISABLED = true

// 支持的文件类型
const DOCUMENT_FILE_TYPES = ['.xlsx', '.xls', '.csv', '.docx', '.doc']
const IMAGE_FILE_TYPES = ['.png', '.jpg', '.jpeg', '.gif', '.webp']

// 图片大小限制: 10MB
const MAX_IMAGE_SIZE = 10 * 1024 * 1024

interface ChatInputAreaProps {
    inputMessage: string
    setInputMessage: (value: string) => void
    isLoading: boolean
    isExecuting: boolean
    currentSessionId: string | null
    onSend: () => void
    onError: (message: string) => void
    onFileChange?: (file: UploadedFile | null) => void
    selectedMode?: QuickToolMode | null
    onRemoveMode?: () => void
    selectedSkill?: { id: string; name: string } | null
    onRemoveSkill?: () => void
    docScope?: DocScope | null
    onDocScopeChange?: (scope: DocScope | null) => void
}

export function ChatInputArea({
    inputMessage,
    setInputMessage,
    isLoading,
    isExecuting,
    currentSessionId,
    onSend,
    onError,
    onFileChange,
    selectedMode,
    onRemoveMode,
    selectedSkill = null,
    onRemoveSkill,
    docScope = null,
    onDocScopeChange,
}: ChatInputAreaProps) {
    const textareaRef = useRef<HTMLTextAreaElement>(null)
    const fileInputRef = useRef<HTMLInputElement>(null)

    const [uploadedFile, setUploadedFile] = useState<UploadedFile | null>(null)
    const [isUploading, setIsUploading] = useState(false)
    const [dragEnterCount, setDragEnterCount] = useState(0)  // 用于防止子元素触发闪烁

    const handleFileUpload = async (file: File) => {
        if (isUploading) return

        const ext = '.' + file.name.split('.').pop()?.toLowerCase()
        const isImage = IMAGE_FILE_TYPES.includes(ext)
        const isDocument = DOCUMENT_FILE_TYPES.includes(ext)

        if (CHAT_DOCUMENT_UPLOAD_DISABLED && isDocument) {
            onError('文件上传功能前端已暂停使用')
            return
        }

        if (!isImage) {
            onError(`不支持的文件类型: ${ext}，请上传图片文件`)
            return
        }

        if (file.size > MAX_IMAGE_SIZE) {
            onError(`图片过大 (${(file.size / 1024 / 1024).toFixed(1)}MB)，最大支持 10MB`)
            return
        }

        setIsUploading(true)

        try {
            // 前端暂停使用文档上传后，这里仅保留图片上传逻辑。
            const previewUrl = URL.createObjectURL(file)
            const formData = new FormData()
            formData.append('file', file)
            if (currentSessionId) {
                formData.append('session_id', currentSessionId)
            }

            const response = await fetch(`${API_BASE_URL}/chat/upload-image`, {
                method: 'POST',
                headers: getAuthHeader(),
                body: formData,
            })

            if (!response.ok) {
                const error = await response.json()
                throw new Error(error.detail || '图片上传失败')
            }

            const data = await response.json()
            const newFile: UploadedFile = {
                name: file.name,
                path: data.image_url,
                session_id: data.session_id,
                sandbox_path: data.image_url,
                type: 'image',
                previewUrl,
            }
            setUploadedFile(newFile)
            onFileChange?.(newFile)
        } catch (error) {
            console.error('Upload error:', error)
            onError(error instanceof Error ? error.message : '上传失败')
        } finally {
            setIsUploading(false)
        }
    }

    const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = e.target.files
        if (files && files.length > 0) {
            handleFileUpload(files[0])
        }
        e.target.value = ''
    }

    const handleDragEnter = (e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        if (!isLoading && !isExecuting) {
            setDragEnterCount(prev => prev + 1)
        }
    }

    const handleDragLeave = (e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        setDragEnterCount(prev => Math.max(0, prev - 1))
    }

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
    }

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        setDragEnterCount(0)

        if (isLoading || isExecuting) return

        const files = e.dataTransfer.files
        if (files.length > 0) {
            handleFileUpload(files[0])
        }
    }

    const isDragOver = dragEnterCount > 0

    const handleRemoveFile = () => {
        if (uploadedFile?.previewUrl?.startsWith('blob:')) {
            URL.revokeObjectURL(uploadedFile.previewUrl)
        }
        setUploadedFile(null)
        onFileChange?.(null)  // 通知父组件文件已移除
    }

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            onSend()
        }
    }

    return (
        <div
            className={cn(
                "relative overflow-hidden bg-manus-secondary/80 backdrop-blur-xl rounded-3xl border shadow-lg transition-all",
                isDragOver
                    ? "border-accent ring-2 ring-accent/30"
                    : "border-manus-border/40 hover:border-manus-border/60 focus-within:border-accent/40 focus-within:shadow-xl"
            )}
            onDragEnter={handleDragEnter}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
        >
            {/* 已上传文件/图片显示 */}
            {uploadedFile && (
                <div className="mx-4 mt-3 flex items-center gap-2 rounded-lg border border-accent/20 bg-accent/10 px-2 py-1.5">
                    {uploadedFile.type === 'image' && uploadedFile.previewUrl ? (
                        <img
                            src={uploadedFile.previewUrl}
                            alt={uploadedFile.name}
                            className="h-10 w-10 object-cover rounded shrink-0"
                        />
                    ) : (
                        <FileSpreadsheet className="h-4 w-4 text-accent shrink-0" />
                    )}
                    <div className="flex-1 min-w-0">
                        <span className="text-sm text-manus-text truncate block">{uploadedFile.name}</span>
                        {uploadedFile.type === 'image' && (
                            <span className="text-xs text-manus-subtle flex items-center gap-1">
                                <ImageIcon className="h-3 w-3" /> 图片
                            </span>
                        )}
                    </div>
                    <button
                        onClick={handleRemoveFile}
                        className="p-0.5 hover:bg-accent/20 rounded transition-colors"
                    >
                        <X className="h-3.5 w-3.5 text-manus-subtle hover:text-manus-text" />
                    </button>
                </div>
            )}

            {/* 拖拽提示 */}
            {isDragOver && (
                <div className="absolute inset-0 flex items-center justify-center bg-manus-secondary/90 backdrop-blur-sm rounded-3xl z-10">
                    <div className="text-accent font-medium flex items-center gap-2">
                        <Plus className="h-5 w-5" />
                        松开以上传图片
                    </div>
                </div>
            )}

            {/* Textarea + 技能胶囊 overlay（Manus 风格） */}
            <div className="relative">
                {selectedSkill && (
                    <div className="absolute top-[17px] left-4 z-10 flex items-center gap-1 bg-violet-500/15 border border-violet-400/30 rounded-md px-1.5 py-0.5 max-w-[108px] pointer-events-auto">
                        <Zap className="h-3 w-3 text-violet-500 shrink-0" />
                        <span className="text-[11px] font-medium text-violet-500 truncate">{selectedSkill.name}</span>
                        <button
                            onClick={onRemoveSkill}
                            className="ml-0.5 hover:bg-violet-400/20 rounded p-0.5 shrink-0"
                        >
                            <X className="h-2.5 w-2.5 text-violet-400" />
                        </button>
                    </div>
                )}
                <Textarea
                    ref={textareaRef}
                    value={inputMessage}
                    onChange={(e) => setInputMessage(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={selectedSkill ? '' : (uploadedFile ? `已上传 ${uploadedFile.name}，输入您想做的操作...` : "分配一个任务或提问任何问题")}
                    disabled={isLoading || isExecuting}
                    rows={3}
                    className={cn(
                        "w-full min-h-[88px] max-h-[200px] resize-none rounded-none border-0 shadow-none outline-none bg-transparent focus-visible:ring-0 focus-visible:ring-offset-0 focus:outline-none text-manus-text placeholder:text-manus-subtle pb-14 text-base",
                        selectedSkill ? "pt-[17px] pl-[128px] pr-5" : "px-5 pt-5"
                    )}
                />
            </div>

            {/* 底部悬浮工具栏 */}
            <div className="absolute bottom-3 left-3 right-3 flex items-center justify-between">
                {/* 左侧：上传按钮 */}
                <div className="flex items-center gap-2">
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept=".png,.jpg,.jpeg,.gif,.webp"
                        onChange={handleFileInputChange}
                        className="hidden"
                    />
                    <Button
                        onClick={() => fileInputRef.current?.click()}
                        disabled={isLoading || isExecuting || isUploading}
                        size="icon"
                        variant="ghost"
                        className="h-7 w-7 rounded-md text-manus-subtle hover:text-manus-text hover:bg-manus-hover"
                        title="上传图片"
                    >
                        {isUploading ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <Plus className="h-5 w-5" />
                        )}
                    </Button>

                    {/* 模式标签 Badge */}
                    {selectedMode && (
                        <div
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium transition-all ${selectedMode.color}`}
                        >
                            <selectedMode.icon className="h-3.5 w-3.5" />
                            <span>{selectedMode.name}</span>
                            <button
                                onClick={onRemoveMode}
                                className="ml-1 hover:opacity-70 transition-opacity"
                            >
                                <X className="h-3 w-3" />
                            </button>
                        </div>
                    )}
                    {onDocScopeChange && (
                        <KnowledgeScopePicker
                            value={docScope}
                            onChange={onDocScopeChange}
                            disabled={isLoading || isExecuting}
                        />
                    )}
                </div>

                {/* 右侧：发送按钮 */}
                <Button
                    onClick={onSend}
                    disabled={!inputMessage.trim() || isLoading || isExecuting}
                    size="icon"
                    className={cn(
                        "h-8 w-8 rounded-full transition-all",
                        inputMessage.trim() && !isLoading && !isExecuting
                            ? "bg-manus-text hover:bg-manus-text/80 text-manus shadow-md"
                            : "bg-manus-tertiary text-manus-subtle hover:bg-manus-hover"
                    )}
                >
                    {isLoading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                        <ArrowUp className="h-4 w-4" />
                    )}
                </Button>
            </div>
        </div>
    )
}
