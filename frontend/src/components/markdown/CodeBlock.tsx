import { useState, memo } from 'react'
import { Copy, Check } from 'lucide-react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'

export const CodeBlock = memo(({ inline, className, children, ...props }: any) => {
    const match = /language-(\w+)/.exec(className || '')
    const language = match ? match[1] : ''
    const [isCopied, setIsCopied] = useState(false)

    const handleCopy = () => {
        if (!children) return
        navigator.clipboard.writeText(String(children).replace(/\n$/, ''))
        setIsCopied(true)
        setTimeout(() => setIsCopied(false), 2000)
    }

    // 行内代码
    if (inline) {
        return (
            <code className="bg-white/10 text-gray-200 px-1.5 py-0.5 rounded font-mono text-sm" {...props}>
                {children}
            </code>
        )
    }

    const content = String(children).replace(/\n$/, '')
    const isSingleLine = content.indexOf('\n') === -1
    const isSimple = !language || language === 'text'

    // 紧凑模式：单行且无特定语言的代码块
    // 渲染为轻量级样式，去除黑色背景框，去除复制按钮，仅作为高亮文本显示
    if (isSingleLine && isSimple) {
        return (
            <span className="inline-flex items-center mx-1 align-baseline">
                <code className="bg-white/10 border border-white/20 px-1.5 py-0.5 rounded text-sm font-mono text-manus-text">
                    {content}
                </code>
            </span>
        )
    }

    return (
        <div className="my-4 rounded-xl overflow-hidden border border-manus-border bg-[#1e1e1e] shadow-sm group">
            <div className="flex items-center justify-between px-4 py-2 bg-[#2d2d2d] text-xs text-gray-400 border-b border-white/5 select-none">
                <span className="font-medium font-mono">{language || 'text'}</span>
                <button
                    onClick={handleCopy}
                    className="flex items-center gap-1.5 hover:text-white transition-colors p-1 rounded hover:bg-white/5"
                    aria-label="复制"
                >
                    {isCopied ? (
                        <>
                            <Check className="h-3.5 w-3.5 text-success" />
                            <span>已复制</span>
                        </>
                    ) : (
                        <>
                            <Copy className="h-3.5 w-3.5" />
                            <span className="opacity-0 group-hover:opacity-100 transition-opacity">复制</span>
                        </>
                    )}
                </button>
            </div>
            <div className="relative">
                <SyntaxHighlighter
                    style={vscDarkPlus as any}
                    language={language}
                    PreTag="div"
                    customStyle={{
                        margin: 0,
                        padding: '1rem',
                        background: 'transparent',
                        fontSize: '0.9rem',
                        lineHeight: '1.5',
                        fontFamily: 'JetBrains Mono, Menlo, Monaco, Consolas, monospace'
                    }}
                    codeTagProps={{
                        style: {
                            background: 'transparent',
                        }
                    }}
                    wrapLines={true}
                    lineProps={{
                        style: { background: 'transparent' }
                    }}
                    {...props}
                >
                    {content}
                </SyntaxHighlighter>
            </div>
        </div>
    )
})
