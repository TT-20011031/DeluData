/**
 * 删除确认对话框
 */
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '@/components/ui/dialog'
import type { DeleteState } from '@/hooks/knowledge'

export interface DeleteConfirmDialogProps {
    state: DeleteState
    onConfirm: () => void
    onCancel: () => void
}

export function DeleteConfirmDialog({ state, onConfirm, onCancel }: DeleteConfirmDialogProps) {
    const isOpen = !!state.target || state.batchTargets.length > 0

    const getDescription = () => {
        if (state.batchTargets.length > 0) {
            return `此操作无法撤销。确定要删除选中的 ${state.batchTargets.length} 个项目吗？`
        }
        if (state.target?.type === 'folder') {
            return '此操作无法撤销。删除文件夹将同时删除其中所有文件。'
        }
        return '此操作无法撤销。确定要删除此文件吗？'
    }

    return (
        <Dialog open={isOpen} onOpenChange={(open) => !open && onCancel()}>
            <DialogContent className="bg-manus-secondary border-manus-border text-manus-text">
                <DialogHeader>
                    <DialogTitle>确认删除</DialogTitle>
                    <DialogDescription>{getDescription()}</DialogDescription>
                </DialogHeader>
                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={onCancel}
                        className="bg-transparent border-manus-border text-manus-text hover:bg-manus-tertiary"
                    >
                        取消
                    </Button>
                    <Button
                        variant="destructive"
                        onClick={onConfirm}
                        disabled={state.isDeleting}
                        className="bg-red-600 hover:bg-red-700 text-white"
                    >
                        {state.isDeleting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        确认删除
                        {state.batchTargets.length > 0 ? ` (${state.batchTargets.length}项)` : ''}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
