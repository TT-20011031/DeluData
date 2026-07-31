import { openDB, type DBSchema, type IDBPDatabase } from 'idb'

const PREVIEW_CACHE_DB_NAME = 'deludata-preview-cache'
const PREVIEW_CACHE_DB_VERSION = 1
const PREVIEW_URL_STORE = 'preview_urls'
const DOCX_PAYLOAD_STORE = 'docx_payloads'
const PREVIEW_URL_SKEW_MS = 60_000
const MAX_PREVIEW_URL_RECORDS = 100
const MAX_DOCX_RECORDS_PER_USER = 3
const MAX_DOCX_BYTES_PER_USER = 100 * 1024 * 1024

type PreviewKind = 'raw' | 'download'

interface PreviewCacheDb extends DBSchema {
  preview_urls: {
    key: string
    value: PreviewUrlRecord
    indexes: {
      'by-user-scope': string
      'by-last-accessed-at': number
    }
  }
  docx_payloads: {
    key: string
    value: DocxPayloadRecord
    indexes: {
      'by-user-scope': string
      'by-last-accessed-at': number
    }
  }
}

export interface PreviewUrlRecord {
  cacheKey: string
  userScope: string
  fileId: string
  updatedAt: string
  kind: PreviewKind
  url: string
  requiresAuth: boolean
  expiresAt: string | null
  createdAt: number
  lastAccessedAt: number
}

export interface DocxPayloadRecord {
  cacheKey: string
  userScope: string
  fileId: string
  updatedAt: string
  fileSize: number
  payload: ArrayBuffer
  payloadBytes: number
  createdAt: number
  lastAccessedAt: number
}

interface PreviewUrlCacheParams {
  userId: string
  fileId: string
  updatedAt: string
  kind: PreviewKind
}

interface PreviewUrlWriteParams extends PreviewUrlCacheParams {
  url: string
  requiresAuth: boolean
  expiresAt: string | null
}

interface DocxPayloadCacheParams {
  userId: string
  fileId: string
  updatedAt: string
}

interface DocxPayloadWriteParams extends DocxPayloadCacheParams {
  fileSize: number
  payload: ArrayBuffer
}

type PreviewCacheDatabase = IDBPDatabase<PreviewCacheDb>

const dbPromise =
  typeof window !== 'undefined' && 'indexedDB' in window
    ? openDB<PreviewCacheDb>(PREVIEW_CACHE_DB_NAME, PREVIEW_CACHE_DB_VERSION, {
        upgrade(db) {
          if (!db.objectStoreNames.contains(PREVIEW_URL_STORE)) {
            const previewUrlStore = db.createObjectStore(PREVIEW_URL_STORE, {
              keyPath: 'cacheKey',
            })
            previewUrlStore.createIndex('by-user-scope', 'userScope')
            previewUrlStore.createIndex('by-last-accessed-at', 'lastAccessedAt')
          }

          if (!db.objectStoreNames.contains(DOCX_PAYLOAD_STORE)) {
            const docxPayloadStore = db.createObjectStore(DOCX_PAYLOAD_STORE, {
              keyPath: 'cacheKey',
            })
            docxPayloadStore.createIndex('by-user-scope', 'userScope')
            docxPayloadStore.createIndex('by-last-accessed-at', 'lastAccessedAt')
          }
        },
      }).catch(() => null)
    : Promise.resolve(null)

function buildUserScope(userId: string): string {
  return `user:${userId}`
}

function buildCacheKey(userId: string, fileId: string, updatedAt: string, kind: string): string {
  return `${buildUserScope(userId)}:${fileId}:${updatedAt}:${kind}`
}

function cloneArrayBuffer(buffer: ArrayBuffer): ArrayBuffer {
  return buffer.slice(0)
}

function parseExpiry(expiresAt: string | null): number | null {
  if (!expiresAt) return null
  const parsed = Date.parse(expiresAt)
  return Number.isFinite(parsed) ? parsed : null
}

function isPreviewUrlExpired(record: PreviewUrlRecord): boolean {
  const expiresAtMs = parseExpiry(record.expiresAt)
  if (expiresAtMs === null) {
    return false
  }
  return expiresAtMs <= Date.now() + PREVIEW_URL_SKEW_MS
}

async function getDb(): Promise<PreviewCacheDatabase | null> {
  try {
    return await dbPromise
  } catch {
    return null
  }
}

async function prunePreviewUrlStore(): Promise<void> {
  try {
    const db = await getDb()
    if (!db) return

    const tx = db.transaction(PREVIEW_URL_STORE, 'readwrite')
    const store = tx.objectStore(PREVIEW_URL_STORE)
    const allRecords = await store.getAll()
    const staleRecords = allRecords.filter(isPreviewUrlExpired)

    for (const record of staleRecords) {
      await store.delete(record.cacheKey)
    }

    const liveRecords = allRecords
      .filter((record) => !isPreviewUrlExpired(record))
      .sort((left, right) => right.lastAccessedAt - left.lastAccessedAt)

    for (const record of liveRecords.slice(MAX_PREVIEW_URL_RECORDS)) {
      await store.delete(record.cacheKey)
    }

    await tx.done
  } catch {
    return
  }
}

async function pruneDocxPayloadStore(userScope: string): Promise<void> {
  try {
    const db = await getDb()
    if (!db) return

    const tx = db.transaction(DOCX_PAYLOAD_STORE, 'readwrite')
    const index = tx.objectStore(DOCX_PAYLOAD_STORE).index('by-user-scope')
    const records = await index.getAll(userScope)
    const orderedRecords = records.sort((left, right) => right.lastAccessedAt - left.lastAccessedAt)

    let totalBytes = 0
    let keptCount = 0
    for (const record of orderedRecords) {
      const withinCountLimit = keptCount < MAX_DOCX_RECORDS_PER_USER
      const withinByteLimit = totalBytes + record.payloadBytes <= MAX_DOCX_BYTES_PER_USER
      if (withinCountLimit && withinByteLimit) {
        keptCount += 1
        totalBytes += record.payloadBytes
        continue
      }
      await tx.objectStore(DOCX_PAYLOAD_STORE).delete(record.cacheKey)
    }

    await tx.done
  } catch {
    return
  }
}

export async function getCachedPreviewUrl(params: PreviewUrlCacheParams): Promise<PreviewUrlRecord | null> {
  try {
    const db = await getDb()
    if (!db) return null

    const cacheKey = buildCacheKey(params.userId, params.fileId, params.updatedAt, params.kind)
    const tx = db.transaction(PREVIEW_URL_STORE, 'readwrite')
    const store = tx.objectStore(PREVIEW_URL_STORE)
    const record = await store.get(cacheKey)
    if (!record) {
      await tx.done
      return null
    }

    if (isPreviewUrlExpired(record)) {
      await store.delete(cacheKey)
      await tx.done
      return null
    }

    const touchedRecord: PreviewUrlRecord = {
      ...record,
      lastAccessedAt: Date.now(),
    }
    await store.put(touchedRecord)
    await tx.done
    return touchedRecord
  } catch {
    return null
  }
}

export async function setCachedPreviewUrl(params: PreviewUrlWriteParams): Promise<void> {
  try {
    const db = await getDb()
    if (!db) return

    const now = Date.now()
    const record: PreviewUrlRecord = {
      cacheKey: buildCacheKey(params.userId, params.fileId, params.updatedAt, params.kind),
      userScope: buildUserScope(params.userId),
      fileId: params.fileId,
      updatedAt: params.updatedAt,
      kind: params.kind,
      url: params.url,
      requiresAuth: params.requiresAuth,
      expiresAt: params.expiresAt,
      createdAt: now,
      lastAccessedAt: now,
    }

    await db.put(PREVIEW_URL_STORE, record)
    await prunePreviewUrlStore()
  } catch {
    return
  }
}

export async function getCachedDocxPayload(params: DocxPayloadCacheParams): Promise<DocxPayloadRecord | null> {
  try {
    const db = await getDb()
    if (!db) return null

    const cacheKey = buildCacheKey(params.userId, params.fileId, params.updatedAt, 'docx')
    const tx = db.transaction(DOCX_PAYLOAD_STORE, 'readwrite')
    const store = tx.objectStore(DOCX_PAYLOAD_STORE)
    const record = await store.get(cacheKey)
    if (!record) {
      await tx.done
      return null
    }

    const touchedRecord: DocxPayloadRecord = {
      ...record,
      payload: cloneArrayBuffer(record.payload),
      lastAccessedAt: Date.now(),
    }
    await store.put(touchedRecord)
    await tx.done
    return touchedRecord
  } catch {
    return null
  }
}

export async function setCachedDocxPayload(params: DocxPayloadWriteParams): Promise<void> {
  try {
    const db = await getDb()
    if (!db) return

    const payloadBytes = params.payload.byteLength
    if (payloadBytes > MAX_DOCX_BYTES_PER_USER) {
      return
    }

    const now = Date.now()
    const record: DocxPayloadRecord = {
      cacheKey: buildCacheKey(params.userId, params.fileId, params.updatedAt, 'docx'),
      userScope: buildUserScope(params.userId),
      fileId: params.fileId,
      updatedAt: params.updatedAt,
      fileSize: params.fileSize,
      payload: cloneArrayBuffer(params.payload),
      payloadBytes,
      createdAt: now,
      lastAccessedAt: now,
    }

    await db.put(DOCX_PAYLOAD_STORE, record)
    await pruneDocxPayloadStore(buildUserScope(params.userId))
  } catch {
    return
  }
}

export async function deleteCachedDocxPayload(params: DocxPayloadCacheParams): Promise<void> {
  try {
    const db = await getDb()
    if (!db) return

    const cacheKey = buildCacheKey(params.userId, params.fileId, params.updatedAt, 'docx')
    await db.delete(DOCX_PAYLOAD_STORE, cacheKey)
  } catch {
    return
  }
}

export async function clearPreviewCacheForUser(userId: string): Promise<void> {
  try {
    const db = await getDb()
    if (!db) return

    const userScope = buildUserScope(userId)
    const tx = db.transaction([PREVIEW_URL_STORE, DOCX_PAYLOAD_STORE], 'readwrite')
    const previewStore = tx.objectStore(PREVIEW_URL_STORE)
    const docxStore = tx.objectStore(DOCX_PAYLOAD_STORE)

    const previewKeys = await previewStore.index('by-user-scope').getAllKeys(userScope)
    for (const key of previewKeys) {
      await previewStore.delete(key)
    }

    const docxKeys = await docxStore.index('by-user-scope').getAllKeys(userScope)
    for (const key of docxKeys) {
      await docxStore.delete(key)
    }

    await tx.done
  } catch {
    return
  }
}
