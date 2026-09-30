import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

function buildJsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}

describe('knowledgePreviewService', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.doUnmock('idb')
    vi.doUnmock('@/lib/previewCache')
    vi.unstubAllGlobals()
  })

  it('reuses cached DOCX payload without calling /access-url again', async () => {
    const payload = new Uint8Array([1, 2, 3, 4]).buffer
    const getCachedDocxPayload = vi
      .fn()
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({
        cacheKey: 'user:user_1:file_1:2026-03-11T12:00:00Z:docx',
        userScope: 'user:user_1',
        fileId: 'file_1',
        updatedAt: '2026-03-11T12:00:00Z',
        fileSize: payload.byteLength,
        payload: payload.slice(0),
        payloadBytes: payload.byteLength,
        createdAt: 1,
        lastAccessedAt: 1,
      })
    const setCachedDocxPayload = vi.fn().mockResolvedValue(undefined)

    vi.doMock('@/lib/previewCache', () => ({
      deleteCachedDocxPayload: vi.fn().mockResolvedValue(undefined),
      getCachedDocxPayload,
      getCachedPreviewUrl: vi.fn().mockResolvedValue(null),
      setCachedDocxPayload,
      setCachedPreviewUrl: vi.fn().mockResolvedValue(undefined),
    }))

    const signedUrl = 'https://oss.example.com/demo.docx'
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        buildJsonResponse({
          url: signedUrl,
          requires_auth: false,
          kind: 'raw',
          expires_at: '2026-03-11T12:30:00Z',
        }),
      )
      .mockResolvedValueOnce(new Response(payload, { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const { resolveDocxPreviewBuffer } = await import('@/services/knowledgePreviewService')
    const params = {
      userId: 'user_1',
      fileId: 'file_1',
      fileInfo: {
        file_type: 'docx',
        file_size: payload.byteLength,
        updated_at: '2026-03-11T12:00:00Z',
      },
    } as const

    const first = await resolveDocxPreviewBuffer(params)
    expect(first.fromCache).toBe(false)
    expect(new Uint8Array(first.arrayBuffer)).toEqual(new Uint8Array(payload))
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(setCachedDocxPayload).toHaveBeenCalledTimes(1)

    const second = await resolveDocxPreviewBuffer(params)
    expect(second.fromCache).toBe(true)
    expect(new Uint8Array(second.arrayBuffer)).toEqual(new Uint8Array(payload))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('falls back to network when IndexedDB initialization fails', async () => {
    vi.stubGlobal('window', { indexedDB: {} })

    vi.doMock('idb', () => ({
      openDB: vi.fn(async () => {
        throw new Error('IndexedDB unavailable')
      }),
    }))

    const accessPayload = {
      url: 'https://oss.example.com/demo.pdf',
      requires_auth: false,
      kind: 'raw',
      expires_at: '2026-03-11T12:30:00Z',
    } as const
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(buildJsonResponse(accessPayload))
    vi.stubGlobal('fetch', fetchMock)

    const { resolvePdfPreviewAccess } = await import('@/services/knowledgePreviewService')
    const result = await resolvePdfPreviewAccess({
      userId: 'user_1',
      fileId: 'file_1',
      fileInfo: {
        updated_at: '2026-03-11T12:00:00Z',
      },
    })

    expect(result).toEqual(accessPayload)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('fetches a fresh PDF URL and upgrades same-host http to https on secure pages', async () => {
    vi.stubGlobal('window', {
      location: {
        protocol: 'https:',
        host: 'agent.deluagent.com',
        origin: 'https://agent.deluagent.com',
      },
    })

    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(buildJsonResponse({
      url: 'http://agent.deluagent.com/api/knowledge/files/file_1/raw',
      requires_auth: true,
      kind: 'raw',
      expires_at: null,
    }))
    vi.stubGlobal('fetch', fetchMock)

    const { resolvePdfPreviewAccess } = await import('@/services/knowledgePreviewService')
    const result = await resolvePdfPreviewAccess({
      userId: 'user_1',
      fileId: 'file_1',
      fileInfo: {
        updated_at: '2026-03-11T12:00:00Z',
      },
    })

    expect(result).toEqual({
      url: 'https://agent.deluagent.com/api/knowledge/files/file_1/raw',
      requires_auth: true,
      kind: 'raw',
      expires_at: null,
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
