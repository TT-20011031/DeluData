/**
 * 音频播放器 Hook
 * 
 * 处理 TTS 音频流的播放，支持 PCM 格式音频队列管理。
 * 使用 Web Audio API 实现低延迟音频播放。
 * 
 * 可视化支持：暴露 analyserNode 用于波形/频谱绘制。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { TTSChunk, AudioPlayerOptions, AudioPlayerState } from '../types'
import { base64ToArrayBuffer, pcmToFloat32 } from '../utils/audio-converter'

interface AudioQueueItem {
    audioData: ArrayBuffer
    text?: string
    isFinal: boolean
}

export function useAudioPlayer(options: AudioPlayerOptions = {}) {
    const {
        sampleRate = 24000,
        onPlayStart,
        onPlayEnd,
        onError
    } = options

    const [state, setState] = useState<AudioPlayerState>({
        isPlaying: false,
        currentText: '',
        queueLength: 0,
    })

    const audioContextRef = useRef<AudioContext | null>(null)
    const analyserRef = useRef<AnalyserNode | null>(null)
    const audioQueueRef = useRef<AudioQueueItem[]>([])
    const isProcessingRef = useRef(false)
    const nextStartTimeRef = useRef(0)

    // 初始化 AudioContext 和 AnalyserNode
    const initAudioContext = useCallback(() => {
        if (!audioContextRef.current) {
            audioContextRef.current = new AudioContext({ sampleRate })
            // 创建 AnalyserNode 用于可视化
            analyserRef.current = audioContextRef.current.createAnalyser()
            analyserRef.current.fftSize = 256
            analyserRef.current.connect(audioContextRef.current.destination)
        }
        return audioContextRef.current
    }, [sampleRate])

    // 播放单个音频块
    const playAudioChunk = useCallback(async (audioData: ArrayBuffer): Promise<number> => {
        const ctx = initAudioContext()

        if (ctx.state === 'suspended') {
            await ctx.resume()
        }

        // 使用智能格式检测的 pcmToFloat32（默认按 Int16 处理）
        const float32Data = pcmToFloat32(audioData)
        if (float32Data.length === 0) {
            return 0
        }

        const audioBuffer = ctx.createBuffer(1, float32Data.length, sampleRate)
        audioBuffer.copyToChannel(new Float32Array(float32Data), 0)

        const source = ctx.createBufferSource()
        source.buffer = audioBuffer

        // 连接到 AnalyserNode（用于可视化）
        if (analyserRef.current) {
            source.connect(analyserRef.current)
        } else {
            source.connect(ctx.destination)
        }

        // 计算开始时间，确保无缝衔接
        const currentTime = ctx.currentTime
        const startTime = Math.max(currentTime, nextStartTimeRef.current)

        source.start(startTime)
        nextStartTimeRef.current = startTime + audioBuffer.duration

        return audioBuffer.duration
    }, [initAudioContext, sampleRate])

    // 处理音频队列（Gapless Playback - Lookahead Scheduling）
    const processQueue = useCallback(async () => {
        if (isProcessingRef.current || audioQueueRef.current.length === 0) {
            return
        }

        isProcessingRef.current = true
        setState(prev => ({ ...prev, isPlaying: true }))
        onPlayStart?.()

        let lastEndTime = 0

        while (audioQueueRef.current.length > 0) {
            const item = audioQueueRef.current.shift()
            if (!item) break

            setState(prev => ({
                ...prev,
                currentText: item.text || prev.currentText,
                queueLength: audioQueueRef.current.length,
            }))

            if (item.audioData.byteLength > 0) {
                try {
                    await playAudioChunk(item.audioData)
                    lastEndTime = nextStartTimeRef.current
                } catch (error) {
                    console.error('[useAudioPlayer] 播放失败:', error)
                    onError?.(`播放失败: ${error}`)
                }
            }

            if (item.isFinal) {
                break
            }
        }

        // 等待所有已调度的音频播放完成
        const ctx = audioContextRef.current
        if (ctx && lastEndTime > ctx.currentTime) {
            const remainingTime = (lastEndTime - ctx.currentTime) * 1000
            await new Promise(resolve => setTimeout(resolve, remainingTime))
        }

        isProcessingRef.current = false

        // 检查等待期间是否有新音频入队
        if (audioQueueRef.current.length > 0) {
            processQueue()
        } else {
            setState(prev => ({ ...prev, isPlaying: false, queueLength: 0 }))
            onPlayEnd?.()
        }
    }, [playAudioChunk, onPlayStart, onPlayEnd, onError])

    // 添加 TTS 音频块到队列
    const enqueue = useCallback((chunk: TTSChunk) => {
        if (!chunk.audio_base64 && !chunk.is_final) {
            return
        }

        const audioData = chunk.audio_base64
            ? base64ToArrayBuffer(chunk.audio_base64)
            : new ArrayBuffer(0)

        audioQueueRef.current.push({
            audioData,
            text: chunk.text,
            isFinal: chunk.is_final || false,
        })

        setState(prev => ({ ...prev, queueLength: audioQueueRef.current.length }))

        // 自动开始处理队列
        if (!isProcessingRef.current) {
            processQueue()
        }
    }, [processQueue])

    // 清空队列并停止播放
    const stop = useCallback(() => {
        audioQueueRef.current = []
        isProcessingRef.current = false
        nextStartTimeRef.current = 0

        if (audioContextRef.current) {
            audioContextRef.current.close()
            audioContextRef.current = null
            analyserRef.current = null
        }

        setState({
            isPlaying: false,
            currentText: '',
            queueLength: 0,
        })
    }, [])

    // 暂停播放
    const pause = useCallback(async () => {
        if (audioContextRef.current && audioContextRef.current.state === 'running') {
            await audioContextRef.current.suspend()
            setState(prev => ({ ...prev, isPlaying: false }))
        }
    }, [])

    // 恢复播放
    const resume = useCallback(async () => {
        if (audioContextRef.current && audioContextRef.current.state === 'suspended') {
            await audioContextRef.current.resume()
            setState(prev => ({ ...prev, isPlaying: true }))
        }
    }, [])

    // 获取频谱数据（用于可视化）
    const getFrequencyData = useCallback((): Uint8Array | null => {
        if (!analyserRef.current) return null
        const dataArray = new Uint8Array(analyserRef.current.frequencyBinCount)
        analyserRef.current.getByteFrequencyData(dataArray)
        return dataArray
    }, [])

    // 获取波形数据（用于可视化）
    const getTimeDomainData = useCallback((): Uint8Array | null => {
        if (!analyserRef.current) return null
        const dataArray = new Uint8Array(analyserRef.current.frequencyBinCount)
        analyserRef.current.getByteTimeDomainData(dataArray)
        return dataArray
    }, [])

    // 清理
    useEffect(() => {
        return () => {
            if (audioContextRef.current) {
                audioContextRef.current.close()
            }
        }
    }, [])

    return {
        enqueue,
        stop,
        pause,
        resume,
        isPlaying: state.isPlaying,
        currentText: state.currentText,
        queueLength: state.queueLength,
        // 可视化支持
        analyserNode: analyserRef.current,
        getFrequencyData,
        getTimeDomainData,
    }
}

export default useAudioPlayer
