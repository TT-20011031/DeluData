/**
 * 人物分析卡片组件
 * 
 * 显示单个人物的分析结果，包含：
 * - 角色标签
 * - 视觉线索标签
 * - 三维评分进度条（消费力、专注度、影响力）
 * - 综合权重
 */
import { cn } from '@/lib/utils'
import type { PersonInsight } from '../types'

interface PersonCardProps {
    person: PersonInsight
    isTarget: boolean
    isHighlighted: boolean
    onMouseEnter: () => void
    onMouseLeave: () => void
}

/** 评分维度配置 */
const SCORE_DIMENSIONS = [
    { key: 'purchasing_power', label: '消费力', color: 'bg-emerald-500' },
    { key: 'engagement', label: '专注度', color: 'bg-blue-500' },
    { key: 'influence', label: '影响力', color: 'bg-purple-500' },
] as const

export function PersonCard({
    person,
    isTarget,
    isHighlighted,
    onMouseEnter,
    onMouseLeave,
}: PersonCardProps) {
    return (
        <div
            className={cn(
                "p-3 rounded-xl border transition-all cursor-pointer",
                isTarget
                    ? "bg-accent/10 border-accent/50"
                    : "bg-manus-tertiary border-manus-border hover:border-manus-border/80",
                isHighlighted && "ring-2 ring-accent/50"
            )}
            onMouseEnter={onMouseEnter}
            onMouseLeave={onMouseLeave}
        >
            {/* 头部：角色标签 + 权重 */}
            <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                    {isTarget && (
                        <span className="text-xs px-1.5 py-0.5 bg-accent text-white rounded font-medium">
                            目标
                        </span>
                    )}
                    <span className="text-sm font-medium text-manus-text">
                        {person.role_label}
                    </span>
                </div>
                <span className={cn(
                    "text-lg font-bold",
                    person.final_weight >= 80 ? "text-accent" :
                    person.final_weight >= 60 ? "text-emerald-500" :
                    "text-manus-muted"
                )}>
                    {Math.round(person.final_weight)}
                </span>
            </div>

            {/* 视觉线索标签 */}
            {person.visual_cues.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-3">
                    {person.visual_cues.slice(0, 4).map((cue, i) => (
                        <span
                            key={i}
                            className="text-xs px-2 py-0.5 bg-manus-hover text-manus-muted rounded-full"
                        >
                            {cue}
                        </span>
                    ))}
                    {person.visual_cues.length > 4 && (
                        <span className="text-xs text-manus-subtle">
                            +{person.visual_cues.length - 4}
                        </span>
                    )}
                </div>
            )}

            {/* 三维评分 */}
            <div className="space-y-1.5">
                {SCORE_DIMENSIONS.map(({ key, label, color }) => {
                    const value = person.scores[key]
                    return (
                        <div key={key} className="flex items-center gap-2">
                            <span className="text-xs text-manus-subtle w-12 shrink-0">
                                {label}
                            </span>
                            <div className="flex-1 h-1.5 bg-manus-hover rounded-full overflow-hidden">
                                <div
                                    className={cn("h-full rounded-full transition-all", color)}
                                    style={{ width: `${value * 10}%` }}
                                />
                            </div>
                            <span className="text-xs text-manus-muted w-4 text-right">
                                {value}
                            </span>
                        </div>
                    )
                })}
            </div>
        </div>
    )
}

export default PersonCard
