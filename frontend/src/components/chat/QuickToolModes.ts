import { BookOpen, Database, Sparkles, Zap } from 'lucide-react'

export interface QuickToolMode {
    id: string
    name: string
    icon: React.ComponentType<{ className?: string }>
    color: string
    description: string
    requiresDb?: boolean
    requiresKnowledge?: boolean
    needsData?: boolean
    isSkillPicker?: boolean
}

export const QUICK_TOOL_MODES: QuickToolMode[] = [
    {
        id: 'sql_only',
        name: 'SQL 查询',
        icon: Database,
        color: 'bg-blue-200/15 text-blue-500 border-blue-400/25',
        description: '直接生成并执行 SQL 查询',
        requiresDb: true,
    },
    {
        id: 'rag_only',
        name: '知识库',
        icon: BookOpen,
        color: 'bg-green-200/15 text-green-500 border-green-400/25',
        description: '在知识库中检索信息',
        requiresKnowledge: true,
    },
    {
        id: 'skill_pick',
        name: 'DeLuSkills',
        icon: Zap,
        color: 'bg-violet-200/15 text-violet-500 border-violet-400/25',
        description: '选择 DeluSkills 直接执行',
        isSkillPicker: true,
    },
]

export function getQuickToolMode(modeId: string): QuickToolMode | undefined {
    return QUICK_TOOL_MODES.find((m) => m.id === modeId)
}

export const AUTO_MODE: QuickToolMode = {
    id: 'auto',
    name: '智能模式',
    icon: Sparkles,
    color: 'bg-accent/20 text-accent border-accent/30',
    description: 'AI 自动判断最佳处理方式',
}
