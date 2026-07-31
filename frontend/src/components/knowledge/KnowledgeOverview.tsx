/**
 * 知识库概览统计卡片区域
 */
import { Globe, Building2, User, ChevronRight, LayoutGrid, Upload, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { StatsData, DeptStats } from '@/types/knowledge'

export interface KnowledgeOverviewProps {
    statsData: StatsData | null
    isLoading: boolean
    onScopeChange: (scope: string) => void
    onUpload: () => void
}

export function KnowledgeOverview({
    statsData,
    isLoading,
    onScopeChange,
    onUpload,
}: KnowledgeOverviewProps) {
    return (
        <div className="h-full p-6 overflow-auto">
            <div className="max-w-5xl mx-auto">
                {/* 标题行 + 上传按钮 */}
                <div className="flex items-center justify-between mb-6">
                    <h2 className="text-lg font-semibold text-manus-text">资源概览</h2>
                    <Button variant="default" size="sm" onClick={onUpload} className="gap-1">
                        <Upload className="h-4 w-4" />
                        上传文档
                    </Button>
                </div>

                {isLoading ? (
                    <div className="flex items-center justify-center py-16">
                        <Loader2 className="h-8 w-8 animate-spin text-accent" />
                    </div>
                ) : statsData ? (
                    <div className="space-y-6">
                        {/* 全局 & 私有 卡片行 */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {/* 全局共享卡片 */}
                            <button
                                onClick={() => onScopeChange('public')}
                                className="flex items-center gap-4 p-5 bg-manus-secondary border border-manus-border rounded-xl hover:border-accent/50 hover:shadow-lg transition-all text-left group"
                            >
                                <div className="w-12 h-12 rounded-lg bg-green-500/20 flex items-center justify-center">
                                    <Globe className="h-6 w-6 text-green-400" />
                                </div>
                                <div className="flex-1">
                                    <h3 className="text-manus-text font-medium group-hover:text-accent transition-colors">
                                        全局共享
                                    </h3>
                                    <p className="text-sm text-manus-subtle">所有人可访问的文档</p>
                                </div>
                                <div className="text-right">
                                    <span className="text-2xl font-bold text-accent">
                                        {statsData.global.count}
                                    </span>
                                    <p className="text-xs text-manus-subtle">份文档</p>
                                </div>
                                <ChevronRight className="h-5 w-5 text-manus-subtle group-hover:text-accent transition-colors" />
                            </button>

                            {/* 私有文档卡片 */}
                            <button
                                onClick={() => onScopeChange('private')}
                                className="flex items-center gap-4 p-5 bg-manus-secondary border border-manus-border rounded-xl hover:border-accent/50 hover:shadow-lg transition-all text-left group"
                            >
                                <div className="w-12 h-12 rounded-lg bg-orange-500/20 flex items-center justify-center">
                                    <User className="h-6 w-6 text-orange-400" />
                                </div>
                                <div className="flex-1">
                                    <h3 className="text-manus-text font-medium group-hover:text-accent transition-colors">
                                        我的文档
                                    </h3>
                                    <p className="text-sm text-manus-subtle">仅自己可见的私有文档</p>
                                </div>
                                <div className="text-right">
                                    <span className="text-2xl font-bold text-accent">
                                        {statsData.private.count}
                                    </span>
                                    <p className="text-xs text-manus-subtle">份文档</p>
                                </div>
                                <ChevronRight className="h-5 w-5 text-manus-subtle group-hover:text-accent transition-colors" />
                            </button>
                        </div>

                        {/* 部门卡片区域 */}
                        {statsData.departments.length > 0 && (
                            <div>
                                <div className="flex items-center justify-between mb-3">
                                    <h3 className="text-sm font-medium text-manus-subtle flex items-center gap-2">
                                        <Building2 className="h-4 w-4" />
                                        部门文档
                                    </h3>
                                </div>

                                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                    {statsData.departments.map((dept: DeptStats) => (
                                        <button
                                            key={dept.id}
                                            onClick={() => onScopeChange(`dept_${dept.id}`)}
                                            className="w-full flex items-center gap-3 p-4 bg-manus-secondary border border-manus-border rounded-lg hover:border-accent/50 hover:shadow transition-all text-left group"
                                        >
                                            <div className="w-10 h-10 rounded-lg bg-blue-500/20 flex items-center justify-center">
                                                <Building2 className="h-5 w-5 text-blue-400" />
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <h4 className="text-manus-text font-medium truncate group-hover:text-accent transition-colors">
                                                    {dept.name}
                                                </h4>
                                            </div>
                                            <div className="text-right">
                                                <span className="text-xl font-bold text-accent">
                                                    {dept.count}
                                                </span>
                                                <p className="text-xs text-manus-subtle">份</p>
                                            </div>
                                            <ChevronRight className="h-4 w-4 text-manus-subtle group-hover:text-accent transition-colors" />
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                ) : (
                    <div className="text-center text-manus-subtle py-16">
                        <LayoutGrid className="h-16 w-16 mx-auto mb-4 opacity-20" />
                        <p>暂无统计数据</p>
                    </div>
                )}
            </div>
        </div>
    )
}
