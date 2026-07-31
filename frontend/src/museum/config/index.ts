/**
 * 博物馆模块 - 统一配置
 * 
 * 遵循 DRY 原则，避免重复定义
 */

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

export const MUSEUM_API = {
    guide: `${API_BASE_URL}/museum/events/guide`,
    upload: `${API_BASE_URL}/museum/guide/upload`,
    images: `${API_BASE_URL}/museum/guide/images`,
    shop: `${API_BASE_URL}/museum/shop`,
    knowledge: `${API_BASE_URL}/knowledge/images`,
}

export function resolveMuseumAssetUrl(url?: string | null): string | null {
    const raw = typeof url === 'string' ? url.trim() : ''
    if (!raw) return null

    if (/^(?:https?:|data:|blob:)/i.test(raw)) {
        return raw
    }

    const apiBase = API_BASE_URL.replace(/\/+$/, '')
    const isAbsoluteApiBase = /^https?:\/\//i.test(apiBase)

    if (raw.startsWith('/')) {
        if (isAbsoluteApiBase) {
            const origin = new URL(apiBase).origin
            if (raw.startsWith('/api/') || raw.startsWith('/static/')) {
                return `${origin}${raw}`
            }
            return `${apiBase}${raw}`
        }

        if (raw === apiBase || raw.startsWith(`${apiBase}/`)) {
            return raw
        }

        return `${apiBase}${raw}`
    }

    return `${apiBase}/${raw.replace(/^\/+/, '')}`
}

export function normalizeMuseumImageUrls(value: unknown): string[] {
    let urls: string[] = []

    if (Array.isArray(value)) {
        urls = value.filter((item): item is string => typeof item === 'string')
    } else if (typeof value === 'string') {
        const raw = value.trim()
        if (!raw) return []

        if (raw.startsWith('[')) {
            try {
                const parsed = JSON.parse(raw)
                if (Array.isArray(parsed)) {
                    urls = parsed.filter((item): item is string => typeof item === 'string')
                } else {
                    urls = [raw]
                }
            } catch {
                urls = [raw]
            }
        } else {
            urls = [raw]
        }
    }

    return urls
        .map((item) => resolveMuseumAssetUrl(item))
        .filter((item): item is string => Boolean(item))
}

export function getMuseumPrimaryImageUrl(value: unknown): string | null {
    return normalizeMuseumImageUrls(value)[0] ?? null
}
