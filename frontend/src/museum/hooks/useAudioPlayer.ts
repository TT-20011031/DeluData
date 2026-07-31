/**
 * 博物馆模块 - 音频播放器 Hook
 * 
 * 兼容性模块：导出公用模块的 useAudioPlayer
 * 
 * [Zero Tech Debt] 改用公用模块实现
 */

export { useAudioPlayer, default } from '@/shared/voice/hooks/useAudioPlayer'
export type { TTSChunk, AudioPlayerOptions, AudioPlayerState } from '@/shared/voice/types'
