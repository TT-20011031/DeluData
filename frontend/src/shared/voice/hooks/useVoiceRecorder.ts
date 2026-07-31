/**
 * 语音录制器 Hook
 * 
 * 封装麦克风录制和 ASR 识别逻辑，支持：
 * - 按住录音、松开发送
 * - 上滑取消
 * - 自动调用 ASR API
 * 
 * 【修复】使用 authStore 获取 token，而非 localStorage
 */

import { useCallback, useRef, useState } from 'react'
import type { VoiceRecorderOptions, VoiceRecorderState } from '../types'
import { useAuthStore } from '@/stores/authStore'

const DEFAULT_API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

export function useVoiceRecorder(options: VoiceRecorderOptions = {}) {
    const {
        sampleRate = 16000,
        transcribeEndpoint = `${DEFAULT_API_BASE}/voice/transcribe`,
        minDuration = 500,
        onTranscribe,
        onError,
        onRecordingChange,
    } = options

    const [state, setState] = useState<VoiceRecorderState>({
        isRecording: false,
        isTranscribing: false,
        isCancelling: false,
    })

    const mediaRecorderRef = useRef<MediaRecorder | null>(null)
    const audioChunksRef = useRef<Blob[]>([])
    const streamRef = useRef<MediaStream | null>(null)
    const startTimeRef = useRef<number>(0)
    const startYRef = useRef<number>(0)
    const cancelRef = useRef(false)

    // 调用 ASR API
    const transcribeAudio = useCallback(async (audioBlob: Blob): Promise<string> => {
        const formData = new FormData()
        formData.append('audio', audioBlob, 'recording.webm')

        // 【修复】使用 authStore 获取 token
        const token = useAuthStore.getState().token
        const headers: Record<string, string> = {}
        if (token) {
            headers['Authorization'] = `Bearer ${token}`
        }

        const response = await fetch(transcribeEndpoint, {
            method: 'POST',
            body: formData,
            headers,
        })

        const result = await response.json().catch(() => ({}))

        if (!response.ok) {
            throw new Error(result.detail || result.msg || `语音识别失败: ${response.status}`)
        }

        if (result.code !== 0) {
            throw new Error(result.msg || '语音识别失败')
        }

        return result.text
    }, [transcribeEndpoint])

    // 开始录音
    const startRecording = useCallback(async (clientY?: number) => {
        if (state.isRecording || state.isTranscribing) return

        if (clientY !== undefined) {
            startYRef.current = clientY
        }
        startTimeRef.current = Date.now()
        cancelRef.current = false

        try {
            console.log('🎤 [VoiceRecorder] 请求麦克风权限...')
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                }
            })

            streamRef.current = stream

            const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus'
                : 'audio/webm'

            const mediaRecorder = new MediaRecorder(stream, { mimeType })
            mediaRecorderRef.current = mediaRecorder
            audioChunksRef.current = []

            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    audioChunksRef.current.push(event.data)
                }
            }

            mediaRecorder.onstop = async () => {
                // 清理音频流
                stream.getTracks().forEach(track => track.stop())
                streamRef.current = null

                // 检查录音时长
                const duration = Date.now() - startTimeRef.current

                // 如果是取消状态或时长太短，不处理
                if (cancelRef.current || duration < minDuration) {
                    setState(prev => ({ ...prev, isCancelling: false }))
                    console.log('🎤 [VoiceRecorder] 录音已取消或太短')
                    if (!cancelRef.current && duration < minDuration) {
                        onError?.('录音时间太短，请重新录制')
                    }
                    cancelRef.current = false
                    return
                }

                const audioBlob = new Blob(audioChunksRef.current, { type: mimeType })
                console.log('🎤 [VoiceRecorder] 音频大小:', audioBlob.size, 'bytes')

                if (audioBlob.size < 1000) {
                    console.log('🎤 [VoiceRecorder] 录音太短，忽略')
                    return
                }

                try {
                    setState(prev => ({ ...prev, isTranscribing: true }))
                    const text = await transcribeAudio(audioBlob)
                    if (text) {
                        console.log('🎤 [VoiceRecorder] 识别成功:', text)
                        onTranscribe?.(text)
                    }
                } catch (error) {
                    console.error('🎤 [VoiceRecorder] 识别失败:', error)
                    onError?.(error instanceof Error ? error.message : '语音识别失败')
                } finally {
                    setState(prev => ({ ...prev, isTranscribing: false }))
                }
            }

            mediaRecorder.start(100)
            setState(prev => ({ ...prev, isRecording: true }))
            onRecordingChange?.(true)
            console.log('🎤 [VoiceRecorder] 开始录音')

        } catch (error) {
            console.error('🎤 [VoiceRecorder] 录音失败:', error)
            onError?.('无法访问麦克风，请检查权限设置')
        }
    }, [state.isRecording, state.isTranscribing, sampleRate, minDuration, transcribeAudio, onTranscribe, onError, onRecordingChange])

    // 停止录音
    const stopRecording = useCallback((cancel: boolean = false) => {
        if (!mediaRecorderRef.current || mediaRecorderRef.current.state === 'inactive') {
            return
        }

        console.log('🎤 [VoiceRecorder] 停止录音, 取消:', cancel)

        cancelRef.current = cancel
        if (cancel) {
            setState(prev => ({ ...prev, isCancelling: true }))
        }

        setState(prev => ({ ...prev, isRecording: false }))
        onRecordingChange?.(false)
        mediaRecorderRef.current.stop()
    }, [onRecordingChange])

    // 更新取消状态（根据手势位置）
    const updateCancelState = useCallback((clientY: number) => {
        if (!state.isRecording) return

        // 上滑超过 80px 进入取消状态
        const deltaY = startYRef.current - clientY
        const shouldCancel = deltaY > 80

        if (shouldCancel !== state.isCancelling) {
            cancelRef.current = shouldCancel
            setState(prev => ({ ...prev, isCancelling: shouldCancel }))
        }
    }, [state.isRecording, state.isCancelling])

    return {
        ...state,
        startRecording,
        stopRecording,
        updateCancelState,
    }
}

export default useVoiceRecorder
