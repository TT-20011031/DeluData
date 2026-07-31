/**
 * 通用音频播放器组件
 * 
 * 封装 useAudioPlayer Hook，提供 TTS 播放 UI。
 * 支持播放/暂停控制、音量调节、可视化显示。
 */

import { useState, useEffect, useRef } from 'react'
import { Play, Pause, Volume2, VolumeX } from 'lucide-react'
import { useAudioPlayer } from '../hooks/useAudioPlayer'
import type { TTSChunk } from '../types'

interface AudioPlayerProps {
    /** 是否自动播放 */
    autoPlay?: boolean
    /** 采样率 */
    sampleRate?: number
    /** 播放开始回调 */
    onPlayStart?: () => void
    /** 播放结束回调 */
    onPlayEnd?: () => void
    /** 错误回调 */
    onError?: (error: string) => void
    /** 显示可视化 */
    showVisualizer?: boolean
    /** 自定义 CSS 类 */
    className?: string
}

interface AudioPlayerRef {
    enqueue: (chunk: TTSChunk) => void
    stop: () => void
    pause: () => Promise<void>
    resume: () => Promise<void>
}

export function AudioPlayer({
    autoPlay: _autoPlay = true, // 保留接口兼容性，暂未实现自动播放控制
    sampleRate = 24000,
    onPlayStart,
    onPlayEnd,
    onError,
    showVisualizer = false,
    className = '',
}: AudioPlayerProps) {
    const [isMuted, setIsMuted] = useState(false)
    const canvasRef = useRef<HTMLCanvasElement>(null)
    const animationRef = useRef<number | null>(null)

    const player = useAudioPlayer({
        sampleRate,
        onPlayStart,
        onPlayEnd,
        onError,
    })

    // 可视化绘制
    useEffect(() => {
        if (!showVisualizer || !canvasRef.current) return

        const canvas = canvasRef.current
        const ctx = canvas.getContext('2d')
        if (!ctx) return

        const draw = () => {
            const frequencyData = player.getFrequencyData()
            if (!frequencyData) {
                animationRef.current = requestAnimationFrame(draw)
                return
            }

            ctx.clearRect(0, 0, canvas.width, canvas.height)

            const barWidth = (canvas.width / frequencyData.length) * 2
            let x = 0

            ctx.fillStyle = '#3b82f6'
            for (let i = 0; i < frequencyData.length; i++) {
                const barHeight = (frequencyData[i] / 255) * canvas.height
                ctx.fillRect(x, canvas.height - barHeight, barWidth - 1, barHeight)
                x += barWidth
            }

            animationRef.current = requestAnimationFrame(draw)
        }

        if (player.isPlaying) {
            draw()
        }

        return () => {
            if (animationRef.current) {
                cancelAnimationFrame(animationRef.current)
            }
        }
    }, [showVisualizer, player.isPlaying, player])

    const handleTogglePlay = async () => {
        if (player.isPlaying) {
            await player.pause()
        } else {
            await player.resume()
        }
    }

    const handleToggleMute = () => {
        setIsMuted(!isMuted)
        // 实际静音逻辑需要在 useAudioPlayer 中实现
    }

    return (
        <div className={`flex items-center gap-3 ${className}`}>
            {/* 播放/暂停按钮 */}
            <button
                onClick={handleTogglePlay}
                className="w-10 h-10 rounded-full bg-blue-500 hover:bg-blue-600 flex items-center justify-center text-white transition-colors"
                disabled={player.queueLength === 0 && !player.isPlaying}
            >
                {player.isPlaying ? (
                    <Pause className="h-5 w-5" />
                ) : (
                    <Play className="h-5 w-5 ml-0.5" />
                )}
            </button>

            {/* 可视化显示 */}
            {showVisualizer && (
                <canvas
                    ref={canvasRef}
                    width={120}
                    height={40}
                    className="bg-gray-100 rounded"
                />
            )}

            {/* 当前文本 */}
            {player.currentText && (
                <span className="text-sm text-gray-600 truncate max-w-[200px]">
                    {player.currentText}
                </span>
            )}

            {/* 队列长度 */}
            {player.queueLength > 0 && (
                <span className="text-xs text-gray-400">
                    Queue: {player.queueLength}
                </span>
            )}

            {/* 静音按钮 */}
            <button
                onClick={handleToggleMute}
                className="w-8 h-8 rounded-full hover:bg-gray-100 flex items-center justify-center transition-colors"
            >
                {isMuted ? (
                    <VolumeX className="h-4 w-4 text-gray-500" />
                ) : (
                    <Volume2 className="h-4 w-4 text-gray-500" />
                )}
            </button>
        </div>
    )
}

// 导出 Hook 的引用类型，供父组件控制
export type { AudioPlayerRef }
export default AudioPlayer
