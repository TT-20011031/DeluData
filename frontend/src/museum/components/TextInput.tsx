/**
 * 文本输入组件 (调试用)
 * 
 * 提供文字输入功能，与语音输入走相同路径
 */
import { useState, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { MessageSquare, Send, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

interface TextInputProps {
    onSubmit: (text: string) => void
    disabled?: boolean
    className?: string
}

export function TextInput({ 
    onSubmit, 
    disabled = false,
    className 
}: TextInputProps) {
    const [isExpanded, setIsExpanded] = useState(false)
    const [text, setText] = useState('')
    const [isSubmitting, setIsSubmitting] = useState(false)
    const inputRef = useRef<HTMLInputElement>(null)

    const handleToggle = () => {
        if (disabled) return
        setIsExpanded(!isExpanded)
        if (!isExpanded) {
            setTimeout(() => inputRef.current?.focus(), 100)
        }
    }

    const handleSubmit = async () => {
        const trimmed = text.trim()
        if (!trimmed || isSubmitting || disabled) return
        
        setIsSubmitting(true)
        try {
            onSubmit(trimmed)
            setText('')
            setIsExpanded(false)
        } finally {
            setIsSubmitting(false)
        }
    }

    const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            handleSubmit()
        } else if (e.key === 'Escape') {
            setIsExpanded(false)
            setText('')
        }
    }

    return (
        <div className={cn("relative", className)}>
            {/* 展开的输入框 */}
            {isExpanded && (
                <div className="absolute bottom-full mb-3 left-1/2 -translate-x-1/2 w-72">
                    <div className="flex items-center gap-2 p-2 bg-manus-secondary rounded-xl border border-manus-border shadow-lg">
                        <input
                            ref={inputRef}
                            type="text"
                            value={text}
                            onChange={(e) => setText(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder="输入问题..."
                            disabled={isSubmitting || disabled}
                            className={cn(
                                "flex-1 bg-transparent text-sm text-manus-text placeholder:text-manus-muted",
                                "outline-none border-none px-2 py-1",
                                disabled && "opacity-50"
                            )}
                        />
                        <button
                            onClick={handleSubmit}
                            disabled={!text.trim() || isSubmitting || disabled}
                            className={cn(
                                "p-2 rounded-lg transition-colors",
                                text.trim() && !isSubmitting
                                    ? "bg-accent text-white hover:bg-accent/90"
                                    : "bg-manus-tertiary text-manus-muted cursor-not-allowed"
                            )}
                        >
                            {isSubmitting ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Send className="h-4 w-4" />
                            )}
                        </button>
                    </div>
                </div>
            )}

            {/* 切换按钮 */}
            <button
                onClick={handleToggle}
                disabled={disabled || isSubmitting}
                className={cn(
                    "w-14 h-14 rounded-full flex items-center justify-center transition-all duration-200",
                    isExpanded
                        ? "bg-accent text-white shadow-lg shadow-accent/30"
                        : "bg-manus-secondary hover:bg-manus-tertiary border border-manus-border",
                    (disabled || isSubmitting) && "opacity-50 cursor-not-allowed"
                )}
                title="文字输入"
            >
                <MessageSquare className={cn(
                    "h-6 w-6 transition-colors",
                    isExpanded ? "text-white" : "text-manus-text"
                )} />
            </button>
        </div>
    )
}

export default TextInput
