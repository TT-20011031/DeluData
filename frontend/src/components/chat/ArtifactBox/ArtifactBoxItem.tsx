/**
 * 单条暂存产物卡片
 */
import { memo, useEffect, useMemo, useState } from 'react'
import { BarChart3, FileText, FileSpreadsheet, Trash2, ExternalLink } from 'lucide-react'
import type { TempArtifact } from '@/api/artifactApi'

interface ArtifactBoxItemProps {
    artifact: TempArtifact
    onView: (artifact: TempArtifact) => void
    onDelete: (id: string) => void
}

function formatCountdown(expiresAt: string): string {
    try {
        const diff = new Date(expiresAt).getTime() - Date.now()
        if (diff <= 0) return '已过期'
        const h = Math.floor(diff / 3600000)
        const m = Math.floor((diff % 3600000) / 60000)
        return h > 0 ? `${h}h ${m}m` : `${m}m`
    } catch {
        return '-'
    }
}

function formatCreatedAt(createdAt: string): string {
    try {
        return new Date(createdAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    } catch {
        return '-'
    }
}

export const ArtifactBoxItem = memo(function ArtifactBoxItem({
    artifact,
    onView,
    onDelete,
}: ArtifactBoxItemProps) {
    const [tick, setTick] = useState(0)
    const lowerFileName = artifact.file_name?.toLowerCase() || ''
    const isExcel = artifact.type === 'doc' && (
        artifact.file_kind === 'excel'
        || lowerFileName.endsWith('.xlsx')
        || lowerFileName.endsWith('.xls')
        || lowerFileName.endsWith('.xlsm')
    )

    useEffect(() => {
        const timer = window.setInterval(() => {
            setTick((t) => t + 1)
        }, 30_000)
        return () => window.clearInterval(timer)
    }, [])

    const countdown = useMemo(() => formatCountdown(artifact.expires_at), [artifact.expires_at, tick])
    const createdAt = useMemo(() => formatCreatedAt(artifact.created_at), [artifact.created_at])

    return (
        <div className="group flex items-start gap-2.5 p-2.5 rounded-lg hover:bg-accent/5 transition-colors cursor-pointer border border-transparent hover:border-accent/10">
            {/* 类型图标 */}
            <div className="flex-shrink-0 mt-0.5">
                {artifact.type === 'chart' ? (
                    <BarChart3 className="h-4 w-4 text-accent/70" />
                ) : isExcel ? (
                    <FileSpreadsheet className="h-4 w-4 text-emerald-600/80" />
                ) : (
                    <FileText className="h-4 w-4 text-blue-500/70" />
                )}
            </div>

            {/* 内容 */}
            <div className="flex-1 min-w-0" onClick={() => onView(artifact)}>
                <p className="text-xs font-medium text-manus-text truncate">{artifact.title}</p>
                <p className="text-[10px] text-manus-muted/60 mt-0.5">
                    {createdAt} · 剩余 {countdown}
                </p>
            </div>

            {/* 操作按钮（hover 显示） */}
            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
                <button
                    onClick={() => onView(artifact)}
                    className="p-1 rounded hover:bg-accent/10 text-manus-muted hover:text-accent transition-colors"
                    title={artifact.type === 'chart' ? '查看图表' : isExcel ? '下载 Excel' : '下载文件'}
                >
                    <ExternalLink className="h-3 w-3" />
                </button>
                <button
                    onClick={(e) => { e.stopPropagation(); onDelete(artifact.id) }}
                    className="p-1 rounded hover:bg-red-500/10 text-manus-muted hover:text-red-500 transition-colors"
                    title="删除"
                >
                    <Trash2 className="h-3 w-3" />
                </button>
            </div>
        </div>
    )
})
