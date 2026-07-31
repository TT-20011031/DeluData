/**
 * TemplateDialog 预览区视图组件
 * 
 * 包含：
 * - IdleUploadView: 空闲状态上传区
 * - FileReadyView: 文件已选择状态
 * - ProcessingView: 处理中进度动画
 * - ErrorView: 错误状态
 * - PreviewSuccessView: 预览成功
 */
import type { MouseEvent, RefObject } from 'react'
import { FileText, Upload, AlertTriangle, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import type { ProcessingStep } from '@/types/extendConfig'

// =============================================================================
// IdleUploadView - 空闲状态上传区
// =============================================================================

interface IdleUploadViewProps {
    onFileChange: (file: File | null) => void
}

export function IdleUploadView({ onFileChange }: IdleUploadViewProps) {
    return (
        <label className="group relative w-full h-full max-w-[600px] max-h-[400px] cursor-pointer block">
            <Input
                type="file"
                accept=".docx,.xlsx"
                className="hidden"
                onChange={(e) => onFileChange(e.target.files?.[0] || null)}
            />
            <div className="absolute inset-0 border-2 border-dashed border-manus-border rounded-3xl transition-all duration-300 group-hover:border-accent group-hover:bg-accent/5 group-hover:scale-[1.02] bg-manus-secondary/30" />

            <div className="absolute inset-0 flex flex-col items-center justify-center gap-6 pointer-events-none">
                <div className="w-20 h-20 rounded-2xl bg-manus-elevated shadow-card flex items-center justify-center group-hover:scale-110 transition-transform duration-300 border border-manus-border group-hover:border-accent/50">
                    <Upload className="h-10 w-10 text-manus-muted group-hover:text-accent transition-colors" />
                </div>

                <div className="text-center space-y-2">
                    <h3 className="text-2xl font-bold text-manus-text group-hover:text-accent transition-colors">
                        点击或拖拽上传模板
                    </h3>
                    <p className="text-base text-manus-muted max-w-[300px] mx-auto">
                        支持 <span className="text-manus-text font-medium">.docx</span> / <span className="text-manus-text font-medium">.xlsx</span> 格式<br />
                        自动识别变量与文档结构
                    </p>
                </div>

                <div className="mt-8 flex gap-8 opacity-50 text-manus-muted/50 grayscale group-hover:grayscale-0 group-hover:opacity-100 transition-all duration-500">
                    <div className="flex flex-col items-center gap-2">
                        <FileText className="h-8 w-8 text-blue-400" />
                        <span className="text-xs">Word 文档</span>
                    </div>
                    <div className="flex flex-col items-center gap-2">
                        <div className="h-8 w-8 text-green-400 flex items-center justify-center font-bold border-2 border-green-400 rounded">X</div>
                        <span className="text-xs">Excel 表格</span>
                    </div>
                </div>
            </div>
        </label>
    )
}

// =============================================================================
// FileReadyView - 文件已选择状态
// =============================================================================

interface FileReadyViewProps {
    file: File
    onFileChange: (file: File | null) => void
    onSave: () => void
}

export function FileReadyView({ file, onFileChange, onSave }: FileReadyViewProps) {
    return (
        <div className="w-full max-w-[500px] bg-manus border border-accent/30 rounded-2xl p-8 flex flex-col items-center text-center shadow-lg shadow-accent/5 animate-in fade-in zoom-in-95 duration-300 relative group">
            <div className="bg-accent/10 p-4 rounded-full mb-6 relative">
                <FileText className="h-10 w-10 text-accent" />
                <div className="absolute -right-1 -top-1 w-4 h-4 bg-green-500 rounded-full border-2 border-manus" />
            </div>

            <h3 className="text-2xl font-semibold text-manus-text mb-2">文件已就绪</h3>
            <p className="text-manus-muted mb-8 max-w-[280px] break-all font-mono bg-manus-secondary px-3 py-1 rounded">
                {file.name}
            </p>

            <div className="flex gap-4 w-full">
                <Button variant="outline" size="lg" className="flex-1 h-12" onClick={() => onFileChange(null)}>
                    更换文件
                </Button>
                <Button
                    size="lg"
                    className="flex-1 h-12 bg-accent hover:bg-accent/90 text-white shadow-lg shadow-accent/20"
                    onClick={onSave}
                >
                    立即上传
                </Button>
            </div>
        </div>
    )
}

// =============================================================================
// ProcessingView - 处理中进度动画
// =============================================================================

interface ProcessingViewProps {
    step: ProcessingStep
    progress: number
}

export function ProcessingView({ step, progress }: ProcessingViewProps) {
    const stepIndex = step === 'uploading' ? 0 : step === 'analyzing' ? 1 : 2
    const stepText: Record<ProcessingStep, string> = {
        uploading: '正在上传文件...',
        analyzing: '正在分析变量结构...',
        generating: '正在生成预览...'
    }

    return (
        <div className="w-full max-w-[400px] flex flex-col items-center gap-8 animate-in fade-in zoom-in-95 duration-500">
            {/* 圆形进度 */}
            <div className="relative w-32 h-32 flex items-center justify-center">
                <div className="absolute inset-0 border-4 border-manus-border rounded-full opacity-20" />
                <div
                    className="absolute inset-0 border-4 border-accent rounded-full border-l-transparent border-b-transparent transition-all duration-300"
                    style={{ transform: `rotate(${progress * 3.6}deg)` }}
                />
                <div className="text-3xl font-bold font-mono text-accent">{Math.floor(progress)}%</div>
            </div>

            {/* 步骤文本 */}
            <div className="space-y-3 w-full text-center">
                <h3 className="text-xl font-medium text-manus-text">{stepText[step]}</h3>
                <p className="text-sm text-manus-muted">请勿关闭窗口，这可能需要几秒钟</p>
            </div>

            {/* 步骤指示器 */}
            <div className="flex items-center gap-2 mt-4">
                {[0, 1, 2].map(i => (
                    <div
                        key={i}
                        className={cn(
                            "h-1.5 w-12 rounded-full transition-colors duration-500",
                            stepIndex >= i ? "bg-accent" : "bg-manus-border"
                        )}
                    />
                ))}
            </div>
        </div>
    )
}

// =============================================================================
// ErrorView - 错误状态
// =============================================================================

interface ErrorViewProps {
    message: string
    onRetry: () => void
    onClose: () => void
}

export function ErrorView({ message, onRetry, onClose }: ErrorViewProps) {
    return (
        <div className="flex flex-col items-center text-center animate-in fade-in zoom-in-95 duration-500">
            <div className="relative w-24 h-24 flex items-center justify-center mb-6">
                <div className="absolute inset-0 bg-red-500/10 rounded-full animate-pulse" />
                <div className="h-20 w-20 bg-red-500 rounded-full flex items-center justify-center shadow-lg shadow-red-500/20">
                    <AlertTriangle className="h-10 w-10 text-white" />
                </div>
            </div>

            <h3 className="text-xl font-medium text-red-500 mb-2">处理失败</h3>
            <p className="text-manus-text mb-6 px-4 py-2 bg-red-500/10 rounded-lg border border-red-500/20 text-sm max-w-[300px]">
                {message}
            </p>

            <div className="flex gap-3">
                <Button
                    variant="outline"
                    onClick={onClose}
                    className="border-red-500/20 hover:bg-red-500/10 hover:text-red-500"
                >
                    关闭
                </Button>
                <Button
                    onClick={onRetry}
                    className="bg-red-500 hover:bg-red-600 text-white shadow-lg shadow-red-500/20"
                >
                    <RotateCw className="h-4 w-4 mr-2" />
                    重试
                </Button>
            </div>
        </div>
    )
}

// =============================================================================
// PreviewSuccessView - 预览成功
// =============================================================================

interface PreviewSuccessViewProps {
    html: string
    previewRef: RefObject<HTMLDivElement | null>
    onMouseUp: (e: MouseEvent) => void
}

export function PreviewSuccessView({ html, previewRef, onMouseUp }: PreviewSuccessViewProps) {
    return (
        <div
            className="flex-1 overflow-y-auto custom-scrollbar bg-manus-elevated/50"
            onClick={onMouseUp}
            ref={previewRef}
        >
            {/* 候选高亮样式 */}
            <style>{`
                .docx-preview-content [data-id].candidate-highlight {
                    background-color: rgba(59, 130, 246, 0.3) !important;
                    outline: 2px solid #3b82f6 !important;
                    border-radius: 2px;
                    transition: all 0.2s ease;
                }
                .docx-preview-content [data-id].variable-bound {
                    background-color: rgba(34, 197, 94, 0.2) !important;
                    outline: 2px solid #22c55e !important;
                    border-radius: 2px;
                }
                .docx-preview-content [data-id] {
                    transition: background-color 0.2s, outline 0.2s;
                }
                .docx-preview-content [data-id]:hover {
                    background-color: rgba(59, 130, 246, 0.1);
                    cursor: pointer;
                }
            `}</style>

            {/* 上方间距 */}
            <div className="pt-8" />

            {/* 白色纸张容器 - 自适应内容高度 */}
            <div className="flex justify-center px-8 pb-8">
                <div className="w-full max-w-[800px] bg-white text-black shadow-2xl rounded-sm origin-top animate-in fade-in slide-in-from-bottom-4 duration-700">
                    {/* 纸张内边距模拟 A4 页面 */}
                    <div
                        className="px-[60px] py-[50px] prose prose-sm max-w-none docx-preview-content select-none cursor-text decoration-clone"
                        dangerouslySetInnerHTML={{ __html: html }}
                        style={{ ['--selection-color' as string]: '#3b82f640' }}
                    />
                </div>
            </div>
        </div>
    )
}
