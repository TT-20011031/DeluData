/**
 * 语音输入组件
 * 
 * 点击录音，松开发送
 * 使用阿里云 ASR 进行语音识别
 * 
 * [Zero Tech Debt] 使用公用模块 useVoiceRecorder
 */
import { Mic, Loader2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useVoiceRecorder } from '@/shared/voice'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

interface VoiceInputProps {
    onTranscribe: (text: string) => void
    onError?: (error: string) => void
    disabled?: boolean
    showIdleHint?: boolean
    className?: string
}

export function VoiceInput({
    onTranscribe,
    onError,
    disabled = false,
    showIdleHint = true,
    className
}: VoiceInputProps) {
    // 使用公用 Hook
    const {
        isRecording,
        isTranscribing,
        isCancelling,
        startRecording,
        stopRecording,
        updateCancelState,
    } = useVoiceRecorder({
        // Museum 仍使用原有端点（保持向后兼容）
        transcribeEndpoint: `${API_BASE_URL}/museum/speech/transcribe`,
        onTranscribe,
        onError,
    })

    // 鼠标事件
    const handleMouseDown = (e: React.MouseEvent) => {
        e.preventDefault()
        if (disabled) return
        startRecording(e.clientY)
    }

    const handleMouseUp = () => {
        stopRecording(isCancelling)
    }

    const handleMouseMove = (e: React.MouseEvent) => {
        updateCancelState(e.clientY)
    }

    const handleMouseLeave = () => {
        if (isRecording) {
            stopRecording(true)
        }
    }

    // 触摸事件
    const handleTouchStart = (e: React.TouchEvent) => {
        e.preventDefault()
        if (disabled) return
        const touch = e.touches[0]
        startRecording(touch.clientY)
    }

    const handleTouchEnd = () => {
        stopRecording(isCancelling)
    }

    const handleTouchMove = (e: React.TouchEvent) => {
        const touch = e.touches[0]
        updateCancelState(touch.clientY)
    }

    return (
        <div className={cn("relative", className)}>
            {/* 录音按钮 */}
            <button
                onMouseDown={handleMouseDown}
                onMouseUp={handleMouseUp}
                onMouseMove={handleMouseMove}
                onMouseLeave={handleMouseLeave}
                onTouchStart={handleTouchStart}
                onTouchEnd={handleTouchEnd}
                onTouchMove={handleTouchMove}
                disabled={disabled || isTranscribing}
                className={cn(
                    "relative w-20 h-20 rounded-full flex items-center justify-center transition-all duration-200",
                    "select-none touch-none",
                    isRecording
                        ? isCancelling
                            ? "bg-red-500 scale-110 shadow-lg shadow-red-500/30"
                            : "bg-accent scale-110 shadow-lg shadow-accent/30"
                        : isTranscribing
                            ? "bg-manus-tertiary"
                            : "bg-manus-secondary hover:bg-manus-tertiary border border-manus-border",
                    disabled && "opacity-50 cursor-not-allowed"
                )}
            >
                {isTranscribing ? (
                    <Loader2 className="h-8 w-8 text-accent animate-spin" />
                ) : isRecording && isCancelling ? (
                    <X className="h-8 w-8 text-white" />
                ) : (
                    <Mic className={cn(
                        "h-8 w-8 transition-colors",
                        isRecording ? "text-white" : "text-manus-text"
                    )} />
                )}

                {/* 录音波纹动画 */}
                {isRecording && !isCancelling && (
                    <>
                        <span className="absolute inset-0 rounded-full bg-accent/30 animate-ping" />
                        <span className="absolute inset-[-8px] rounded-full border-2 border-accent/50 animate-pulse" />
                    </>
                )}
            </button>

            {/* 状态提示 */}
            <div className="absolute -bottom-8 left-1/2 -translate-x-1/2 whitespace-nowrap">
                {isTranscribing ? (
                    <span className="text-sm text-accent">识别中...</span>
                ) : isRecording ? (
                    isCancelling ? (
                        <span className="text-sm text-red-500">松开取消</span>
                    ) : (
                        <span className="text-sm text-accent">松开发送</span>
                    )
                ) : showIdleHint ? (
                    <span className="text-sm text-manus-subtle">按住说话</span>
                ) : null}
            </div>
        </div>
    )
}
