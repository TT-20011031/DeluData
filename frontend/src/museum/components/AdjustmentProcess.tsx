/**
 * 调节过程可视化组件
 * 
 * 展示提示词调节的逐步过程
 */
import { CheckCircle, Loader2, Palette, Target, Type, Volume2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { AdjustmentStep } from '../types'

interface AdjustmentProcessProps {
    steps: AdjustmentStep[]
    isAdjusting: boolean
}

const STEP_ICONS: Record<string, React.ElementType> = {
    style: Palette,
    focus: Target,
    vocabulary: Type,
    voice: Volume2,
}

export function AdjustmentProcess({ steps, isAdjusting }: AdjustmentProcessProps) {
    if (steps.length === 0) return null

    return (
        <div className="space-y-2 p-4 bg-slate-800/50 rounded-xl border border-slate-700/50">
            <h4 className="text-sm font-medium text-slate-400 mb-3">
                个性化调节中...
            </h4>

            {steps.map((step, index) => {
                const Icon = STEP_ICONS[step.step] || Palette
                const isComplete = step.progress === 100 || index < steps.length - 1
                const isActive = index === steps.length - 1 && isAdjusting

                return (
                    <div
                        key={step.step}
                        className={cn(
                            "flex items-center gap-3 p-2 rounded-lg transition-all",
                            isActive && "bg-amber-500/10"
                        )}
                    >
                        {/* 图标 */}
                        <div className={cn(
                            "flex items-center justify-center w-8 h-8 rounded-full",
                            isComplete
                                ? "bg-green-500/20 text-green-400"
                                : isActive
                                    ? "bg-amber-500/20 text-amber-400"
                                    : "bg-slate-700 text-slate-500"
                        )}>
                            {isComplete ? (
                                <CheckCircle className="h-4 w-4" />
                            ) : isActive ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Icon className="h-4 w-4" />
                            )}
                        </div>

                        {/* 内容 */}
                        <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between">
                                <span className={cn(
                                    "text-sm font-medium",
                                    isComplete ? "text-green-400" : "text-slate-300"
                                )}>
                                    {step.label}
                                </span>
                            </div>
                            <p className="text-xs text-slate-500 truncate">
                                {step.value}
                            </p>
                        </div>

                        {/* 进度 */}
                        {isActive && (
                            <div className="w-16">
                                <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                                    <div
                                        className="h-full bg-amber-500 rounded-full transition-all duration-300"
                                        style={{ width: `${step.progress}%` }}
                                    />
                                </div>
                            </div>
                        )}
                    </div>
                )
            })}
        </div>
    )
}

export default AdjustmentProcess
