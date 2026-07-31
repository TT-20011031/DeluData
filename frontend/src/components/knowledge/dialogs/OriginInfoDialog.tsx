/**
 * 溯源信息对话框
 */
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import type { OriginInfo } from '@/types/knowledge'

export interface OriginInfoDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    isLoading: boolean
    originInfo: OriginInfo | null
}

export function OriginInfoDialog({
    open,
    onOpenChange,
    isLoading,
    originInfo,
}: OriginInfoDialogProps) {
    const getVisibilityBadge = (visibility: string) => {
        const styles = {
            public: 'bg-green-500/20 text-green-400',
            private: 'bg-orange-500/20 text-orange-400',
            dept: 'bg-blue-500/20 text-blue-400',
        }
        const labels = {
            public: '全局共享',
            private: '仅自己可见',
            dept: '本部门可见',
        }
        return (
            <span className={cn('px-2 py-0.5 rounded text-xs', styles[visibility as keyof typeof styles] || styles.dept)}>
                {labels[visibility as keyof typeof labels] || '本部门可见'}
            </span>
        )
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="bg-manus-secondary border-manus-border text-manus-text max-w-md">
                <DialogHeader>
                    <DialogTitle>溯源信息</DialogTitle>
                </DialogHeader>

                <div className="py-4 space-y-3">
                    {isLoading ? (
                        <div className="flex items-center justify-center py-8">
                            <Loader2 className="h-8 w-8 animate-spin text-accent" />
                        </div>
                    ) : originInfo ? (
                        <>
                            <div className="flex justify-between items-center py-2 border-b border-manus-border">
                                <span className="text-manus-subtle">文件名</span>
                                <span className="font-medium">{originInfo.name}</span>
                            </div>
                            <div className="flex justify-between items-center py-2 border-b border-manus-border">
                                <span className="text-manus-subtle">上传者</span>
                                <span className="font-medium">{originInfo.owner_name}</span>
                            </div>
                            <div className="flex justify-between items-center py-2 border-b border-manus-border">
                                <span className="text-manus-subtle">所属部门</span>
                                <span className="font-medium">{originInfo.department_name || '无'}</span>
                            </div>
                            <div className="flex justify-between items-center py-2 border-b border-manus-border">
                                <span className="text-manus-subtle">可见范围</span>
                                {getVisibilityBadge(originInfo.visibility)}
                            </div>
                            <div className="flex justify-between items-center py-2 border-b border-manus-border">
                                <span className="text-manus-subtle">上传时间</span>
                                <span className="font-medium text-sm">{originInfo.created_at}</span>
                            </div>

                            {/* [新增] 切片统计区域 */}
                            <div className="mt-3 pt-3 border-t border-manus-border-strong">
                                <div className="text-xs text-manus-subtle mb-2 font-medium">RAG 切片统计</div>
                                <div className="flex justify-between items-center py-1">
                                    <span className="text-manus-subtle text-sm">切片数量</span>
                                    <span className="font-mono text-sm">{originInfo.chunk_count ?? 0}</span>
                                </div>
                                <div className="flex justify-between items-center py-1">
                                    <span className="text-manus-subtle text-sm">file_id 覆盖</span>
                                    <span className={cn(
                                        "font-mono text-sm px-2 py-0.5 rounded",
                                        originInfo.chunk_count === originInfo.chunks_with_file_id
                                            ? "bg-green-500/20 text-green-400"
                                            : "bg-orange-500/20 text-orange-400"
                                    )}>
                                        {originInfo.file_id_coverage || '0/0'}
                                    </span>
                                </div>
                            </div>
                        </>
                    ) : (
                        <div className="text-center text-manus-subtle py-4">无法获取溯源信息</div>
                    )}
                </div>

                <DialogFooter>
                    <Button
                        onClick={() => onOpenChange(false)}
                        className="bg-accent hover:bg-accent/90 text-white"
                    >
                        关闭
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
