/**
 * 暂存箱侧边面板
 *
 * 展示当前用户所有临时产物（图表/文档），支持查看、删除和跳转会话。
 */
import { useEffect, useCallback } from 'react'
import { X, Archive, RefreshCw } from 'lucide-react'
import { useArtifactBoxStore } from '@/stores/artifactBoxStore'
import { artifactApi, type TempArtifact } from '@/api/artifactApi'
import { ArtifactBoxItem } from './ArtifactBoxItem'

interface ArtifactBoxPanelProps {
    onViewChart: (htmlContent: string) => void
}

export function ArtifactBoxPanel({ onViewChart }: ArtifactBoxPanelProps) {
    const { isOpen, artifacts, isLoading, error, close, fetchArtifacts, deleteArtifact } = useArtifactBoxStore()

    useEffect(() => {
        if (isOpen) {
            fetchArtifacts()
        }
    }, [isOpen, fetchArtifacts])

    const handleView = useCallback(async (artifact: TempArtifact) => {
        if (artifact.type === 'chart') {
            try {
                const html = await artifactApi.getContent(artifact.id)
                onViewChart(html)
            } catch {
                // silent
            }
        } else if (artifact.download_url) {
            window.open(artifact.download_url, '_blank')
        }
    }, [onViewChart])

    const handleDelete = useCallback(async (id: string) => {
        await deleteArtifact(id)
    }, [deleteArtifact])

    if (!isOpen) return null

    return (
        <>
            {/* 遮罩层（点击关闭） */}
            <div
                className="fixed inset-0 z-40 bg-transparent"
                onClick={close}
            />

            {/* 面板 */}
            <div className="fixed right-0 top-0 bottom-0 z-50 w-[92vw] sm:w-[30rem] lg:w-[34rem] bg-manus-surface border-l border-manus-border flex flex-col shadow-2xl animate-fade-in">
                {/* 标题栏 */}
                <div className="flex items-center gap-2 px-4 py-3.5 border-b border-manus-border">
                    <Archive className="h-4 w-4 text-accent/70" />
                    <div className="flex-1">
                        <p className="text-sm font-semibold text-manus-text">暂存箱</p>
                        <p className="text-[11px] text-manus-muted">图表与文档会自动暂存到这里</p>
                    </div>
                    <button
                        onClick={fetchArtifacts}
                        className="p-1.5 rounded hover:bg-accent/10 text-manus-muted hover:text-accent transition-colors"
                        title="刷新"
                    >
                        <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`} />
                    </button>
                    <button
                        onClick={close}
                        className="p-1.5 rounded hover:bg-accent/10 text-manus-muted hover:text-manus-text transition-colors"
                    >
                        <X className="h-3.5 w-3.5" />
                    </button>
                </div>

                {/* 说明文字 */}
                <div className="px-4 py-2 bg-accent/5 border-b border-manus-border">
                    <p className="text-[10px] text-manus-muted/70">产物在生成后 1 小时内可访问，活跃时自动续期</p>
                </div>

                {/* 产物列表 */}
                <div className="flex-1 overflow-y-auto">
                    {error && (
                        <div className="px-4 py-3 text-xs text-red-600 bg-red-50 border-b border-red-100">
                            暂存箱拉取失败：{error}
                        </div>
                    )}

                    {isLoading && artifacts.length === 0 && (
                        <div className="flex items-center justify-center h-full text-manus-muted text-sm">
                            加载中...
                        </div>
                    )}

                    {!isLoading && artifacts.length === 0 && (
                        <div className="flex flex-col items-center justify-center h-full text-manus-muted/60">
                            <Archive className="h-10 w-10 mb-3 opacity-40" />
                            <p className="text-sm font-medium">暂无产物</p>
                            <p className="text-xs mt-1 opacity-70 text-center px-4">图表和文档将在生成后自动保存至此</p>
                        </div>
                    )}

                    {artifacts.length > 0 && (
                        <div className="py-2">
                            {artifacts.map((artifact) => (
                                <ArtifactBoxItem
                                    key={artifact.id}
                                    artifact={artifact}
                                    onView={handleView}
                                    onDelete={handleDelete}
                                />
                            ))}
                        </div>
                    )}
                </div>

                {/* 底部计数 */}
                {artifacts.length > 0 && (
                    <div className="px-4 py-2 border-t border-manus-border">
                        <p className="text-[10px] text-manus-muted/60 text-center">
                            共 {artifacts.length} 个产物
                        </p>
                    </div>
                )}
            </div>
        </>
    )
}
