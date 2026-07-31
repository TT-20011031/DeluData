/**
 * 通用确认对话框组件
 * 
 * 用于确认、错误、成功提示
 */
import { Loader2, CheckCircle } from 'lucide-react'
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

export type ConfirmDialogType = 'error' | 'confirm' | 'success'

interface ConfirmDialogProps {
    /** 对话框类型 */
    type: ConfirmDialogType
    /** 是否打开 */
    isOpen: boolean
    /** 关闭回调 */
    onClose: () => void
    /** 标题 */
    title: string
    /** 消息内容 */
    message: string
    /** 确认回调（仅 confirm 类型需要） */
    onConfirm?: () => Promise<void> | void
    /** 是否正在加载 */
    isLoading?: boolean
}

export function ConfirmDialog({
    type,
    isOpen,
    onClose,
    title,
    message,
    onConfirm,
    isLoading = false,
}: ConfirmDialogProps) {
    const handleConfirm = async () => {
        if (isLoading) return
        try {
            await onConfirm?.()
            onClose()
        } catch (e) {
            console.error(e)
        }
    }

    return (
        <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
            <DialogContent className="bg-manus-secondary border-manus-border">
                <DialogHeader>
                    <DialogTitle className={cn(
                        type === 'error' && "text-red-500",
                        type === 'success' && "text-green-500",
                        type === 'confirm' && "text-manus-text"
                    )}>
                        {type === 'success' && <CheckCircle className="h-5 w-5 inline mr-2" />}
                        {title}
                    </DialogTitle>
                    <DialogDescription className="text-manus-muted">
                        {message}
                    </DialogDescription>
                </DialogHeader>

                {type === 'confirm' && (
                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={onClose}
                            className="bg-manus border-manus-border text-manus-text"
                        >
                            取消
                        </Button>
                        <Button
                            onClick={handleConfirm}
                            disabled={isLoading}
                            className="bg-red-500 hover:bg-red-600 text-white"
                        >
                            {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            确认
                        </Button>
                    </DialogFooter>
                )}
            </DialogContent>
        </Dialog>
    )
}

/**
 * 确认对话框状态管理 Hook
 */
import { useState, useCallback } from 'react'

interface ConfirmDialogState {
    type: ConfirmDialogType
    isOpen: boolean
    title: string
    message: string
    isLoading: boolean
    onConfirm?: () => Promise<void> | void
}

const initialState: ConfirmDialogState = {
    type: 'error',
    isOpen: false,
    title: '',
    message: '',
    isLoading: false,
    onConfirm: undefined,
}

export function useConfirmDialog() {
    const [state, setState] = useState<ConfirmDialogState>(initialState)

    const showError = useCallback((message: string) => {
        setState({
            type: 'error',
            isOpen: true,
            title: '操作失败',
            message,
            isLoading: false,
            onConfirm: undefined,
        })
    }, [])

    const showConfirm = useCallback((message: string, onConfirm: () => Promise<void> | void) => {
        setState({
            type: 'confirm',
            isOpen: true,
            title: '确认操作',
            message,
            isLoading: false,
            onConfirm,
        })
    }, [])

    const showSuccess = useCallback((message: string) => {
        setState({
            type: 'success',
            isOpen: true,
            title: '操作成功',
            message,
            isLoading: false,
            onConfirm: undefined,
        })
        // 自动关闭
        setTimeout(() => {
            setState(prev => prev.type === 'success' ? { ...prev, isOpen: false } : prev)
        }, 1500)
    }, [])

    const close = useCallback(() => {
        setState(prev => ({ ...prev, isOpen: false }))
    }, [])

    const setLoading = useCallback((loading: boolean) => {
        setState(prev => ({ ...prev, isLoading: loading }))
    }, [])

    return {
        state,
        showError,
        showConfirm,
        showSuccess,
        close,
        setLoading,
    }
}
