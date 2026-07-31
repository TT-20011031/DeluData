/**
 * 浮动变量编辑工具栏
 * 
 * 点击文档预览区时显示，允许用户直接创建/编辑变量并绑定到选中位置
 */
import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { X, Check, Trash2, Type, Hash, Calendar, ImageIcon, TableIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import type { VariableItem } from '@/types/extendConfig'

const VARIABLE_TYPES = [
    { value: 'text', label: '文本', icon: Type },
    { value: 'number', label: '数字', icon: Hash },
    { value: 'date', label: '日期', icon: Calendar },
    { value: 'image', label: '图像', icon: ImageIcon },
    { value: 'table', label: '表格', icon: TableIcon },
]

interface FloatingToolbarProps {
    visible: boolean
    x: number
    y: number
    selectedText: string
    /** 当前位置已绑定的变量（如果有） */
    existingVariable?: VariableItem | null
    /** 创建新变量并绑定 */
    onCreateAndBind: (variable: VariableItem) => void
    /** 更新已绑定的变量 */
    onUpdateBinding: (oldName: string, variable: VariableItem) => void
    /** 解绑变量 */
    onUnbind: (varName: string) => void
    /** 关闭工具栏 */
    onClose: () => void
}

export function FloatingToolbar({
    visible,
    x,
    y,
    selectedText,
    existingVariable,
    onCreateAndBind,
    onUpdateBinding,
    onUnbind,
    onClose,
}: FloatingToolbarProps) {
    const [varName, setVarName] = useState('')
    const [varType, setVarType] = useState('text')
    const [varDesc, setVarDesc] = useState('')
    const [varMockValue, setVarMockValue] = useState('')

    // 当显示时重置表单
    useEffect(() => {
        if (visible) {
            if (existingVariable) {
                setVarName(existingVariable.name)
                setVarType(existingVariable.type || 'text')
                setVarDesc(existingVariable.desc || '')
                setVarMockValue(existingVariable.exampleValue || '')
            } else {
                // 智能猜测变量名：使用选中文本的前几个字符
                const suggestedName = selectedText
                    .replace(/[^\u4e00-\u9fa5a-zA-Z0-9]/g, '')
                    .slice(0, 10) || '新变量'
                setVarName(suggestedName)
                setVarType('text')
                setVarDesc('')
                setVarMockValue(selectedText || '')
            }
        }
    }, [visible, existingVariable, selectedText])

    if (!visible) return null

    const isEditing = !!existingVariable

    const handleSubmit = () => {
        if (!varName.trim()) return

        const variable: VariableItem = {
            name: varName.trim(),
            type: varType,
            desc: varDesc.trim(),
            exampleValue: varMockValue.trim(),
        }

        if (isEditing) {
            onUpdateBinding(existingVariable!.name, variable)
        } else {
            onCreateAndBind(variable)
        }
    }

    const handleUnbind = () => {
        if (existingVariable) {
            onUnbind(existingVariable.name)
        }
    }

    // 计算弹窗位置：显示在点击位置下方，确保不超出视口
    const popupWidth = 320
    const popupHeight = 340

    let left = Math.max(10, Math.min(x - popupWidth / 2, window.innerWidth - popupWidth - 10))
    let top = y + 15 // 显示在点击位置下方

    // 如果下方空间不够，显示在上方
    if (top + popupHeight > window.innerHeight - 10) {
        top = Math.max(10, y - popupHeight - 15)
    }

    const style: React.CSSProperties = {
        top,
        left,
    }

    const SelectedIcon = VARIABLE_TYPES.find(t => t.value === varType)?.icon || Type

    return createPortal(
        <div
            className="fixed z-[9999] bg-manus border border-manus-border shadow-2xl rounded-xl p-4 animate-in fade-in zoom-in-95 duration-200"
            style={{ ...style, width: popupWidth }}
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
            onMouseUp={(e) => e.stopPropagation()}
        >
            {/* 头部 */}
            <div className="flex items-center justify-between mb-3 pb-2 border-b border-manus-border">
                <span className="font-medium text-manus-text">
                    {isEditing ? '编辑变量' : '新建变量'}
                </span>
                <button
                    onClick={onClose}
                    className="p-1 hover:bg-manus-tertiary rounded transition-colors"
                >
                    <X className="h-4 w-4 text-manus-muted" />
                </button>
            </div>

            {/* 选中的文本提示 */}
            {selectedText && (
                <div className="mb-3 px-2 py-1.5 bg-accent/10 border border-accent/20 rounded text-xs text-accent truncate">
                    绑定位置: {selectedText}
                </div>
            )}

            {/* 表单 */}
            <div className="space-y-3">
                {/* 变量名 */}
                <div>
                    <Label className="text-[10px] text-manus-muted uppercase font-bold tracking-wider">
                        变量名 <span className="text-red-500">*</span>
                    </Label>
                    <Input
                        value={varName}
                        onChange={(e) => setVarName(e.target.value)}
                        placeholder="输入变量名..."
                        className="mt-1 h-8 bg-manus-tertiary/30 border-manus-border text-xs"
                        autoFocus
                        onKeyDown={(e) => {
                            if (e.key === 'Enter') handleSubmit()
                            if (e.key === 'Escape') onClose()
                        }}
                    />
                </div>

                {/* 类型选择 */}
                <div>
                    <Label className="text-[10px] text-manus-muted uppercase font-bold tracking-wider">
                        类型
                    </Label>
                    <Select value={varType} onValueChange={setVarType}>
                        <SelectTrigger className="mt-1 h-8 bg-manus-tertiary/30 border-manus-border text-xs">
                            <div className="flex items-center gap-2">
                                <SelectedIcon className="h-3.5 w-3.5" />
                                <SelectValue />
                            </div>
                        </SelectTrigger>
                        <SelectContent>
                            {VARIABLE_TYPES.map(t => {
                                const TIcon = t.icon
                                return (
                                    <SelectItem key={t.value} value={t.value}>
                                        <div className="flex items-center gap-2 text-xs">
                                            <TIcon className="h-3.5 w-3.5 opacity-70" />
                                            {t.label}
                                        </div>
                                    </SelectItem>
                                )
                            })}
                        </SelectContent>
                    </Select>
                </div>

                {/* 描述 */}
                <div>
                    <Label className="text-[10px] text-manus-muted uppercase font-bold tracking-wider">
                        描述
                    </Label>
                    <Input
                        value={varDesc}
                        onChange={(e) => setVarDesc(e.target.value)}
                        placeholder="描述变量用途..."
                        className="mt-1 h-8 bg-manus-tertiary/30 border-manus-border text-xs"
                    />
                </div>

                {/* Mock Value */}
                <div>
                    <Label className="text-[10px] text-manus-muted uppercase font-bold tracking-wider">
                        示例值
                    </Label>
                    <Input
                        value={varMockValue}
                        onChange={(e) => setVarMockValue(e.target.value)}
                        placeholder="用于预览的测试值..."
                        className="mt-1 h-8 bg-manus-tertiary/30 border-manus-border text-xs font-mono"
                    />
                </div>
            </div>

            {/* 操作按钮 */}
            <div className="flex items-center justify-between mt-4 pt-3 border-t border-manus-border">
                <div>
                    {isEditing && (
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={handleUnbind}
                            className="text-red-500 hover:text-red-600 hover:bg-red-500/10 h-7 text-xs"
                        >
                            <Trash2 className="h-3.5 w-3.5 mr-1" />
                            解绑
                        </Button>
                    )}
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={onClose} className="h-7 text-xs">
                        取消
                    </Button>
                    <Button
                        size="sm"
                        onClick={handleSubmit}
                        disabled={!varName.trim()}
                        className="h-7 text-xs bg-manus-text text-manus hover:bg-manus-text/90"
                    >
                        <Check className="h-3.5 w-3.5 mr-1" />
                        {isEditing ? '保存' : '确定'}
                    </Button>
                </div>
            </div>

            {/* Arrow 指示器 - 显示在顶部或底部取决于弹窗位置 */}
            {top > y && (
                <div className="absolute -top-2 left-1/2 -translate-x-1/2 w-0 h-0 border-l-[8px] border-l-transparent border-r-[8px] border-r-transparent border-b-[8px] border-b-manus" />
            )}
            {top < y && (
                <div className="absolute -bottom-2 left-1/2 -translate-x-1/2 w-0 h-0 border-l-[8px] border-l-transparent border-r-[8px] border-r-transparent border-t-[8px] border-t-manus" />
            )}
        </div>,
        document.body
    )
}
