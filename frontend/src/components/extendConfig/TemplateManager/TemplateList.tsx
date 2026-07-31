/**
 * 模板列表组件
 */
import { Edit, Trash2, Play, FileText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import type { Template } from '@/types/extendConfig'

interface TemplateListProps {
    /** 模板列表 */
    templates: Template[]
    /** 编辑回调 */
    onEdit: (template: Template) => void
    /** 删除回调 */
    onDelete: (id: number) => void
    /** 切换启用状态回调 */
    onToggleActive: (template: Template) => void
    /** 测试渲染回调 */
    onTestRender: (template: Template) => void
    /** 是否正在测试渲染 */
    isTestingRender: boolean
    /** 是否按分组筛选 */
    isFiltered: boolean
}

export function TemplateList({
    templates,
    onEdit,
    onDelete,
    onToggleActive,
    onTestRender,
    isTestingRender,
    isFiltered,
}: TemplateListProps) {
    if (templates.length === 0) {
        return (
            <div className="text-center py-12 text-manus-muted">
                <FileText className="h-12 w-12 mx-auto mb-4 opacity-50" />
                <p>{isFiltered ? '该分组暂无模板' : '暂无模板'}</p>
                <p className="text-sm mt-2">上传 .docx 或 .xlsx 模板文件</p>
            </div>
        )
    }

    return (
        <ScrollArea className="h-[400px]">
            <div className="space-y-4">
                {templates.map((template) => (
                    <div
                        key={template.id}
                        className={cn(
                            "p-4 rounded-lg border transition-colors",
                            template.is_active
                                ? "bg-manus-tertiary border-manus-border"
                                : "bg-manus border-manus-border/50 opacity-60"
                        )}
                    >
                        <div className="flex items-start justify-between gap-4">
                            {/* 内容 */}
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                    <h3 className="font-medium text-manus-text">
                                        {template.name}
                                    </h3>
                                    <span className="text-xs px-2 py-0.5 rounded bg-accent/20 text-accent">
                                        {template.file_type.toUpperCase()}
                                    </span>
                                </div>
                                {template.description && (
                                    <p className="text-sm text-manus-muted mt-1">
                                        {template.description}
                                    </p>
                                )}
                                {template.keywords && (
                                    <p className="text-xs text-manus-muted mt-1">
                                        关键词: {template.keywords}
                                    </p>
                                )}
                                {template.variables_schema && Object.keys(template.variables_schema).length > 0 && (
                                    <p className="text-xs text-manus-muted mt-1">
                                        变量: {Object.keys(template.variables_schema).join(', ')}
                                    </p>
                                )}
                            </div>

                            {/* 操作按钮 */}
                            <div className="flex items-center gap-2 shrink-0">
                                <Switch
                                    checked={template.is_active}
                                    onCheckedChange={() => onToggleActive(template)}
                                />
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => onTestRender(template)}
                                    disabled={isTestingRender || !template.example_context}
                                    className="text-manus-muted hover:text-manus-text"
                                >
                                    <Play className="h-4 w-4 mr-1" />
                                    测试
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => onEdit(template)}
                                    className="text-manus-muted hover:text-manus-text"
                                >
                                    <Edit className="h-4 w-4" />
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => onDelete(template.id)}
                                    className="text-manus-muted hover:text-red-500"
                                >
                                    <Trash2 className="h-4 w-4" />
                                </Button>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </ScrollArea>
    )
}
