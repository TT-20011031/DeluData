/**
 * 通用录音按钮组件
 * 
 * 封装 useVoiceRecorder Hook，提供开箱即用的录音 UI。
 * 支持按住录音或点击开始、确认发送两种交互模式。
 */

import { Mic, Loader2, Send, X } from 'lucide-react'
import { useVoiceRecorder } from '../hooks/useVoiceRecorder'

interface VoiceRecorderProps {
    /** 语音识别成功回调 */
    onTranscript: (text: string) => void
    /** 错误回调 */
    onError?: (error: string) => void
    /** ASR API 端点（默认 /api/voice/transcribe） */
    apiEndpoint?: string
    /** 禁用状态 */
    disabled?: boolean
    /** 显示空闲提示 */
    showIdleHint?: boolean
    /** 自定义 CSS 类 */
    className?: string
    /** 按钮尺寸 */
    size?: 'sm' | 'md' | 'lg'
    /** 录音交互模式：按住说话，或点击开始后确认发送 */
    interactionMode?: 'hold' | 'tap-confirm'
}

const sizeClasses = {
    sm: 'w-14 h-14',
    md: 'w-20 h-20',
    lg: 'w-24 h-24',
}

const iconSizeClasses = {
    sm: 'h-6 w-6',
    md: 'h-8 w-8',
    lg: 'h-10 w-10',
}

export function VoiceRecorder({
    onTranscript,
    onError,
    apiEndpoint,
    disabled = false,
    showIdleHint = true,
    className = '',
    size = 'md',
    interactionMode = 'hold',
}: VoiceRecorderProps) {
    const {
        isRecording,
        isTranscribing,
        isCancelling,
        startRecording,
        stopRecording,
        updateCancelState,
    } = useVoiceRecorder({
        transcribeEndpoint: apiEndpoint,
        onTranscribe: onTranscript,
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

    const buttonSize = sizeClasses[size]
    const iconSize = iconSizeClasses[size]
    const isTapConfirm = interactionMode === 'tap-confirm'

    const handleTapStart = () => {
        if (disabled || isRecording || isTranscribing) return
        void startRecording()
    }

    const handleSend = () => {
        stopRecording(false)
    }

    const handleCancel = () => {
        stopRecording(true)
    }

    return (
        <div className={`${isTapConfirm ? 'flex flex-col items-center' : 'relative'} ${className}`}>
            {/* 录音按钮 */}
            <button
                type="button"
                onClick={isTapConfirm ? handleTapStart : undefined}
                onMouseDown={isTapConfirm ? undefined : handleMouseDown}
                onMouseUp={isTapConfirm ? undefined : handleMouseUp}
                onMouseMove={isTapConfirm ? undefined : handleMouseMove}
                onMouseLeave={isTapConfirm ? undefined : handleMouseLeave}
                onTouchStart={isTapConfirm ? undefined : handleTouchStart}
                onTouchEnd={isTapConfirm ? undefined : handleTouchEnd}
                onTouchMove={isTapConfirm ? undefined : handleTouchMove}
                disabled={disabled || isTranscribing}
                aria-label={isTapConfirm ? '点击开始录音' : '按住说话'}
                className={`
                    relative ${buttonSize} rounded-full flex items-center justify-center
                    transition-all duration-200 select-none touch-none
                    ${isRecording
                        ? isCancelling
                            ? 'bg-red-500 scale-110 shadow-lg shadow-red-500/30'
                            : 'bg-blue-500 scale-110 shadow-lg shadow-blue-500/30'
                        : isTranscribing
                            ? 'bg-gray-200'
                            : 'bg-gray-100 hover:bg-gray-200 border border-gray-300'
                    }
                    ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
                `}
            >
                {isTranscribing ? (
                    <Loader2 className={`${iconSize} text-blue-500 animate-spin`} />
                ) : isRecording && isCancelling ? (
                    <X className={`${iconSize} text-white`} />
                ) : (
                    <Mic className={`
                        ${iconSize} transition-colors
                        ${isRecording ? 'text-white' : 'text-gray-700'}
                    `} />
                )}

                {/* 录音波纹动画 */}
                {isRecording && !isCancelling && (
                    <>
                        <span className="absolute inset-0 rounded-full bg-blue-500/30 animate-ping" />
                        <span className="absolute inset-[-8px] rounded-full border-2 border-blue-500/50 animate-pulse" />
                    </>
                )}
            </button>

            {/* 状态提示 */}
            <div className={isTapConfirm ? 'mt-4 min-h-5 whitespace-nowrap' : 'absolute -bottom-8 left-1/2 -translate-x-1/2 whitespace-nowrap'}>
                {isTranscribing ? (
                    <span className="text-sm text-blue-500">识别中...</span>
                ) : isRecording ? (
                    isTapConfirm ? (
                        <span className="text-sm font-medium text-blue-600">正在录音...</span>
                    ) : isCancelling ? (
                        <span className="text-sm text-red-500">松开取消</span>
                    ) : (
                        <span className="text-sm text-blue-500">松开发送</span>
                    )
                ) : showIdleHint ? (
                    <span className="text-sm text-gray-500">
                        {isTapConfirm ? '点击开始说话' : '按住说话'}
                    </span>
                ) : null}
            </div>

            {isTapConfirm && isRecording && (
                <div className="mt-4 flex w-full min-w-[220px] items-center gap-3">
                    <button
                        type="button"
                        onClick={handleCancel}
                        className="inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-md border border-gray-300 bg-white px-4 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50 active:bg-gray-100"
                    >
                        <X className="h-4 w-4" />
                        取消
                    </button>
                    <button
                        type="button"
                        onClick={handleSend}
                        className="inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-md bg-[#176b4d] px-4 text-sm font-semibold text-white transition-colors hover:bg-[#12543d] active:bg-[#0e4633]"
                    >
                        <Send className="h-4 w-4" />
                        发送
                    </button>
                </div>
            )}
        </div>
    )
}

export default VoiceRecorder
