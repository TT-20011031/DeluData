/**
 * 知识库页面头部导航栏
 */
import { FolderOpen, RefreshCw, LayoutGrid, Network, BookOpen } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export type KnowledgeViewMode = 'repository' | 'graph' | 'wiki'

export interface KnowledgeHeaderProps {
    viewMode: KnowledgeViewMode
    onViewModeChange: (mode: KnowledgeViewMode) => void
    isLoading: boolean
    isRefreshing: boolean
    onRefresh: () => void
}

export function KnowledgeHeader({
    viewMode,
    onViewModeChange,
    isLoading,
    isRefreshing,
    onRefresh,
}: KnowledgeHeaderProps) {
    return (
        <header className="flex items-center justify-between px-6 py-4 border-b border-manus-border bg-manus-secondary">
            <div className="flex items-center gap-2">
                <FolderOpen className="text-accent h-6 w-6" />
                <h1 className="text-xl font-semibold">知识图谱与文档管理</h1>
            </div>

            <div className="flex items-center gap-4">
                {/* 视图切换 */}
                <div className="flex bg-manus-tertiary rounded-lg p-1 border border-manus-border">
                    <button
                        onClick={() => onViewModeChange('repository')}
                        className={cn(
                            'flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition-colors',
                            viewMode === 'repository'
                                ? 'bg-manus-secondary text-accent shadow-sm'
                                : 'text-manus-subtle hover:text-manus-text'
                        )}
                    >
                        <LayoutGrid size={16} /> 资源管理
                    </button>
                    <button
                        onClick={() => onViewModeChange('graph')}
                        className={cn(
                            'flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition-colors',
                            viewMode === 'graph'
                                ? 'bg-manus-secondary text-accent shadow-sm'
                                : 'text-manus-subtle hover:text-manus-text'
                        )}
                    >
                        <Network size={16} /> 知识图谱
                    </button>
                    <button
                        onClick={() => onViewModeChange('wiki')}
                        className={cn(
                            'flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition-colors',
                            viewMode === 'wiki'
                                ? 'bg-manus-secondary text-accent shadow-sm'
                                : 'text-manus-subtle hover:text-manus-text'
                        )}
                    >
                        <BookOpen size={16} /> Wiki 视图
                    </button>
                </div>

                {/* 刷新按钮（Wiki 视图自带刷新，这里隐藏） */}
                {viewMode !== 'wiki' && (
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={onRefresh}
                        disabled={isLoading || isRefreshing}
                    >
                        <RefreshCw
                            className={cn('h-4 w-4', (isLoading || isRefreshing) && 'animate-spin')}
                        />
                    </Button>
                )}
            </div>
        </header>
    )
}
