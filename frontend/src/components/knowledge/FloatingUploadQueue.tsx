import { useState } from 'react'
import { useUploadStore } from '@/stores/uploadStore'
import { Loader2, X, CheckCircle2, AlertCircle, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'

const STAGE_LABELS: Record<string, string> = {
    queued: '排队中',
    preclean: '清理旧数据',
    parsing: '解析文档',
    ocr: 'OCR 处理中',
    chunking: '文本切分',
    embedding: '向量生成',
    storing: '写入存储',
    completed: '处理完成',
    failed: '处理失败',
}

function getProcessingText(task: any): string {
    if (task.status === 'uploading') return '正在上传...'
    if (task.status === 'processing') {
        const ocr = task.processingDetail?.ocr
        if (ocr && typeof ocr.completed_pages === 'number' && typeof ocr.total_pages === 'number' && ocr.total_pages > 0) {
            return `OCR 处理中 ${ocr.completed_pages}/${ocr.total_pages} 页`
        }
        if (task.processingStage && STAGE_LABELS[task.processingStage]) {
            return `${STAGE_LABELS[task.processingStage]}...`
        }
        return '后台处理中...'
    }
    if (task.status === 'success') return '处理完成'
    if (task.status === 'error') return task.error || '上传失败'
    if (task.status === 'cancelled') return '已取消'
    return '处理中...'
}

export function FloatingUploadQueue() {
    const { tasks, cancelTask, removeTask, clearCompletedTasks } = useUploadStore()
    const [isExpanded, setIsExpanded] = useState(false)

    if (tasks.length === 0) return null

    const activeTasksCount = tasks.filter(t => t.status === 'uploading' || t.status === 'processing').length
    const hasError = tasks.some(t => t.status === 'error')

    return (
        <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end">
            {/* 展开的列表 */}
            {isExpanded && (
                <div className="mb-4 w-80 bg-manus-secondary border border-manus-border rounded-lg shadow-xl overflow-hidden animate-in slide-in-from-bottom-5">
                    <div className="px-4 py-3 border-b border-manus-border flex items-center justify-between bg-manus-background">
                        <h3 className="text-sm font-semibold text-manus-text">上传队列 ({tasks.length})</h3>
                        <div className="flex items-center space-x-2">
                            <button
                                onClick={clearCompletedTasks}
                                className="text-xs text-manus-subtle hover:text-manus-text transition-colors"
                            >
                                清除已完成
                            </button>
                            <button onClick={() => setIsExpanded(false)} className="text-manus-subtle hover:text-manus-text">
                                <ChevronDown className="w-4 h-4" />
                            </button>
                        </div>
                    </div>
                    <div className="max-h-80 overflow-y-auto p-2 space-y-2">
                        {tasks.map(task => (
                            <div key={task.id} className="relative group p-3 rounded-md bg-manus-tertiary border border-manus-border flex items-start gap-3">
                                {/* Status Icon */}
                                <div className="mt-0.5 shrink-0">
                                    {task.status === 'uploading' && <Loader2 className="w-4 h-4 text-accent animate-spin" />}
                                    {task.status === 'processing' && <Loader2 className="w-4 h-4 text-accent animate-spin" />}
                                    {task.status === 'success' && <CheckCircle2 className="w-4 h-4 text-green-500" />}
                                    {task.status === 'error' && <AlertCircle className="w-4 h-4 text-red-500" />}
                                    {task.status === 'cancelled' && <X className="w-4 h-4 text-manus-muted" />}
                                </div>

                                {/* Content */}
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center justify-between">
                                        <p className="text-sm text-manus-text font-medium truncate pr-4" title={task.fileName}>
                                            {task.fileName}
                                        </p>
                                        <div className="flex space-x-1 shrink-0">
                                            {(task.status === 'uploading' || task.status === 'processing') && (
                                                <button
                                                    onClick={() => cancelTask(task.id)}
                                                    className="w-6 h-6 flex items-center justify-center rounded-sm hover:bg-manus-background text-manus-subtle hover:text-red-400 transition-colors"
                                                    title="取消"
                                                >
                                                    <X className="w-3.5 h-3.5" />
                                                </button>
                                            )}
                                            {(task.status === 'success' || task.status === 'error' || task.status === 'cancelled') && (
                                                <button
                                                    onClick={() => removeTask(task.id)}
                                                    className="w-6 h-6 flex items-center justify-center rounded-sm hover:bg-manus-background text-manus-subtle hover:text-manus-text transition-colors"
                                                    title="移除"
                                                >
                                                    <X className="w-3.5 h-3.5" />
                                                </button>
                                            )}
                                        </div>
                                    </div>

                                    <div className="mt-1 flex items-center justify-between text-xs">
                                        <span className={
                                            task.status === 'error' ? 'text-red-400' :
                                                task.status === 'success' ? 'text-green-500' :
                                                    'text-manus-subtle'
                                        }>
                                            {getProcessingText(task)}
                                        </span>
                                        {(task.status === 'uploading' || task.status === 'processing') && (
                                            <span className="text-manus-subtle font-mono">{task.progress}%</span>
                                        )}
                                    </div>

                                    {/* Progress Bar */}
                                    {(task.status === 'uploading' || task.status === 'processing') && (
                                        <div className="mt-2 h-1.5 w-full bg-manus-background rounded-full overflow-hidden">
                                            <div
                                                className={`h-full transition-all duration-300 ${task.status === 'processing' ? 'bg-accent/70' : 'bg-accent'}`}
                                                style={{ width: `${task.progress}%` }}
                                            />
                                        </div>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* 悬浮按钮 (最小化状态) */}
            <Button
                variant="outline"
                size="icon"
                onClick={() => setIsExpanded(!isExpanded)}
                className={`w-14 h-14 rounded-full shadow-lg border border-manus-border  transition-all duration-300 ${hasError ? 'bg-red-500/10 hover:bg-red-500/20 text-red-500 border-red-500/30' : 'bg-manus-secondary hover:bg-manus-tertiary text-accent'} ${isExpanded ? 'scale-90 rotate-180 opacity-0 pointer-events-none' : ''}`}
            >
                <div className="relative flex items-center justify-center w-full h-full">
                    {activeTasksCount > 0 ? (
                        <div className="relative flex items-center justify-center">
                            <Loader2 className="w-6 h-6 animate-spin" />
                            <span className="absolute text-[10px] font-bold">{activeTasksCount}</span>
                        </div>
                    ) : hasError ? (
                        <AlertCircle className="w-6 h-6 text-red-500" />
                    ) : (
                        <CheckCircle2 className="w-6 h-6 text-green-500" />
                    )}
                </div>
            </Button>
        </div>
    )
}
