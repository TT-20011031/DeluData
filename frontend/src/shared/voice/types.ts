/**
 * 语音模块类型定义
 */

/**
 * TTS 音频块
 */
export interface TTSChunk {
    audio_base64?: string
    text?: string
    is_final?: boolean
    error_code?: number
    error_message?: string
}

/**
 * ASR 识别结果
 */
export interface ASRResult {
    code: number
    text: string
    msg: string
    error_detail?: string
}

/**
 * 音频播放器选项
 */
export interface AudioPlayerOptions {
    /** 采样率 (默认 24000) */
    sampleRate?: number
    /** 开始播放回调 */
    onPlayStart?: () => void
    /** 播放结束回调 */
    onPlayEnd?: () => void
    /** 错误回调 */
    onError?: (error: string) => void
}

/**
 * 音频播放器状态
 */
export interface AudioPlayerState {
    isPlaying: boolean
    currentText: string
    queueLength: number
}

/**
 * 语音录制器选项
 */
export interface VoiceRecorderOptions {
    /** 采样率 (默认 16000) */
    sampleRate?: number
    /** ASR API 端点 */
    transcribeEndpoint?: string
    /** 最小录音时长 (毫秒，默认 500) */
    minDuration?: number
    /** 识别成功回调 */
    onTranscribe?: (text: string) => void
    /** 错误回调 */
    onError?: (error: string) => void
    /** 录音状态变化回调 */
    onRecordingChange?: (isRecording: boolean) => void
}

/**
 * 语音录制器状态
 */
export interface VoiceRecorderState {
    isRecording: boolean
    isTranscribing: boolean
    isCancelling: boolean
}
