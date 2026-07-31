/**
 * 聊天消息组件
 *
 * 渲染用户消息和 AI 消息，支持 Markdown、多模态图片和引用链接
 *
 * [v2.5] 图片渲染策略：
 * - 图片引用从正文中提取并移除
 * - 所有图片在消息末尾由 ImageCarousel 统一展示
 *
 * [v3.0] 思考状态动态化：
 * - 使用 ThinkingStatusConfig 的前缀标记判断占位消息
 * - 根据 step 阶段动态显示文案（获取数据中 / 查询数据中 等）
 */
import { memo, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { BarChart3 } from 'lucide-react'
import type { Message } from '@/stores/chatStore'
import type { Artifact } from '@/types/sessionRound'
import {
    createMarkdownComponents,
    preprocessMessageContent,
    extractImagesFromContent,
    removeImageTags
} from './MarkdownRenderConfig'
import { ArtifactCard } from './ArtifactCard'
import { ImageCarousel } from './ImageCarousel'
import {
    isThinkingContent,
    decodeThinkingContent,
    DEFAULT_THINKING_STATUS,
} from './ThinkingStatusConfig'
import { ThinkingPanel } from './ThinkingPanel'

interface ChatMessageProps {
    message: Message
    /** [Session Round] 消息关联的产物列表 */
    artifacts?: Artifact[]
    onPreviewFile: (fileId: string, fileName: string, fileType: string, page?: number, anchor?: string) => void
    onPreviewDataFrame: (dfKey: string) => void
    onOpenWikiSlug?: (slug: string) => void
    onShowHtmlReport?: (report: { id: string; title: string; content: string }) => void
    onDownloadFile?: (downloadUrl: string, fileName: string) => void
}

export const ChatMessage = memo(function ChatMessage({
    message,
    artifacts,
    onPreviewFile,
    onPreviewDataFrame,
    onOpenWikiSlug,
    onShowHtmlReport,
    onDownloadFile
}: ChatMessageProps) {
    const isUser = message.role === 'user'
    const normalizedContent = (message.content || '').trim()
    const isThinkingPlaceholder = message.isStreaming && isThinkingContent(normalizedContent)

    // 解析 thinking 状态文案
    const thinkingText = useMemo(() => {
        if (!isThinkingPlaceholder) return null
        const decoded = decodeThinkingContent(normalizedContent)
        return decoded?.text || DEFAULT_THINKING_STATUS.text
    }, [isThinkingPlaceholder, normalizedContent])

    // [v2.5] 提取图片并准备处理后的内容
    const { processedContent, extractedImages } = useMemo(() => {
        if (isUser) {
            return { processedContent: message.content, extractedImages: [] }
        }
        const images = extractImagesFromContent(message.content)
        const content = removeImageTags(message.content)
        return { processedContent: content, extractedImages: images }
    }, [message.content, isUser])

    // 用户消息
    if (isUser) {
        return (
            <div className="flex flex-row-reverse gap-3 mb-6 animate-fade-in w-full">
                <div className="flex flex-col max-w-[85%] items-end gap-2">
                    {/* 附件图片渲染 */}
                    {message.attachments?.filter(a => a.type === 'image').map((attachment, idx) => (
                        <img
                            key={idx}
                            src={attachment.previewUrl || attachment.url}
                            alt={attachment.name}
                            className="max-w-xs max-h-48 rounded-lg object-cover cursor-pointer hover:opacity-90 transition-opacity shadow-md"
                            onClick={() => window.open(attachment.previewUrl || attachment.url, '_blank')}
                            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                        />
                    ))}
                    <div className="chat-user-bubble px-4 py-3 bg-accent text-white rounded-2xl rounded-tr-sm text-sm leading-relaxed shadow-sm break-words whitespace-pre-wrap">
                        {message.content}
                    </div>
                </div>
            </div>
        )
    }

    const markdownComponents = createMarkdownComponents({
        onPreviewFile,
        onPreviewDataFrame,
        onOpenWikiSlug,
    })

    const handleViewChart = (htmlContent: string) => {
        onShowHtmlReport?.({
            id: 'artifact-chart',
            title: '数据可视化报告',
            content: htmlContent
        })
    }

    const handleDownload = (downloadUrl: string, fileName: string) => {
        if (onDownloadFile) {
            onDownloadFile(downloadUrl, fileName)
        } else {
            window.open(downloadUrl, '_blank')
        }
    }

    return (
        <div className="flex items-start gap-4 mb-8 animate-fade-in w-full">
            {/* 头像：固定尺寸，顶部对齐 */}
            <div className="flex-shrink-0 relative" style={{ width: 32, height: 32 }}>
                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-accent/20 to-accent/5 flex items-center justify-center border border-accent/10 shadow-sm">
                    <span className="text-accent font-semibold text-[10px] tracking-tight">{'-_<'}</span>
                </div>
                {message.isStreaming && (
                    <div className="absolute -inset-2 rounded-full bg-accent/30 animate-ripple z-[-1]" />
                )}
            </div>

            {/* 内容区域：顶部对齐 */}
            <div className="flex-1 overflow-hidden min-w-0 pt-[5px]">
                {/* [LLM 推理令牌] 思考过程面板 */}
                {!isUser && message.thinkingContent && (
                    <ThinkingPanel
                        content={message.thinkingContent}
                        isDone={Boolean(message.isThinkingDone) && !message.isStreaming}
                        durationMs={message.thinkingDurationMs}
                    />
                )}
                {isThinkingPlaceholder && thinkingText ? (
                    <div className="inline-flex items-center gap-2 text-sm text-manus-muted">
                        <span className="font-medium text-manus-text/80">{thinkingText}</span>
                        <span className="inline-flex items-center gap-1">
                            {[0, 1, 2].map((idx) => (
                                <span
                                    key={idx}
                                    className="h-1.5 w-1.5 rounded-full bg-accent/80 animate-thinking-breath"
                                    style={{ animationDelay: `${idx * 180}ms` }}
                                />
                            ))}
                        </span>
                    </div>
                ) : (
                    <div className="markdown-body prose prose-sm dark:prose-invert max-w-none break-words leading-7 text-manus-text prose-p:my-3 prose-headings:my-4 prose-ul:my-3 prose-ol:my-3 prose-pre:p-0 prose-pre:bg-transparent prose-pre:border-0">
                        <ReactMarkdown
                            remarkPlugins={[remarkGfm, remarkMath]}
                            rehypePlugins={[rehypeKatex]}
                            components={markdownComponents}
                        >
                            {preprocessMessageContent(processedContent)}
                        </ReactMarkdown>
                    </div>
                )}

                {extractedImages.length > 0 && (
                    <ImageCarousel images={extractedImages} />
                )}

                {artifacts && artifacts.length > 0 && (
                    <div className="mt-4 grid gap-3 grid-cols-1 sm:grid-cols-2">
                        {artifacts.map(artifact => (
                            <ArtifactCard
                                key={artifact.stepId}
                                artifact={artifact}
                                onViewChart={handleViewChart}
                                onDownload={handleDownload}
                            />
                        ))}
                    </div>
                )}

                {message.artifacts?.html_report && !artifacts?.length && (
                    <div className="mt-3">
                        <button
                            onClick={() => onShowHtmlReport?.({
                                id: message.artifacts!.html_report!.report_id,
                                title: message.artifacts!.html_report!.title,
                                content: message.artifacts!.html_report!.html_content
                            })}
                            className="inline-flex items-center gap-2 px-3 py-1.5 bg-accent/10 hover:bg-accent/20 text-accent rounded-lg text-sm transition-colors border border-accent/20"
                        >
                            <BarChart3 className="h-4 w-4" />
                            查看图表报告
                        </button>
                    </div>
                )}
            </div>
        </div>
    )
})
