import { API_BASE_URL } from '@/config'
import { getAuthHeader } from '@/stores/authStore'
import {
  deleteCachedDocxPayload,
  getCachedDocxPayload,
  setCachedDocxPayload,
} from '@/lib/previewCache'

export interface PreviewFileInfo {
  id: string
  name: string
  file_type: string
  file_size: number
  status: string
  preview_policy?: string | null
  updated_at?: string | null
}

export interface FileAccessUrl {
  url: string
  requires_auth: boolean
  kind: 'raw' | 'download'
  expires_at?: string | null
}

interface PdfPreviewAccessParams {
  userId?: string | null
  fileId: string
  fileInfo: Pick<PreviewFileInfo, 'updated_at'>
}

interface DocxPreviewBufferParams {
  userId?: string | null
  fileId: string
  fileInfo: Pick<PreviewFileInfo, 'file_type' | 'file_size' | 'updated_at'>
}

export interface DocxPreviewBufferResult {
  arrayBuffer: ArrayBuffer
  fromCache: boolean
}

function normalizeSameHostHttpsUrl(url: string): string {
  const location = typeof window === 'undefined' ? null : window.location
  if (!url || !location || location.protocol !== 'https:') {
    return url
  }

  try {
    const resolvedUrl = new URL(url, location.origin)
    if (resolvedUrl.protocol === 'http:' && resolvedUrl.host === location.host) {
      resolvedUrl.protocol = 'https:'
      return resolvedUrl.toString()
    }
  } catch {
    return url
  }

  return url
}

function getVersionToken(updatedAt?: string | null): string | null {
  const normalized = updatedAt?.trim()
  return normalized ? normalized : null
}

async function parseErrorMessage(response: Response, fallback: string): Promise<string> {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    const payload = await response.json().catch(() => ({}))
    return payload?.detail || payload?.message || fallback
  }
  const text = await response.text().catch(() => '')
  return text || fallback
}

async function fetchPreviewResource(url: string, requiresAuth: boolean): Promise<ArrayBuffer> {
  const response = await fetch(normalizeSameHostHttpsUrl(url), {
    headers: requiresAuth ? getAuthHeader() : undefined,
  })
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, '无法加载预览文件'))
  }
  return response.arrayBuffer()
}

export async function fetchPreviewFileInfo(fileId: string): Promise<PreviewFileInfo> {
  const response = await fetch(`${API_BASE_URL}/knowledge/files/${fileId}/info`, {
    headers: getAuthHeader(),
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, '无法获取文件信息'))
  }

  return response.json()
}

export async function fetchFileAccessUrl(
  fileId: string,
  kind: 'raw' | 'download',
  inline = true,
): Promise<FileAccessUrl> {
  const response = await fetch(
    `${API_BASE_URL}/knowledge/files/${fileId}/access-url?kind=${kind}&inline=${inline ? 'true' : 'false'}`,
    {
      headers: getAuthHeader(),
    },
  )

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, '无法获取文件访问地址'))
  }

  const payload: FileAccessUrl = await response.json()
  return {
    ...payload,
    url: normalizeSameHostHttpsUrl(payload.url),
  }
}

export async function resolvePdfPreviewAccess(
  params: PdfPreviewAccessParams,
): Promise<FileAccessUrl> {
  // PDF.js fetches the document from the browser. Always use a fresh same-origin
  // backend URL so OSS CORS/signature redirects cannot break preview loading.
  const access = await fetchFileAccessUrl(params.fileId, 'raw', true)
  return access
}

export async function resolveDocxPreviewBuffer(
  params: DocxPreviewBufferParams,
): Promise<DocxPreviewBufferResult> {
  const normalizedFileType = (params.fileInfo.file_type || '').toLowerCase()
  const versionToken = getVersionToken(params.fileInfo.updated_at)
  const shouldCachePayload = normalizedFileType === 'docx' && !!params.userId && !!versionToken

  if (shouldCachePayload) {
    const cachedEntry = await getCachedDocxPayload({
      userId: params.userId as string,
      fileId: params.fileId,
      updatedAt: versionToken as string,
    })
    if (cachedEntry) {
      return {
        arrayBuffer: cachedEntry.payload.slice(0),
        fromCache: true,
      }
    }
  }

  const access = await fetchFileAccessUrl(params.fileId, 'raw', true)
  const arrayBuffer = await fetchPreviewResource(access.url, access.requires_auth)

  if (shouldCachePayload) {
    await setCachedDocxPayload({
      userId: params.userId as string,
      fileId: params.fileId,
      updatedAt: versionToken as string,
      fileSize: params.fileInfo.file_size || arrayBuffer.byteLength,
      payload: arrayBuffer,
    })
  }

  return {
    arrayBuffer,
    fromCache: false,
  }
}

export async function recoverDocxPreviewBuffer(
  params: DocxPreviewBufferParams,
): Promise<DocxPreviewBufferResult> {
  const versionToken = getVersionToken(params.fileInfo.updated_at)
  if (params.userId && versionToken) {
    await deleteCachedDocxPayload({
      userId: params.userId,
      fileId: params.fileId,
      updatedAt: versionToken,
    })
  }
  return resolveDocxPreviewBuffer(params)
}
