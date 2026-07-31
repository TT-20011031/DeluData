import { useEffect, useState } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { skillService, type SkillSummary } from '@/services/skillService'
import { QUICK_TOOL_MODES, type QuickToolMode } from './QuickToolModes'

interface QuickToolBarProps {
    onSelectMode: (mode: QuickToolMode) => void
    onSelectSkill?: (skillId: string, skillName: string) => void
    hasData?: boolean
    userExecutionMode?: string
    hasDb?: boolean
    hasKnowledge?: boolean
    readinessAvailable?: boolean
}

function getDisableReason(
    mode: QuickToolMode,
    options: {
        hasData: boolean
        hasDb: boolean
        hasKnowledge: boolean
        readinessAvailable: boolean
    },
): string | null {
    const { hasData, hasDb, hasKnowledge, readinessAvailable } = options

    if (readinessAvailable) {
        if (mode.requiresDb && !hasDb) {
            return '请先连接数据库'
        }
        if (mode.requiresKnowledge && !hasKnowledge) {
            return '请先上传知识库文档'
        }
    }

    if (mode.needsData && !hasData) {
        return '需要先上传文件或先执行 SQL/知识库查询'
    }

    return null
}

export function QuickToolBar({
    onSelectMode,
    onSelectSkill,
    hasData = false,
    userExecutionMode = 'auto',
    hasDb = false,
    hasKnowledge = false,
    readinessAvailable = false,
}: QuickToolBarProps) {
    const [skills, setSkills] = useState<SkillSummary[]>([])
    const [skillsLoading, setSkillsLoading] = useState(false)
    const [skillDropdownOpen, setSkillDropdownOpen] = useState(false)

    useEffect(() => {
        if (!skillDropdownOpen) return
        let cancelled = false
        setSkillsLoading(true)
        skillService.getSkills()
            .then((list) => { if (!cancelled) setSkills(list) })
            .catch(() => { if (!cancelled) setSkills([]) })
            .finally(() => { if (!cancelled) setSkillsLoading(false) })
        return () => { cancelled = true }
    }, [skillDropdownOpen])

    if (userExecutionMode !== 'auto') {
        return null
    }

    return (
        <div className="flex items-center justify-center gap-2 mt-3">
            {QUICK_TOOL_MODES.map((mode) => {
                const disableReason = getDisableReason(mode, {
                    hasData,
                    hasDb,
                    hasKnowledge,
                    readinessAvailable,
                })
                const isDisabled = Boolean(disableReason)
                const Icon = mode.icon

                if (mode.isSkillPicker) {
                    return (
                        <DropdownMenu
                            key={mode.id}
                            open={skillDropdownOpen}
                            onOpenChange={setSkillDropdownOpen}
                        >
                            <DropdownMenuTrigger asChild>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-8 px-3 gap-1.5 rounded-lg border-0 transition-all text-manus-subtle hover:text-manus-text hover:bg-manus-hover"
                                >
                                    <Icon className="h-4 w-4" />
                                    <span className="text-xs">{mode.name}</span>
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent
                                align="center"
                                className="w-64 max-h-72 overflow-y-auto bg-manus-secondary border-manus-border"
                            >
                                {skillsLoading ? (
                                    <div className="flex items-center justify-center gap-2 py-4 text-xs text-manus-muted">
                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                        加载技能列表...
                                    </div>
                                ) : skills.length === 0 ? (
                                    <div className="py-4 text-center text-xs text-manus-muted">
                                        暂无可用技能，请先创建 DeluSkills
                                    </div>
                                ) : (
                                    skills.map((skill) => (
                                        <DropdownMenuItem
                                            key={skill.id}
                                            className="flex flex-col items-start gap-0.5 px-3 py-2 cursor-pointer hover:bg-manus-hover"
                                            onClick={() => {
                                                onSelectSkill?.(skill.id, skill.title)
                                                setSkillDropdownOpen(false)
                                            }}
                                        >
                                            <span className="text-sm font-medium text-manus-text truncate w-full">{skill.title}</span>
                                            {skill.description && (
                                                <span className="text-xs text-manus-muted truncate w-full">{skill.description}</span>
                                            )}
                                        </DropdownMenuItem>
                                    ))
                                )}
                            </DropdownMenuContent>
                        </DropdownMenu>
                    )
                }

                return (
                    <Button
                        key={mode.id}
                        variant="outline"
                        size="sm"
                        onClick={() => !isDisabled && onSelectMode(mode)}
                        className={
                            `h-8 px-3 gap-1.5 rounded-lg border-0 transition-all ` +
                            (isDisabled
                                ? 'opacity-50 cursor-not-allowed text-manus-muted'
                                : 'text-manus-subtle hover:text-manus-text hover:bg-manus-hover')
                        }
                    >
                        <Icon className="h-4 w-4" />
                        <span className="text-xs">{mode.name}</span>
                        {isDisabled && <AlertTriangle className="h-3.5 w-3.5 text-yellow-500" />}
                    </Button>
                )
            })}
        </div>
    )
}
