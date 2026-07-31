/**
 * 批量移动对话框
 */
import { Loader2, FolderPlus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import type { SqlGroup } from '@/types/extendConfig'

interface BatchMoveDialogProps {
    /** 是否打开 */
    open: boolean
    /** 关闭回调 */
    onClose: () => void
    /** 选中的数量 */
    selectedCount: number
    /** 分组列表 */
    groups: SqlGroup[]
    /** 目标分组 ID */
    targetGroupId: number | null
    /** 目标分组变化回调 */
    onTargetChange: (id: number | null) => void
    /** 确认回调 */
    onConfirm: () => void
    /** 是否正在移动 */
    isMoving: boolean
}

export function BatchMoveDialog({
    open,
    onClose,
    selectedCount,
    groups,
    targetGroupId,
    onTargetChange,
    onConfirm,
    isMoving,
}: BatchMoveDialogProps) {
    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="bg-manus-secondary border-manus-border max-w-md">
                <DialogHeader>
                    <DialogTitle className="text-manus-text">
                        移动到分组
                    </DialogTitle>
                    <DialogDescription className="text-manus-muted">
                        将选中的 {selectedCount} 条示例移动到指定分组
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-4">
                    <div className="flex flex-wrap gap-2">
                        <button
                            onClick={() => onTargetChange(null)}
                            className={cn(
                                "px-4 py-2 rounded-lg text-sm border transition-all",
                                targetGroupId === null
                                    ? "bg-accent text-white border-accent"
                                    : "border-manus-border text-manus-muted hover:text-manus-text"
                            )}
                        >
                            未分组
                        </button>
                        {groups.map(g => (
                            <button
                                key={g.id}
                                onClick={() => onTargetChange(g.id)}
                                className={cn(
                                    "px-4 py-2 rounded-lg text-sm border transition-all",
                                    targetGroupId === g.id
                                        ? "scale-105"
                                        : "opacity-70 hover:opacity-100"
                                )}
                                style={{
                                    borderColor: g.color,
                                    backgroundColor: targetGroupId === g.id ? g.color : 'transparent',
                                    color: targetGroupId === g.id ? 'white' : g.color
                                }}
                            >
                                {g.name}
                            </button>
                        ))}
                    </div>
                </div>

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={onClose}
                        className="bg-manus border-manus-border text-manus-text"
                    >
                        取消
                    </Button>
                    <Button
                        onClick={onConfirm}
                        disabled={isMoving}
                        className="bg-accent hover:bg-accent/90 text-white"
                    >
                        {isMoving ? (
                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        ) : (
                            <FolderPlus className="h-4 w-4 mr-2" />
                        )}
                        确认移动
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
