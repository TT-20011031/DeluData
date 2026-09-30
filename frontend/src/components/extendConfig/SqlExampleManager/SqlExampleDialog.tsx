/**
 * SQL 示例编辑对话框
 */
import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, Loader2, Save, ShieldCheck, X } from 'lucide-react'
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
import { sqlExampleService } from '@/services/sqlExampleService'
import type { SqlExampleForm, SqlExampleValidationResult, SqlGroup } from '@/types/extendConfig'

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
    onSave: (activate: boolean, form?: SqlExampleForm) => void
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
    const [isValidating, setIsValidating] = useState(false)
    const [validation, setValidation] = useState<SqlExampleValidationResult | null>(null)

    useEffect(() => {
        if (!open) setValidation(null)
    }, [open])

    const updateField = <K extends keyof SqlExampleForm>(key: K, value: SqlExampleForm[K]) => {
        onFormChange({ ...form, [key]: value })
        if (key === 'question' || key === 'sql' || key === 'parameters') setValidation(null)
    }

    const validateAndEnable = async () => {
        if (!form.question.trim() || !form.sql.trim()) return
        setIsValidating(true)
        try {
            const result = await sqlExampleService.validate(form)
            setValidation(result)
            const nextForm = { ...form, parameters: result.parameters, is_active: result.status === 'valid' }
            onFormChange(nextForm)
            const parametersAlreadyConfirmed = form.parameters.length > 0 || result.parameters.length === 0
            if (result.status === 'valid' && parametersAlreadyConfirmed) onSave(true, nextForm)
        } catch (error) {
            setValidation({
                status: 'invalid',
                errors: [{ code: 'request_failed', message: error instanceof Error ? error.message : '校验失败' }],
                parameters: form.parameters,
                normalized_question: form.question,
                preview_sql: '',
            })
        } finally {
            setIsValidating(false)
        }
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

                    {form.parameters.length > 0 && (
                        <div className="space-y-2 rounded-lg border border-manus-border bg-manus p-3">
                            <div>
                                <Label className="text-manus-text">动态参数确认</Label>
                                <p className="text-xs text-manus-muted mt-1">
                                    系统已从问题和 SQL 中识别参数；请确认名称和对应语义字段。
                                </p>
                            </div>
                            {form.parameters.map((parameter, index) => (
                                <div key={`${parameter.key}-${parameter.column_id}`} className="grid grid-cols-[1fr_1.4fr_auto] gap-2 items-center">
                                    <Input
                                        value={parameter.label}
                                        onChange={(event) => {
                                            const parameters = [...form.parameters]
                                            parameters[index] = { ...parameter, label: event.target.value }
                                            updateField('parameters', parameters)
                                        }}
                                        className="bg-manus-secondary border-manus-border text-manus-text"
                                    />
                                    <Input
                                        value={parameter.field}
                                        readOnly
                                        title="参数只能绑定当前账号有权查询的语义字段"
                                        className="bg-manus-secondary border-manus-border text-manus-muted"
                                    />
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        onClick={() => updateField('parameters', form.parameters.filter((_, itemIndex) => itemIndex !== index))}
                                        className="text-manus-muted hover:text-red-500"
                                    >
                                        <X className="h-4 w-4" />
                                    </Button>
                                </div>
                            ))}
                        </div>
                    )}

                    {validation && (
                        <div className={cn(
                            'rounded-lg border p-3 text-sm',
                            validation.status === 'valid'
                                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400'
                                : 'border-red-500/40 bg-red-500/10 text-red-400',
                        )}>
                            <div className="flex items-center gap-2 font-medium">
                                {validation.status === 'valid'
                                    ? <CheckCircle2 className="h-4 w-4" />
                                    : <AlertCircle className="h-4 w-4" />}
                                {validation.status === 'valid' ? '校验通过，可以启用' : '校验未通过，已保留为草稿'}
                            </div>
                            {validation.errors.length > 0 && (
                                <ul className="mt-2 space-y-1 list-disc pl-5">
                                    {validation.errors.map((error, index) => <li key={`${error.code}-${index}`}>{error.message}</li>)}
                                </ul>
                            )}
                            {validation.preview_sql && (
                                <div className="mt-3">
                                    <p className="mb-1 text-xs font-medium">按当前账号权限生成的安全预览</p>
                                    <pre className="max-h-36 overflow-auto whitespace-pre-wrap rounded bg-black/20 p-2 font-mono text-xs text-manus-text">
                                        {validation.preview_sql}
                                    </pre>
                                </div>
                            )}
                        </div>
                    )}

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
                        variant="outline"
                        onClick={() => onSave(false, { ...form, is_active: false })}
                        disabled={isSaving}
                        className="bg-manus border-manus-border text-manus-text"
                    >
                        {isSaving ? (
                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        ) : (
                            <Save className="h-4 w-4 mr-2" />
                        )}
                        保存草稿
                    </Button>
                    <Button
                        onClick={validateAndEnable}
                        disabled={isSaving || isValidating}
                        className="bg-accent hover:bg-accent/90 text-white"
                    >
                        {isValidating || isSaving ? (
                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        ) : (
                            <ShieldCheck className="h-4 w-4 mr-2" />
                        )}
                        {form.parameters.length > 0
                            ? '确认参数并启用'
                            : '校验并启用'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
