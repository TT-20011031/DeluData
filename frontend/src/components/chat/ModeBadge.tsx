/**
 * 模式标签 Badge 组件
 * 
 * 点击快捷工具后显示在输入框内的模式指示器
 */
import { X } from 'lucide-react'
import { type QuickToolMode } from './QuickToolModes'

interface ModeBadgeProps {
    mode: QuickToolMode
    onRemove: () => void
}

export function ModeBadge({ mode, onRemove }: ModeBadgeProps) {
    const Icon = mode.icon

    return (
        <div
            className={`
                inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-medium
                ${mode.color}
                transition-all animate-in fade-in-50 zoom-in-95 duration-200
            `}
        >
            <Icon className="h-3.5 w-3.5" />
            <span>{mode.name}</span>
            <button
                onClick={(e) => {
                    e.stopPropagation()
                    onRemove()
                }}
                className="p-0.5 rounded-full hover:bg-white/20 transition-colors ml-0.5"
            >
                <X className="h-3 w-3" />
            </button>
        </div>
    )
}
