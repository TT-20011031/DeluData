/**
 * UUID 生成工具
 * 
 * 兼容非安全上下文（HTTP），优先使用 crypto.randomUUID()，
 * 降级到 crypto.getRandomValues() 手动拼接 RFC 4122 v4
 */

export function generateId(): string {
    // 安全上下文（HTTPS）下使用原生 API
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID()
    }
    // 降级：手动拼接 UUID v4
    const bytes = new Uint8Array(16)
    crypto.getRandomValues(bytes)
    bytes[6] = (bytes[6] & 0x0f) | 0x40  // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80  // variant 10
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
