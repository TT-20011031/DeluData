/**
 * 博物馆模块 - 音频播放器组件
 * 
 * 显示 TTS 音频播放状态和控制按钮
 */

import { Volume2, VolumeX, Pause, Play, Square } from 'lucide-react'
import { cn } from '@/lib/utils'

interface AudioPlayerProps {
    isPlaying: boolean
    currentText: string
    queueLength: number
    onPause?: () => void
    onResume?: () => void
    onStop?: () => void
    className?: string
}

export function AudioPlayer({
    isPlaying,
    currentText,
    queueLength,
    onPause,
    onResume,
    onStop,
    className,
}: AudioPlayerProps) {
    if (!isPlaying && queueLength === 0 && !currentText) {
        return null
    }

    return (
        <div
            className={cn(
                'flex items-center gap-3 px-4 py-2 bg-gradient-to-r from-blue-50 to-indigo-50',
                'border border-blue-200 rounded-lg shadow-sm',
                className
            )}
        >
            {/* 播放状态图标 */}
            <div className="flex-shrink-0">
                {isPlaying ? (
                    <div className="relative">
                        <Volume2 className="w-5 h-5 text-blue-600 animate-pulse" />
                        <span className="absolute -top-1 -right-1 w-2 h-2 bg-green-500 rounded-full animate-ping" />
                    </div>
                ) : (
                    <VolumeX className="w-5 h-5 text-gray-400" />
                )}
            </div>

            {/* 当前播放文本 */}
            <div className="flex-1 min-w-0">
                <p className="text-sm text-gray-700 truncate">
                    {currentText || '准备播放...'}
                </p>
                {queueLength > 0 && (
                    <p className="text-xs text-gray-500">
                        队列中还有 {queueLength} 段音频
                    </p>
                )}
            </div>

            {/* 控制按钮 */}
            <div className="flex-shrink-0 flex items-center gap-2">
                {isPlaying ? (
                    <button
                        onClick={onPause}
                        className="p-1.5 text-gray-600 hover:text-blue-600 hover:bg-blue-100 rounded-full transition-colors"
                        title="暂停"
                    >
                        <Pause className="w-4 h-4" />
                    </button>
                ) : (
                    <button
                        onClick={onResume}
                        className="p-1.5 text-gray-600 hover:text-blue-600 hover:bg-blue-100 rounded-full transition-colors"
                        title="继续"
                    >
                        <Play className="w-4 h-4" />
                    </button>
                )}
                <button
                    onClick={onStop}
                    className="p-1.5 text-gray-600 hover:text-red-600 hover:bg-red-100 rounded-full transition-colors"
                    title="停止"
                >
                    <Square className="w-4 h-4" />
                </button>
            </div>
        </div>
    )
}

export default AudioPlayer
