/**
 * 关联创建对话框
 */
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import type { ConnectState } from '@/hooks/knowledge'
import type { RelationType } from '@/types/knowledge'

const PRESET_RELATION_TYPES = ['关联', '引用', '补充', '冲突', '前置'] as const

export interface RelationDialogProps {
    state: ConnectState
    onRelationTypeChange: (type: RelationType) => void
    onCustomRelationChange: (value: string) => void
    onConfirm: () => void
    onCancel: () => void
}

export function RelationDialog({
    state,
    onRelationTypeChange,
    onCustomRelationChange,
    onConfirm,
    onCancel,
}: RelationDialogProps) {
    const isOpen = !!state.params

    return (
        <Dialog open={isOpen} onOpenChange={(open) => !open && onCancel()}>
            <DialogContent className="bg-manus-secondary border-manus-border text-manus-text">
                <DialogHeader>
                    <DialogTitle>创建关联</DialogTitle>
                    <DialogDescription>请选择或输入两个文档之间的关联类型。</DialogDescription>
                </DialogHeader>

                <div className="py-4 space-y-4">
                    <div className="flex flex-wrap gap-2">
                        {PRESET_RELATION_TYPES.map((type) => (
                            <Button
                                key={type}
                                variant={state.relationType === type ? 'default' : 'outline'}
                                onClick={() => {
                                    onRelationTypeChange(type)
                                    onCustomRelationChange('')
                                }}
                                className={cn(
                                    'h-8 text-xs',
                                    state.relationType === type
                                        ? 'bg-accent text-white hover:bg-accent/90 border-transparent'
                                        : 'bg-transparent border-manus-border text-manus-text hover:bg-manus-tertiary'
                                )}
                            >
                                {type}
                            </Button>
                        ))}
                        <Button
                            variant={state.relationType === 'custom' ? 'default' : 'outline'}
                            onClick={() => onRelationTypeChange('custom')}
                            className={cn(
                                'h-8 text-xs',
                                state.relationType === 'custom'
                                    ? 'bg-accent text-white hover:bg-accent/90 border-transparent'
                                    : 'bg-transparent border-manus-border text-manus-text hover:bg-manus-tertiary'
                            )}
                        >
                            自定义...
                        </Button>
                    </div>

                    {state.relationType === 'custom' && (
                        <Input
                            placeholder="输入自定义关联类型..."
                            value={state.customRelation}
                            onChange={(e) => onCustomRelationChange(e.target.value)}
                            className="bg-manus-tertiary border-manus-border text-manus-text"
                            autoFocus
                        />
                    )}
                </div>

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={onCancel}
                        className="bg-transparent border-manus-border text-manus-text hover:bg-manus-tertiary"
                    >
                        取消
                    </Button>
                    <Button onClick={onConfirm} className="bg-accent hover:bg-accent/90 text-white">
                        创建连接
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
