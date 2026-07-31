import { Check, ChevronDown } from 'lucide-react'
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import type { ReplyModelKey } from '@/types/replyModel'

interface ReplyModelSelectorProps {
    value: ReplyModelKey
    onChange: (value: ReplyModelKey) => void
    disabled?: boolean
}

const MODEL_OPTIONS: Array<{ key: ReplyModelKey; label: string; hint: string; description: string }> = [
    {
        key: 'plus',
        label: 'Qwen3.5-Plus',
        hint: '质量优先',
        description: '默认开启深度思考，适合需要更稳、更完整回答的场景。',
    },
    {
        key: 'max',
        label: 'Qwen3.7-Max',
        hint: '最强能力',
        description: '适合复杂知识库问答、长链路分析和对准确性要求更高的任务。',
    },
    {
        key: 'flash',
        label: 'Qwen3.5-Flash',
        hint: '速度优先',
        description: '响应更快，适合快速问答和连续追问。',
    },
]

export function ReplyModelSelector({
    value,
    onChange,
    disabled = false,
}: ReplyModelSelectorProps) {
    const activeOption = MODEL_OPTIONS.find((option) => option.key === value) ?? MODEL_OPTIONS[0]

    return (
        <div className="w-fit">
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <button
                        type="button"
                        disabled={disabled}
                        className={cn(
                            'inline-flex min-w-[220px] items-center justify-between gap-4 rounded-2xl border border-manus-border/60 bg-manus-secondary/92 px-4 py-3 text-left shadow-sm backdrop-blur-xl transition-all',
                            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/45',
                            disabled
                                ? 'cursor-not-allowed opacity-60'
                                : 'hover:border-manus-border hover:bg-manus-secondary',
                        )}
                    >
                        <div className="min-w-0 truncate text-[15px] font-semibold text-manus-text">
                            {activeOption.label}
                        </div>
                        <ChevronDown className="h-4 w-4 shrink-0 text-manus-subtle" />
                    </button>
                </DropdownMenuTrigger>

                <DropdownMenuContent
                    align="start"
                    side="bottom"
                    sideOffset={10}
                    className="w-[340px] rounded-2xl border border-manus-border/70 bg-manus-secondary/98 p-2 shadow-2xl backdrop-blur-2xl"
                >
                    <div className="px-3 pb-2 pt-1">
                        <div className="text-xs font-medium text-manus-subtle">
                            模型
                        </div>
                    </div>
                    {MODEL_OPTIONS.map((option) => {
                        const active = value === option.key
                        return (
                            <button
                                key={option.key}
                                type="button"
                                onClick={() => onChange(option.key)}
                                disabled={disabled}
                                className={cn(
                                    'flex w-full items-start gap-3 rounded-xl px-3 py-3 text-left transition-all duration-200',
                                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50',
                                    disabled
                                        ? 'cursor-not-allowed opacity-50'
                                        : 'cursor-pointer',
                                    active
                                        ? 'bg-manus-hover text-manus-text'
                                        : 'text-manus-subtle hover:bg-manus-elevated/80 hover:text-manus-text',
                                )}
                            >
                                <div
                                    className={cn(
                                        'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border',
                                        active
                                            ? 'border-accent bg-accent/10 text-accent'
                                            : 'border-manus-border/60 text-transparent',
                                    )}
                                >
                                    <Check className="h-3.5 w-3.5" />
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="text-base font-medium leading-none text-manus-text">
                                        {option.label}
                                    </div>
                                    <div className="mt-2 text-sm leading-5 text-manus-subtle">
                                        {option.description}
                                    </div>
                                </div>
                            </button>
                        )
                    })}
                </DropdownMenuContent>
            </DropdownMenu>
        </div>
    )
}
