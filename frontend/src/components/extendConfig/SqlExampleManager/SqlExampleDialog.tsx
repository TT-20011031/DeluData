/**
 * SQL 示例编辑对话框
 */
import { Loader2, Save } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import type { SqlExampleForm, SqlGroup } from '@/types/extendConfig'

interface SqlExampleDialogProps {
    /** 是否打开 */
    open: boolean
    /** 关闭回调 */
    onClose: () => void
    /** 表单数据 */
    form: SqlExampleForm
    /** 表单变化回调 */
    onFormChange: (form: SqlExampleForm) => void
    /** 保存回调 */
    onSave: () => void
    /** 是否正在保存 */
    isSaving: boolean
    /** 是否编辑模式 */
    isEditing: boolean
    /** 可选的分组列表 */
    groups: SqlGroup[]
}

export function SqlExampleDialog({
    open,
    onClose,
    form,
    onFormChange,
    onSave,
    isSaving,
    isEditing,
    groups,
}: SqlExampleDialogProps) {
    const updateField = <K extends keyof SqlExampleForm>(key: K, value: SqlExampleForm[K]) => {
        onFormChange({ ...form, [key]: value })
    }

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="bg-manus-secondary border-manus-border max-w-2xl">
                <DialogHeader>
                    <DialogTitle className="text-manus-text">
                        {isEditing ? '编辑 SQL 示例' : '新建 SQL 示例'}
                    </DialogTitle>
                    <DialogDescription className="text-manus-muted">
                        添加问题描述和对应的 SQL
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-4">
                    <div className="space-y-2">
                        <Label className="text-manus-text">问题描述 *</Label>
                        <Input
                            placeholder="例如：查询本月销售额前10的产品"
                            value={form.question}
                            onChange={(e) => updateField('question', e.target.value)}
                            className="bg-manus border-manus-border text-manus-text"
                        />
                    </div>

                    <div className="space-y-2">
                        <Label className="text-manus-text">示例 SQL *</Label>
                        <Textarea
                            placeholder="SELECT product_name, SUM(amount) as total..."
                            value={form.sql}
                            onChange={(e) => updateField('sql', e.target.value)}
                            className="bg-manus border-manus-border text-manus-text font-mono min-h-[120px]"
                        />
                    </div>

                    <div className="space-y-2">
                        <Label className="text-manus-text">补充说明</Label>
                        <Input
                            placeholder="可选：补充说明查询逻辑"
                            value={form.description}
                            onChange={(e) => updateField('description', e.target.value)}
                            className="bg-manus border-manus-border text-manus-text"
                        />
                    </div>

                    <div className="space-y-2">
                        <Label className="text-manus-text">涉及的表</Label>
                        <Input
                            placeholder="例如：orders, products"
                            value={form.tables}
                            onChange={(e) => updateField('tables', e.target.value)}
                            className="bg-manus border-manus-border text-manus-text"
                        />
                    </div>

                    <div className="space-y-2">
                        <Label className="text-manus-text">所属分组</Label>
                        <div className="flex flex-wrap gap-2">
                            <button
                                type="button"
                                onClick={() => updateField('group_id', null)}
                                className={cn(
                                    "px-3 py-1.5 rounded-full text-sm border transition-all",
                                    form.group_id === null
                                        ? "bg-accent text-white border-accent"
                                        : "border-manus-border text-manus-muted hover:text-manus-text"
                                )}
                            >
                                未分组
                            </button>
                            {groups.map(g => (
                                <button
                                    key={g.id}
                                    type="button"
                                    onClick={() => updateField('group_id', g.id)}
                                    className={cn(
                                        "px-3 py-1.5 rounded-full text-sm border transition-all",
                                        form.group_id === g.id
                                            ? "scale-105"
                                            : "opacity-70 hover:opacity-100"
                                    )}
                                    style={{
                                        borderColor: g.color,
                                        backgroundColor: form.group_id === g.id ? g.color : 'transparent',
                                        color: form.group_id === g.id ? 'white' : g.color
                                    }}
                                >
                                    {g.name}
                                </button>
                            ))}
                        </div>
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
