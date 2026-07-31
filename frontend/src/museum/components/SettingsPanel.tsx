/**
 * 博物馆模块 - 设置面板
 * 
 * 配置部门知识库隔离等设置
 */
import { useState, useEffect } from 'react'
import { Settings, X, Database, Globe, Building2, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { getAuthHeader } from '@/stores/authStore'
import { useGuideStore } from '../stores/guideStore'
import { API_BASE_URL } from '../config'

interface Department {
    id: number
    name: string
    code: string | null
}

interface SettingsPanelProps {
    className?: string
}

export function SettingsPanel({ className }: SettingsPanelProps) {
    const { deptId, setDeptId } = useGuideStore()
    const [isOpen, setIsOpen] = useState(false)
    const [departments, setDepartments] = useState<Department[]>([])
    const [isLoading, setIsLoading] = useState(false)

    // 获取部门列表
    useEffect(() => {
        if (isOpen && departments.length === 0) {
            fetchDepartments()
        }
    }, [isOpen])

    const fetchDepartments = async () => {
        setIsLoading(true)
        try {
            const response = await fetch(`${API_BASE_URL}/museum/settings/departments`, {
                headers: getAuthHeader(),
            })
            if (response.ok) {
                const data = await response.json()
                setDepartments(data)
            }
        } catch (error) {
            console.error('[SettingsPanel] 获取部门列表失败:', error)
        } finally {
            setIsLoading(false)
        }
    }

    const handleSelect = (id: string | null) => {
        setDeptId(id)
        setIsOpen(false)
    }

    // 当前选中的部门名称
    const currentDeptName = deptId 
        ? departments.find(d => String(d.id) === deptId)?.name || `部门 ${deptId}`
        : null

    return (
        <div className={cn("relative", className)}>
            {/* 设置按钮 */}
            <Button
                variant="ghost"
                size="icon"
                onClick={() => setIsOpen(!isOpen)}
                className={cn(
                    "h-8 w-8 rounded-lg",
                    deptId ? "text-accent bg-accent/10" : "text-manus-muted hover:text-manus-text"
                )}
                title={currentDeptName ? `知识库: ${currentDeptName}` : "设置知识库（全局）"}
            >
                <Database className="h-4 w-4" />
            </Button>

            {/* 设置面板 */}
            {isOpen && (
                <div className="absolute right-0 top-10 z-50 w-64 rounded-lg border bg-background p-3 shadow-lg">
                    <div className="flex items-center justify-between mb-3">
                        <h3 className="text-sm font-medium flex items-center gap-2">
                            <Settings className="h-4 w-4" />
                            知识库范围
                        </h3>
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6"
                            onClick={() => setIsOpen(false)}
                        >
                            <X className="h-3 w-3" />
                        </Button>
                    </div>

                    <div className="space-y-1">
                        {/* 全局选项 */}
                        <button
                            className={cn(
                                "w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
                                !deptId 
                                    ? "bg-accent/10 text-accent" 
                                    : "hover:bg-muted text-muted-foreground"
                            )}
                            onClick={() => handleSelect(null)}
                        >
                            <Globe className="h-4 w-4" />
                            <span>全局（所有部门）</span>
                        </button>

                        {/* 分隔线 */}
                        <div className="h-px bg-border my-2" />

                        {/* 部门列表 */}
                        {isLoading ? (
                            <div className="flex items-center justify-center py-4 text-muted-foreground">
                                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                加载中...
                            </div>
                        ) : departments.length === 0 ? (
                            <div className="text-center py-4 text-xs text-muted-foreground">
                                暂无部门
                            </div>
                        ) : (
                            <div className="max-h-48 overflow-y-auto space-y-1">
                                {departments.map((dept) => (
                                    <button
                                        key={dept.id}
                                        className={cn(
                                            "w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
                                            deptId === String(dept.id)
                                                ? "bg-accent/10 text-accent"
                                                : "hover:bg-muted text-muted-foreground"
                                        )}
                                        onClick={() => handleSelect(String(dept.id))}
                                    >
                                        <Building2 className="h-4 w-4" />
                                        <span className="truncate">{dept.name}</span>
                                        {dept.code && (
                                            <span className="ml-auto text-xs opacity-50">{dept.code}</span>
                                        )}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}

export default SettingsPanel
