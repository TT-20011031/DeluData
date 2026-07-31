/**
 * 语音公用模块
 * 
 * 提供跨项目共享的语音功能：
 * - 录音 (VoiceRecorder)
 * - 播放 (AudioPlayer)
 * - Web Audio API Hooks
 */

// Types
export type {
    TTSChunk,
    ASRResult,
    AudioPlayerOptions,
    AudioPlayerState,
    VoiceRecorderOptions,
    VoiceRecorderState,
} from './types'

// Hooks
export { useAudioPlayer } from './hooks/useAudioPlayer'
export { useVoiceRecorder } from './hooks/useVoiceRecorder'

// Components
export { VoiceRecorder } from './components/VoiceRecorder'
export { AudioPlayer } from './components/AudioPlayer'

// Utils
export {
    base64ToArrayBuffer,
    int16ToFloat32,
    pcmToFloat32,
} from './utils/audio-converter'
