/**
 * 模板分组编辑对话框
 */
import { Loader2, Save } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '@/components/ui/dialog'
import type { TemplateGroupForm } from '@/types/extendConfig'

interface TemplateGroupDialogProps {
    /** 是否打开 */
    open: boolean
    /** 关闭回调 */
    onClose: () => void
    /** 表单数据 */
    form: TemplateGroupForm
    /** 表单变化回调 */
    onFormChange: (form: TemplateGroupForm) => void
    /** 保存回调 */
    onSave: () => void
    /** 是否正在保存 */
    isSaving: boolean
    /** 是否编辑模式 */
    isEditing: boolean
}

export function TemplateGroupDialog({
    open,
    onClose,
    form,
    onFormChange,
    onSave,
    isSaving,
    isEditing,
}: TemplateGroupDialogProps) {
    const updateField = <K extends keyof TemplateGroupForm>(key: K, value: TemplateGroupForm[K]) => {
        onFormChange({ ...form, [key]: value })
    }

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="bg-manus-secondary border-manus-border max-w-md">
                <DialogHeader>
                    <DialogTitle className="text-manus-text">
                        {isEditing ? '编辑分组' : '新建分组'}
                    </DialogTitle>
                    <DialogDescription className="text-manus-muted">
                        为模板创建分组便于管理
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-4">
                    <div className="space-y-2">
                        <Label className="text-manus-text">分组名称 *</Label>
                        <Input
                            placeholder="例如：财务报表"
                            value={form.name}
                            onChange={(e) => updateField('name', e.target.value)}
                            className="bg-manus border-manus-border text-manus-text"
                        />
                    </div>

                    <div className="space-y-2">
                        <Label className="text-manus-text">描述</Label>
                        <Input
                            placeholder="分组用途说明"
                            value={form.description}
                            onChange={(e) => updateField('description', e.target.value)}
                            className="bg-manus border-manus-border text-manus-text"
                        />
                    </div>

                    <div className="space-y-2">
                        <Label className="text-manus-text">分组颜色</Label>
                        <Input
                            type="color"
                            value={form.color}
                            onChange={(e) => updateField('color', e.target.value)}
                            className="bg-manus border-manus-border text-manus-text h-10"
                        />
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
                        onClick={onSave}
                        disabled={isSaving}
                        className="bg-accent hover:bg-accent/90 text-white"
                    >
                        {isSaving ? (
                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        ) : (
                            <Save className="h-4 w-4 mr-2" />
                        )}
                        保存
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
