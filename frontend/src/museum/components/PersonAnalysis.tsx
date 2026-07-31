/**
 * 人物分析结果组件
 * 
 * 显示 VLM 识别结果
 */
import { User, Briefcase, Baby, Heart, Clock } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { PersonType } from '../types'

interface PersonAnalysisProps {
    personType: PersonType
    confidence: number
    features: string[]
    fallback?: boolean
    reason?: string
}

const PERSON_ICONS: Record<PersonType, React.ElementType> = {
    '商务人士': Briefcase,
    '儿童': Baby,
    '妇女': Heart,
    '老年人': Clock,
    '通用访客': User,
}

const PERSON_COLORS: Record<PersonType, string> = {
    '商务人士': 'bg-blue-500/20 border-blue-500/50 text-blue-400',
    '儿童': 'bg-green-500/20 border-green-500/50 text-green-400',
    '妇女': 'bg-pink-500/20 border-pink-500/50 text-pink-400',
    '老年人': 'bg-amber-500/20 border-amber-500/50 text-amber-400',
    '通用访客': 'bg-slate-500/20 border-slate-500/50 text-slate-400',
}

const PERSON_DESCRIPTIONS: Record<PersonType, string> = {
    '商务人士': '专业、简洁的导览风格',
    '儿童': '活泼、有趣的互动式导览',
    '妇女': '优雅、细腻的讲解风格',
    '老年人': '清晰、怀旧的讲解风格',
    '通用访客': '友好、平衡的导览服务',
}

export function PersonAnalysis({
    personType,
    confidence,
    features,
    fallback,
    reason,
}: PersonAnalysisProps) {
    const Icon = PERSON_ICONS[personType] || User
    const colorClass = PERSON_COLORS[personType] || PERSON_COLORS['通用访客']
    const description = PERSON_DESCRIPTIONS[personType] || PERSON_DESCRIPTIONS['通用访客']

    return (
        <div className={cn(
            "relative p-4 rounded-xl border backdrop-blur-sm",
            colorClass
        )}>
            <div className="flex items-start gap-3">
                {/* 图标 */}
                <div className={cn(
                    "flex items-center justify-center w-12 h-12 rounded-full",
                    "bg-current/10"
                )}>
                    <Icon className="h-6 w-6" />
                </div>

                {/* 内容 */}
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                        <h3 className="font-bold text-lg text-white">
                            {personType}
                        </h3>
                        {!fallback && confidence > 0 && (
                            <span className="text-xs px-2 py-0.5 rounded-full bg-current/20">
                                {Math.round(confidence * 100)}% 置信度
                            </span>
                        )}
                    </div>

                    <p className="text-sm text-slate-400 mt-1">
                        {description}
                    </p>

                    {/* 特征标签 */}
                    {features.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 mt-2">
                            {features.map((feature, index) => (
                                <span
                                    key={index}
                                    className="text-xs px-2 py-0.5 rounded bg-slate-700/50 text-slate-300"
                                >
                                    {feature}
                                </span>
                            ))}
                        </div>
                    )}

                    {/* 降级提示 */}
                    {fallback && reason && (
                        <p className="text-xs text-slate-500 mt-2 italic">
                            {reason}
                        </p>
                    )}
                </div>
            </div>
        </div>
    )
}

export default PersonAnalysis
