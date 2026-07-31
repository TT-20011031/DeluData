import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
    DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Loader2 } from "lucide-react"

interface ConfirmDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    title: string
    description?: React.ReactNode
    onConfirm: () => void
    loading?: boolean
    confirmText?: string
    cancelText?: string
    variant?: 'default' | 'destructive'
}

export function ConfirmDialog({
    open,
    onOpenChange,
    title,
    description,
    onConfirm,
    loading = false,
    confirmText = "确定",
    cancelText = "取消",
    variant = 'default'
}: ConfirmDialogProps) {
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="bg-manus-secondary border-manus-border sm:max-w-[425px]">
                <DialogHeader>
                    <DialogTitle className="text-manus-text">{title}</DialogTitle>
                    {description && (
                        <DialogDescription className="text-manus-muted pt-2 text-start">
                            {description}
                        </DialogDescription>
                    )}
                </DialogHeader>
                <DialogFooter className="gap-2 sm:gap-0 mt-4">
                    <Button
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={loading}
                        className="bg-manus-tertiary border-manus-border text-manus-text hover:bg-manus-hover"
                    >
                        {cancelText}
                    </Button>
                    <Button
                        onClick={onConfirm}
                        disabled={loading}
                        className={variant === 'destructive'
                            ? "bg-red-600 hover:bg-red-700 text-white"
                            : "bg-accent hover:bg-accent/90 text-white"}
                    >
                        {loading && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                        {confirmText}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
