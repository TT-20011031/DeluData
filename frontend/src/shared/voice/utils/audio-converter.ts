/**
 * 音频格式转换工具
 * 
 * 注意：火山引擎/阿里云 TTS 标准返回 Int16 PCM 格式
 */

/**
 * Base64 解码为 ArrayBuffer
 */
export function base64ToArrayBuffer(base64: string): ArrayBuffer {
    const binaryString = atob(base64)
    const bytes = new Uint8Array(binaryString.length)
    for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i)
    }
    return bytes.buffer
}

/**
 * Int16 PCM 数据转 Float32（标准转换）
 * 
 * 火山引擎/阿里云 TTS 返回 Int16 PCM 数据
 * Web Audio API 需要 -1.0 到 1.0 的 Float32 格式
 */
export function int16ToFloat32(int16Data: ArrayBuffer): Float32Array {
    const int16Array = new Int16Array(int16Data)
    const float32Array = new Float32Array(int16Array.length)
    for (let i = 0; i < int16Array.length; i++) {
        // 16-bit 范围: -32768 到 32767，归一化到 -1.0 到 1.0
        float32Array[i] = int16Array[i] / 32768.0
    }
    return float32Array
}

/**
 * 智能 PCM 转 Float32（自动检测格式）
 * 
 * 尝试检测 PCM 数据是 Int16 还是 Float32 格式
 * 默认按 Int16 处理（火山引擎标准格式）
 */
export function pcmToFloat32(pcmData: ArrayBuffer): Float32Array {
    const byteLength = pcmData.byteLength

    // 检测策略：如果字节数是4的倍数，可能是 Float32
    if (byteLength % 4 === 0 && byteLength > 0) {
        const float32View = new Float32Array(pcmData)
        // 检查前几个样本是否在合理的 Float32 音频范围 [-1.0, 1.0]
        let looksLikeFloat32 = true
        const samplesToCheck = Math.min(20, float32View.length)
        for (let i = 0; i < samplesToCheck; i++) {
            const sample = float32View[i]
            // Float32 音频样本应该在 [-1, 1] 范围内
            // Int16 被错误解释为 Float32 会产生很大或很小的值
            if (!isFinite(sample) || Math.abs(sample) > 1.5) {
                looksLikeFloat32 = false
                break
            }
        }
        if (looksLikeFloat32 && float32View.length > 0) {
            // 额外检查：Float32 音频通常有合理的能量分布
            const avgAbsValue = float32View.reduce((sum, v) => sum + Math.abs(v), 0) / float32View.length
            if (avgAbsValue > 0.001 && avgAbsValue < 0.8) {
                return float32View
            }
        }
    }

    // 默认按 Int16 处理（火山引擎/阿里云标准格式）
    return int16ToFloat32(pcmData)
}
