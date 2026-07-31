/**
 * LLM 思考过程面板
 *
 * 展示 Qwen3 reasoning_content 推理链内容，可折叠。
 * - 流式期间：自动展开，渐进显示思维链
 * - 完成后：显示"已思考 N 秒"标签，默认折叠，可点击展开
 */
import { useState, useEffect, useRef, memo, useMemo, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { ChevronDown, ChevronRight, Brain } from 'lucide-react'

interface ThinkingPanelProps {
    content: string
    isDone: boolean
    durationMs?: number
}

function sanitizeThinkingContent(raw: string): string {
    if (!raw) return ''

    const filtered = raw
        // 清理内部标记
        .replace(/\[\[(?:CITATION|IMAGE):[^\]]+\]\]/gi, '')
        .replace(/\[(?:REF|IMGREF|CITATION|IMAGE):[^\]]+\]/gi, '')

    // 按行过滤明显暴露提示词工程的内容，并压缩空白
    const lines = filtered.split(/\r?\n/)
    const sanitizedLines = lines.filter((line) => {
        const trimmed = line.trim()
        if (!trimmed) return true
        return !/(system\s*prompt|prompt\s*engineering|提示词工程|内部规则|根据规则|规则指出|tool\s*call|function\s*call)/i.test(trimmed)
    })

    const compacted = sanitizedLines
        .join('\n')
        .replace(/\n{3,}/g, '\n\n')  // 仅压缩过多空行，保留模型自然分段
        .trim()

    return formatThinkingMarkdown(compacted)
}

/**
 * 将高密度思考文本整理为更易读的 Markdown 结构：
 * - 断开粘连的分段编号（如 `。1.`）
 * - 规范列表标记空格（如 `1.标题` -> `1. 标题`）
 * - 将行内 `*` 要点转为 Markdown 无序列表
 */
function formatThinkingMarkdown(raw: string): string {
    if (!raw) return ''

    return raw
        .replace(/\r\n?/g, '\n')
        .replace(/\u00A0/g, ' ')
        .replace(/[ \t]+\n/g, '\n')
        .replace(/([。！？!?；;])\s*(\d{1,2}[.、])(?=\S)/g, '$1\n\n$2')
        .replace(/(?<![A-Za-z0-9])([:：])\s*(\d{1,2}[.、])(?=\S)/g, '$1\n$2')
        .replace(/([。！？!?；;])\s*\*(?!\*)\s*(?=\S)/g, '$1\n- ')
        .replace(/(^|\n)\s*\*(?!\*)\s*(?=\S)/g, '$1- ')
        .replace(/(^|\n)(\d{1,2})\.(\S)/g, '$1$2. $3')
        .replace(/(^|\n)(\d{1,2})\)(\S)/g, '$1$2. $3')
        .replace(/(^|\n)(\d{1,2})\]\s*(\S)/g, '$1资料$2 $3')
        .replace(/(^|\n)-(\S)/g, '$1- $2')
        .replace(/(^|\n)-\s*(\d{1,2})\)\s*(\S)/g, '$1- $2. $3')
        .replace(/(^|\n)-\s*(\d{1,2})\]\s*(\S)/g, '$1- 资料$2 $3')
        .replace(/(^|\n)-\s+\*(?!\*)\s*/g, '$1- ')
        .replace(/在\s*(\d{1,2})\]\s*中/g, '在资料$1中')
        .replace(/([。！？!?；;])\s*(?:IMGREF|REF)\s*[:：]?\s*(\d+)/gi, '$1\n资料$2')
        .replace(/\b(?:IMGREF|REF)\s*[:：]?\s*(\d+)\b/gi, '资料$1')
        .replace(/\b(?:IMGREF|REF)\b/gi, '资料')
        .replace(/(^|\s)(\d{1,2})\](?=\s|[，。；;,:：])/g, '$1资料$2')
        .replace(/(^|\s)\*(?!\*)(?=\s|$)/g, '$1')
        .replace(/[ \t]{2,}/g, ' ')
        .replace(/\n{3,}/g, '\n\n')
        .trim()
}

export const ThinkingPanel = memo(function ThinkingPanel({
    content,
    isDone,
    durationMs = 0,
}: ThinkingPanelProps) {
    const [isExpanded, setIsExpanded] = useState(!isDone)
    const [followStream, setFollowStream] = useState(true)
    const scrollRef = useRef<HTMLDivElement>(null)
    const safeContent = useMemo(() => sanitizeThinkingContent(content), [content])

    const handleScroll = useCallback(() => {
        const el = scrollRef.current
        if (!el) return
        const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight
        // 仅在用户停留在底部附近时跟随流式滚动
        setFollowStream(distanceToBottom < 24)
    }, [])

    // 流式期间：仅在用户仍关注底部时自动跟随
    useEffect(() => {
        if (!isDone && isExpanded && followStream && scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight
        }
    }, [safeContent, isDone, isExpanded, followStream])

    // 推理结束时自动折叠
    useEffect(() => {
        if (isDone) {
            setIsExpanded(false)
        }
    }, [isDone])

    // 展开时默认重新跟随到底部；用户手动滚动后会取消
    useEffect(() => {
        if (isExpanded) {
            setFollowStream(true)
        }
    }, [isExpanded])

    if (!safeContent) return null

    const durationSec = durationMs > 0 ? (durationMs / 1000).toFixed(1) : null

    return (
        <div className="mb-4 rounded-xl border border-manus-border/40 overflow-hidden">
            {/* 标题栏 */}
            <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-xs hover:bg-manus-hover/40 transition-colors"
            >
                <Brain className="h-3.5 w-3.5 text-accent/50 flex-shrink-0" />
                {isDone ? (
                    <span className="text-manus-subtle text-[11px] font-medium tracking-wide">
                        {durationSec ? `思考了 ${durationSec} 秒` : '思考过程'}
                    </span>
                ) : (
                    <span className="flex items-center gap-2 text-manus-subtle text-[11px]">
                        <span className="font-medium tracking-wide">思考中...</span>
                        <span className="inline-flex items-center gap-0.5">
                            {[0, 1, 2].map((i) => (
                                <span
                                    key={i}
                                    className="h-1 w-1 rounded-full bg-accent/50 animate-thinking-breath"
                                    style={{ animationDelay: `${i * 160}ms` }}
                                />
                            ))}
                        </span>
                    </span>
                )}
                <span className="ml-auto opacity-40">
                    {isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                </span>
            </button>

            {/* 内容区域 */}
            {isExpanded && (
                <div
                    ref={scrollRef}
                    onScroll={handleScroll}
                    className="border-t border-manus-border/30 px-5 py-4 max-h-[420px] overflow-y-auto scrollbar-thin scrollbar-thumb-manus-border/40"
                >
                    <div className="thinking-content text-[13px] leading-7 break-words antialiased
                        prose prose-sm dark:prose-invert max-w-none
                        text-manus-text/72
                        prose-p:my-2.5 prose-p:leading-7 prose-p:text-manus-text/72
                        prose-headings:my-3 prose-headings:text-manus-text/85 prose-headings:font-semibold prose-headings:tracking-tight
                        prose-ul:my-3 prose-ul:pl-5 prose-ul:space-y-1.5 prose-ul:marker:text-manus-text/45
                        prose-ol:my-3 prose-ol:pl-5 prose-ol:space-y-1.5 prose-ol:marker:text-manus-text/45 prose-ol:marker:font-medium
                        prose-li:my-0 prose-li:leading-7 prose-li:text-manus-text/72
                        prose-strong:font-semibold prose-strong:text-manus-text/90
                        prose-em:text-manus-text/78 prose-em:not-italic prose-em:font-medium
                        prose-code:text-[11px] prose-code:text-accent/80 prose-code:bg-accent/10 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded-md prose-code:font-normal
                        prose-pre:my-3 prose-pre:bg-manus-tertiary/45 prose-pre:rounded-lg prose-pre:border prose-pre:border-manus-border/35 prose-pre:px-3 prose-pre:py-2 prose-pre:text-[11px]
                        prose-blockquote:my-3 prose-blockquote:border-l-2 prose-blockquote:border-accent/30 prose-blockquote:bg-accent/[0.04] prose-blockquote:pl-3 prose-blockquote:py-1 prose-blockquote:rounded-r-md prose-blockquote:text-manus-text/62 prose-blockquote:not-italic
                        prose-hr:my-4 prose-hr:border-manus-border/30
                    ">
                        <ReactMarkdown
                            remarkPlugins={[remarkGfm, remarkMath]}
                            rehypePlugins={[rehypeKatex]}
                        >
                            {safeContent}
                        </ReactMarkdown>
                    </div>
                </div>
            )}
        </div>
    )
})
