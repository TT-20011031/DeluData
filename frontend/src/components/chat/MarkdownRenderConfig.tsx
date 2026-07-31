/**
 * Markdown 渲染配置
 * 
 * 提取 ReactMarkdown 的 components 配置对象
 * 包含自定义的 Image、Citation、DataFrame 渲染逻辑
 * 
 * [v2.5] 图片策略变更：
 * - 图片引用 [[IMAGE:...]] 在正文中被移除
 * - 所有图片统一收集，由 ChatMessage 在末尾用 ImageCarousel 展示
 */
import React from 'react'
import { BookOpen, Table2 } from 'lucide-react'
import { CodeBlock } from '@/components/markdown/CodeBlock'
import { Hr, Table, Thead, Th, Td, H1, H2, H3, Ul, Ol, Li, Blockquote, A } from '@/components/markdown/MarkdownElements'
import { FileTypeIcon } from '@/components/knowledge/FileTypeIcons'

// 回调函数类型
export interface MarkdownCallbacks {
    onPreviewFile: (fileId: string, fileName: string, fileType: string, page?: number, anchor?: string) => void
    onPreviewDataFrame: (dfKey: string) => void
    onOpenWikiSlug?: (slug: string) => void
}

/**
 * 从文件名提取文件类型
 * 
 * [修复] 正确处理没有扩展名的文件名
 */
function getFileTypeFromName(fileName: string): string {
    if (!fileName.includes('.')) return 'unknown'
    const ext = fileName.split('.').pop()?.toLowerCase() || ''
    // 验证是否为已知扩展名
    const knownTypes = ['pdf', 'docx', 'doc', 'txt', 'md', 'xlsx', 'xls', 'pptx', 'ppt']
    return knownTypes.includes(ext) ? ext : 'unknown'
}

interface ParsedCitationLink {
    fileId: string
    page?: number
    anchor?: string
}

function parseCitationHref(href: string): ParsedCitationLink | null {
    const parsePage = (raw: string | null): number | undefined => {
        if (!raw) return undefined
        const parsed = Number.parseInt(raw, 10)
        return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined
    }

    const parsePageFromParams = (params: URLSearchParams): number | undefined => {
        for (const key of ['page', 'page_number', 'p']) {
            const page = parsePage(params.get(key))
            if (page) return page
        }
        return undefined
    }

    const parseAnchorFromParams = (params: URLSearchParams): string | undefined => {
        for (const key of ['anchor', 'snippet', 'q']) {
            const value = (params.get(key) || '').trim()
            if (value) return value
        }
        return undefined
    }

    const parseSlot = (rawSlot: string): ParsedCitationLink | null => {
        const decoded = decodeURIComponent(rawSlot || '').trim()
        if (!decoded) return null
        const [rawFileId, embeddedQuery = ''] = decoded.split('?', 2)
        const fileId = rawFileId.trim()
        if (!fileId) return null
        const query = new URLSearchParams(embeddedQuery)
        const page = parsePageFromParams(query)
        const anchor = parseAnchorFromParams(query)
        return { fileId, page, anchor }
    }

    try {
        const parsedUrl = new URL(href, 'http://citation.local')
        if (parsedUrl.hostname === 'citation') {
            const slot = parsedUrl.pathname.replace(/^\/+/, '')
            const parsedSlot = parseSlot(slot)
            if (!parsedSlot) return null
            const page = parsePageFromParams(parsedUrl.searchParams) ?? parsedSlot.page
            const anchor = parseAnchorFromParams(parsedUrl.searchParams) ?? parsedSlot.anchor
            return { fileId: parsedSlot.fileId, page, anchor }
        }
        if (parsedUrl.pathname.startsWith('/citation/')) {
            const slot = parsedUrl.pathname.replace(/^\/citation\//, '')
            const parsedSlot = parseSlot(slot)
            if (!parsedSlot) return null
            const page = parsePageFromParams(parsedUrl.searchParams) ?? parsedSlot.page
            const anchor = parseAnchorFromParams(parsedUrl.searchParams) ?? parsedSlot.anchor
            return { fileId: parsedSlot.fileId, page, anchor }
        }
    } catch {
        // 回退到正则解析
    }

    const match = href.match(/citation\/([^/?#]+)(?:\?([^#]+))?/)
    if (!match) return null

    const parsedSlot = parseSlot(match[1] || '')
    if (!parsedSlot) return null

    const query = new URLSearchParams(match[2] || '')
    const page = parsePageFromParams(query) ?? parsedSlot.page
    const anchor = parseAnchorFromParams(query) ?? parsedSlot.anchor
    return { fileId: parsedSlot.fileId, page, anchor }
}

/**
 * 图片信息接口 (与 ImageCarousel 共用)
 */
export interface ExtractedImage {
    fileId: string
    imageId: string
    caption: string
}

/**
 * 从消息内容中提取所有图片引用
 * 
 * @param content - 原始消息内容
 * @returns 提取的图片信息列表
 */
export function extractImagesFromContent(content: string): ExtractedImage[] {
    const imagePattern = /\[\[(IMAGE|IMG):([^:\]]+):([^:\]]+)(?::([^\]]*))?\]\]/g
    const images: ExtractedImage[] = []
    let match

    while ((match = imagePattern.exec(content)) !== null) {
        const [, _tag, fileId, imageId, caption = ''] = match
        // 去重：检查是否已存在相同 fileId + imageId
        const exists = images.some(img => img.fileId === fileId && img.imageId === imageId)
        if (!exists) {
            images.push({ fileId, imageId, caption })
        }
    }

    return images
}

/**
 * 从消息内容中移除图片引用标记
 * 
 * @param content - 原始消息内容
 * @returns 移除图片标记后的内容
 */
export function removeImageTags(content: string): string {
    // 移除 [[IMAGE:...]] 和 [[IMG:...]] 标记
    return content
        .replace(/\[\[(IMAGE|IMG):[^\]]+\]\]/g, '')
        // 清理因移除图片产生的多余空行
        .replace(/\n{3,}/g, '\n\n')
        .trim()
}

/**
 * 创建 Markdown 渲染组件配置
 * 
 * [v2.5] 图片引用不再在正文中渲染，改为末尾 Carousel 展示
 * 
 * @param callbacks - 回调函数对象
 * @returns ReactMarkdown 的 components 配置
 */
export function createMarkdownComponents(callbacks: MarkdownCallbacks) {
    const { onPreviewFile, onPreviewDataFrame, onOpenWikiSlug } = callbacks

    return {
        code: CodeBlock,
        hr: Hr,
        table: Table,
        thead: Thead,
        th: Th,
        td: Td,
        h1: H1,
        h2: H2,
        h3: H3,
        ul: Ul,
        ol: Ol,
        li: Li,
        blockquote: Blockquote,

        // [v2.5] 简化 p 标签处理 - 不再处理图片引用（已在预处理阶段移除）
        p: ({ children }: { children?: React.ReactNode }) => {
            return <div className="mb-2 last:mb-0 leading-relaxed break-words">{children}</div>
        },

        // 链接处理：拦截 citation 和 dataframe 链接
        a: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { children?: React.ReactNode }) => {
            // 拦截 http://citation/ 协议的链接
            if (href?.includes('citation/')) {
                const parsedCitation = parseCitationHref(href)
                const fileId = parsedCitation?.fileId || ''
                const page = parsedCitation?.page
                const anchor = parsedCitation?.anchor
                const fileName = String(children)
                const pageLabel = typeof page === 'number' && page > 0 ? ` · P${page}` : ''
                const displayLabel = `${fileName}${pageLabel}`

                if (fileId) {
                    // [修复] 如果 fileId 以 df_ 开头，说明是 DataFrame 引用，用 DataFramePreview
                    if (fileId.startsWith('df_')) {
                        return (
                            <span
                                className="inline-flex items-center gap-1 text-accent cursor-pointer hover:underline"
                                title={`查看数据: ${fileName}`}
                                onClick={(e) => {
                                    e.preventDefault()
                                    e.stopPropagation()
                                    onPreviewDataFrame(fileId)
                                }}
                            >
                                <Table2 className="h-3 w-3" />
                                {children}
                            </span>
                        )
                    }
                    // 否则是知识库文件引用
                    const fileType = getFileTypeFromName(fileName)
                    return (
                        <span
                            className="inline-flex items-center justify-center px-2 py-0.5 bg-manus-secondary/80 hover:bg-manus-tertiary rounded-md cursor-pointer transition-all mx-0.5 align-middle border border-manus-border/50 hover:border-accent/50 hover:shadow-sm gap-1.5 group"
                            title={`来源: ${fileName}${pageLabel}`}
                            onClick={(e) => {
                                e.preventDefault()
                                e.stopPropagation()
                                onPreviewFile(fileId, fileName, fileType, page, anchor)
                            }}
                        >
                            <FileTypeIcon type={fileType} size={14} />
                            <span className="text-xs text-manus-subtle group-hover:text-manus-text max-w-[120px] truncate">
                                {displayLabel}
                            </span>
                        </span>
                    )
                }
            }

            // 拦截 DataFrame 引用链接 (格式: http://dataframe/df_xxx)
            if (href?.includes('dataframe/')) {
                const match = href.match(/dataframe\/(df_[a-zA-Z0-9_]+)/)
                const dfKey = match ? match[1] : ''
                if (dfKey) {
                    return (
                        <span
                            className="inline-flex items-center gap-1 text-accent cursor-pointer hover:underline"
                            title={`查看数据: ${dfKey}`}
                            onClick={(e) => {
                                e.preventDefault()
                                e.stopPropagation()
                                onPreviewDataFrame(dfKey)
                            }}
                        >
                            <Table2 className="h-3 w-3" />
                            {children}
                        </span>
                    )
                }
            }

            if (href?.includes('wiki/')) {
                const match = href.match(/wiki\/([^?#]+)/)
                const slug = match ? decodeURIComponent(match[1] || '').replace(/^\/+/, '') : ''
                if (slug) {
                    return (
                        <span
                            className="inline-flex items-center justify-center px-2 py-0.5 bg-emerald-500/10 hover:bg-emerald-500/15 text-emerald-700 rounded-md cursor-pointer transition-all mx-0.5 align-middle border border-emerald-500/20 hover:border-emerald-500/40 gap-1.5"
                            title={`查看 Wiki: ${String(children)}`}
                            onClick={(e) => {
                                e.preventDefault()
                                e.stopPropagation()
                                if (onOpenWikiSlug) {
                                    onOpenWikiSlug(slug)
                                } else {
                                    window.location.href = `/knowledge?view=wiki&wiki_slug=${encodeURIComponent(slug)}`
                                }
                            }}
                        >
                            <BookOpen className="h-3.5 w-3.5" />
                            <span className="text-xs max-w-[140px] truncate">{children}</span>
                        </span>
                    )
                }
            }

            return <A href={href} {...props}>{children}</A>
        }
    }
}

/**
 * 预处理消息内容
 * 转换引用格式，过滤可视化文件链接
 * 
 * [v2.5] 不再处理图片标记（由 extractImagesFromContent + removeImageTags 处理）
 */
export function preprocessMessageContent(content: string): string {
    return content
        // 文档引用: [[CITATION:id:name]] -> [name](http://citation/id)
        .replace(/\[\[CITATION:(.*?):(.*?)]]/g, '[$2](http://citation/$1)')
        // Wiki 引用: [[slug|title]] -> [title](http://wiki/slug)
        .replace(/\[\[([a-zA-Z0-9][a-zA-Z0-9._/-]{0,127})\|([^\]]+)]]/g, '[$2](http://wiki/$1)')
        // [FIX] 兼容只有 ID 的格式: [[CITATION:id]] / [[CITATION:id?page=n]] -> [来源](http://citation/id)
        .replace(/\[\[CITATION:([^\]]+)]]/g, '[来源](http://citation/$1)')
        // [REF 编号方案] 流式阶段 REF 标记渲染：[REF:N:filename] -> 带样式的内联引用
        .replace(/\[REF:\d+:([^\]]+)\]/g, '`📎 $1`')
        // DataFrame引用: [df_xxx] -> [数据表](http://dataframe/df_xxx)
        .replace(/\[df_([a-zA-Z0-9_]+)\]/g, '[数据表](http://dataframe/df_$1)')
        // [Fix] 移除图表文件链接，避免与按钮重复
        .replace(/👉\s*\*?\*?\[visualization_[a-zA-Z0-9_]+\.html\]\(visualization_[a-zA-Z0-9_]+\.html\)\*?\*?/g, '')
        .replace(/\[visualization_[a-zA-Z0-9_]+\.html\]\(visualization_[a-zA-Z0-9_]+\.html\)/g, '')
        .replace(/`visualization_[a-zA-Z0-9_]+\.html`/g, '')
}
