/**
 * 文档样式管理组件
 *
 * 管理员设置工作空间默认样式，用户设置个人偏好。
 * 支持预设主题选择 + 细粒度自定义。
 */
import { useState, useEffect, useCallback } from 'react'
import { Palette, Save, RotateCcw, Loader2, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import { useToast } from '@/components/ui/toast'
import { useAuthStore } from '@/stores/authStore'
import { API_BASE_URL } from '@/config'
import { cn } from '@/lib/utils'

// ========== 常量 ==========

const PRESET_THEMES = [
    { value: 'modern_business', label: '现代商务' },
    { value: 'academic', label: '学术论文' },
    { value: 'clean_minimal', label: '简洁清爽' },
]

const ZH_FONTS = [
    { value: '微软雅黑', label: '微软雅黑' },
    { value: '宋体', label: '宋体' },
    { value: '黑体', label: '黑体' },
    { value: '楷体', label: '楷体' },
]

const EN_FONTS = [
    { value: 'Arial', label: 'Arial' },
    { value: 'Times New Roman', label: 'Times New Roman' },
    { value: 'Calibri', label: 'Calibri' },
]

const FONT_SIZES = [
    { value: '10.5', label: '10.5pt (五号)' },
    { value: '12', label: '12pt (小四)' },
    { value: '14', label: '14pt (四号)' },
]

const LINE_SPACINGS = [
    { value: '1', label: '1.0 倍' },
    { value: '1.15', label: '1.15 倍' },
    { value: '1.5', label: '1.5 倍' },
    { value: '2', label: '2.0 倍' },
]

const HEADING_COLORS = [
    { value: '1A1A1A', label: '经典黑', color: '#1A1A1A' },
    { value: '2B579A', label: '商务蓝', color: '#2B579A' },
    { value: 'C04851', label: '沉稳红', color: '#C04851' },
    { value: '2E7D32', label: '自然绿', color: '#2E7D32' },
]

const TABLE_THEMES = [
    { value: 'business_blue', label: '商务蓝', headerBg: '#2B579A', altBg: '#F2F7FB' },
    { value: 'elegant_gray', label: '素雅灰', headerBg: '#333333', altBg: '#F5F5F5' },
    { value: 'minimal_white', label: '简约白', headerBg: '#E8E8E8', altBg: '#FAFAFA' },
]

// ========== 类型 ==========

interface StyleFormData {
    preset_theme: string
    font_family_zh: string
    font_family_en: string
    font_size_pt: string
    line_spacing: string
    heading_color: string
    table_theme: string
}

const DEFAULT_FORM: StyleFormData = {
    preset_theme: 'modern_business',
    font_family_zh: '微软雅黑',
    font_family_en: 'Arial',
    font_size_pt: '12',
    line_spacing: '1.5',
    heading_color: '1A1A1A',
    table_theme: 'business_blue',
}

// ========== 辅助函数 ==========

function formToConfig(form: StyleFormData): Record<string, unknown> {
    const tableMap: Record<string, { bg: string; fg: string; alt: string }> = {
        business_blue: { bg: '2B579A', fg: 'FFFFFF', alt: 'F2F7FB' },
        elegant_gray: { bg: '333333', fg: 'FFFFFF', alt: 'F5F5F5' },
        minimal_white: { bg: 'E8E8E8', fg: '333333', alt: 'FAFAFA' },
    }
    const t = tableMap[form.table_theme] || tableMap.business_blue

    return {
        preset_theme: form.preset_theme,
        normal: {
            font_family_zh: form.font_family_zh,
            font_family_en: form.font_family_en,
            font_size_pt: parseFloat(form.font_size_pt),
            line_spacing: parseFloat(form.line_spacing),
        },
        heading_1: { color_hex: form.heading_color },
        heading_2: { color_hex: form.heading_color },
        heading_3: { color_hex: form.heading_color },
        table_header_bg: t.bg,
        table_header_fg: t.fg,
        table_alt_row_bg: t.alt,
    }
}

function configToForm(config: Record<string, unknown>): StyleFormData {
    const normal = (config.normal || {}) as Record<string, unknown>
    const h1 = (config.heading_1 || {}) as Record<string, unknown>

    let tableTheme = 'business_blue'
    const headerBg = (config.table_header_bg as string || '').toUpperCase()
    if (headerBg === '333333') tableTheme = 'elegant_gray'
    else if (headerBg === 'E8E8E8') tableTheme = 'minimal_white'

    return {
        preset_theme: (config.preset_theme as string) || 'modern_business',
        font_family_zh: (normal.font_family_zh as string) || '微软雅黑',
        font_family_en: (normal.font_family_en as string) || 'Arial',
        font_size_pt: String(normal.font_size_pt || 12),
        line_spacing: String(normal.line_spacing || 1.5),
        heading_color: (h1.color_hex as string) || '1A1A1A',
        table_theme: tableTheme,
    }
}

// ========== 组件 ==========

export function DocStyleManager() {
    const { token } = useAuthStore()
    const { toast } = useToast()
    const [isLoading, setIsLoading] = useState(true)
    const [isSaving, setIsSaving] = useState(false)
    const [form, setForm] = useState<StyleFormData>(DEFAULT_FORM)

    const headers = {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
    }

    const fetchConfig = useCallback(async () => {
        setIsLoading(true)
        try {
            const res = await fetch(`${API_BASE_URL}/config/doc-styles/workspace`, { headers })
            if (res.ok) {
                const data = await res.json()
                if (data.config && Object.keys(data.config).length > 0) {
                    setForm(configToForm(data.config))
                }
            }
        } catch (e) {
            console.error('获取文档样式配置失败:', e)
        } finally {
            setIsLoading(false)
        }
    }, [token])

    useEffect(() => {
        fetchConfig()
    }, [fetchConfig])

    const handleSave = async () => {
        setIsSaving(true)
        try {
            const config = formToConfig(form)
            const res = await fetch(`${API_BASE_URL}/config/doc-styles/workspace`, {
                method: 'PUT',
                headers,
                body: JSON.stringify({
                    preset_theme: form.preset_theme,
                    config,
                }),
            })
            if (res.ok) {
                toast({ type: 'success', title: '保存成功', description: '文档样式配置已更新' })
            } else {
                toast({ type: 'error', title: '保存失败', description: '请检查权限' })
            }
        } catch (e) {
            toast({ type: 'error', title: '保存失败', description: String(e) })
        } finally {
            setIsSaving(false)
        }
    }

    const handleReset = async () => {
        try {
            await fetch(`${API_BASE_URL}/config/doc-styles/workspace`, {
                method: 'DELETE',
                headers,
            })
            setForm(DEFAULT_FORM)
            toast({ type: 'success', title: '已重置', description: '已恢复为系统默认样式' })
        } catch (e) {
            toast({ type: 'error', title: '重置失败', description: String(e) })
        }
    }

    const updateField = (field: keyof StyleFormData, value: string) => {
        setForm(prev => ({ ...prev, [field]: value }))
    }

    if (isLoading) {
        return (
            <div className="flex items-center justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-manus-muted" />
            </div>
        )
    }

    return (
        <div className="space-y-6">
            {/* 标题区 */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <Palette className="h-5 w-5 text-accent" />
                    <h3 className="text-lg font-medium text-manus-text">文档生成样式</h3>
                    <span className="text-xs text-manus-muted">（适用于 AI 生成的 Word 文档）</span>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={handleReset}
                        className="border-manus-border text-manus-muted hover:text-error">
                        <RotateCcw className="h-3.5 w-3.5 mr-1" /> 重置
                    </Button>
                    <Button size="sm" onClick={handleSave} disabled={isSaving}
                        className="bg-accent hover:bg-accent/90 text-white">
                        {isSaving ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Save className="h-3.5 w-3.5 mr-1" />}
                        保存
                    </Button>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* 预设主题 */}
                <Card className="bg-manus-secondary border-manus-border">
                    <CardHeader className="pb-3">
                        <h4 className="text-sm font-medium text-manus-text">预设主题</h4>
                    </CardHeader>
                    <CardContent>
                        <div className="grid grid-cols-3 gap-3">
                            {PRESET_THEMES.map(t => (
                                <button
                                    key={t.value}
                                    onClick={() => updateField('preset_theme', t.value)}
                                    className={cn(
                                        "relative p-3 rounded-lg border text-center text-sm transition-all",
                                        form.preset_theme === t.value
                                            ? "border-accent bg-accent/10 text-accent"
                                            : "border-manus-border bg-manus text-manus-muted hover:border-manus-border-strong"
                                    )}
                                >
                                    {form.preset_theme === t.value && (
                                        <Check className="absolute top-1.5 right-1.5 h-3.5 w-3.5 text-accent" />
                                    )}
                                    {t.label}
                                </button>
                            ))}
                        </div>
                    </CardContent>
                </Card>

                {/* 字体配置 */}
                <Card className="bg-manus-secondary border-manus-border">
                    <CardHeader className="pb-3">
                        <h4 className="text-sm font-medium text-manus-text">字体配置</h4>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="text-xs text-manus-muted mb-1 block">中文字体</label>
                                <Select value={form.font_family_zh} onValueChange={v => updateField('font_family_zh', v)}>
                                    <SelectTrigger className="bg-manus border-manus-border"><SelectValue /></SelectTrigger>
                                    <SelectContent className="bg-manus-secondary border-manus-border">
                                        {ZH_FONTS.map(f => <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div>
                                <label className="text-xs text-manus-muted mb-1 block">英文字体</label>
                                <Select value={form.font_family_en} onValueChange={v => updateField('font_family_en', v)}>
                                    <SelectTrigger className="bg-manus border-manus-border"><SelectValue /></SelectTrigger>
                                    <SelectContent className="bg-manus-secondary border-manus-border">
                                        {EN_FONTS.map(f => <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="text-xs text-manus-muted mb-1 block">正文字号</label>
                                <Select value={form.font_size_pt} onValueChange={v => updateField('font_size_pt', v)}>
                                    <SelectTrigger className="bg-manus border-manus-border"><SelectValue /></SelectTrigger>
                                    <SelectContent className="bg-manus-secondary border-manus-border">
                                        {FONT_SIZES.map(f => <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div>
                                <label className="text-xs text-manus-muted mb-1 block">行距</label>
                                <Select value={form.line_spacing} onValueChange={v => updateField('line_spacing', v)}>
                                    <SelectTrigger className="bg-manus border-manus-border"><SelectValue /></SelectTrigger>
                                    <SelectContent className="bg-manus-secondary border-manus-border">
                                        {LINE_SPACINGS.map(f => <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                {/* 标题颜色 */}
                <Card className="bg-manus-secondary border-manus-border">
                    <CardHeader className="pb-3">
                        <h4 className="text-sm font-medium text-manus-text">标题颜色</h4>
                    </CardHeader>
                    <CardContent>
                        <div className="flex gap-3">
                            {HEADING_COLORS.map(c => (
                                <button
                                    key={c.value}
                                    onClick={() => updateField('heading_color', c.value)}
                                    className={cn(
                                        "flex items-center gap-2 px-3 py-2 rounded-lg border text-sm transition-all",
                                        form.heading_color === c.value
                                            ? "border-accent bg-accent/10"
                                            : "border-manus-border bg-manus hover:border-manus-border-strong"
                                    )}
                                >
                                    <span
                                        className="w-4 h-4 rounded-full border border-manus-border"
                                        style={{ backgroundColor: c.color }}
                                    />
                                    <span className="text-manus-text">{c.label}</span>
                                </button>
                            ))}
                        </div>
                    </CardContent>
                </Card>

                {/* 表格主题 */}
                <Card className="bg-manus-secondary border-manus-border">
                    <CardHeader className="pb-3">
                        <h4 className="text-sm font-medium text-manus-text">表格主题</h4>
                    </CardHeader>
                    <CardContent>
                        <div className="grid grid-cols-3 gap-3">
                            {TABLE_THEMES.map(t => (
                                <button
                                    key={t.value}
                                    onClick={() => updateField('table_theme', t.value)}
                                    className={cn(
                                        "relative p-3 rounded-lg border text-center text-sm transition-all",
                                        form.table_theme === t.value
                                            ? "border-accent bg-accent/10"
                                            : "border-manus-border bg-manus hover:border-manus-border-strong"
                                    )}
                                >
                                    {/* 迷你表格预览 */}
                                    <div className="mb-2 rounded overflow-hidden border border-manus-border">
                                        <div className="h-3" style={{ backgroundColor: t.headerBg }} />
                                        <div className="h-2 bg-white" />
                                        <div className="h-2" style={{ backgroundColor: t.altBg }} />
                                        <div className="h-2 bg-white" />
                                    </div>
                                    <span className="text-manus-text text-xs">{t.label}</span>
                                    {form.table_theme === t.value && (
                                        <Check className="absolute top-1.5 right-1.5 h-3.5 w-3.5 text-accent" />
                                    )}
                                </button>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    )
}
