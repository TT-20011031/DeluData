/**
 * 策略建议面板组件
 * 
 * 显示 VLM 分析后的服务策略建议，包含：
 * - 建议语气
 * - 切入策略话术
 */
import { Lightbulb, MessageSquare } from 'lucide-react'
import { cn } from '@/lib/utils'

interface StrategyPanelProps {
    suggestedTone: string
    engagementStrategy: string
    className?: string
}

export function StrategyPanel({
    suggestedTone,
    engagementStrategy,
    className,
}: StrategyPanelProps) {
    return (
        <div className={cn("space-y-3", className)}>
            {/* 建议语气 */}
            <div className="p-3 rounded-xl bg-manus-tertiary border border-manus-border">
                <div className="flex items-center gap-2 mb-2">
                    <MessageSquare className="h-4 w-4 text-accent" />
                    <span className="text-xs font-medium text-manus-muted">建议语气</span>
                </div>
                <p className="text-sm text-manus-text font-medium">
                    {suggestedTone}
                </p>
            </div>

            {/* 切入策略 */}
            <div className="p-3 rounded-xl bg-accent/5 border border-accent/20">
                <div className="flex items-center gap-2 mb-2">
                    <Lightbulb className="h-4 w-4 text-accent" />
                    <span className="text-xs font-medium text-accent">切入策略</span>
                </div>
                <p className="text-sm text-manus-text leading-relaxed">
                    {engagementStrategy}
                </p>
            </div>
        </div>
    )
}

export default StrategyPanel
