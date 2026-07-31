/**
 * TemplateDialog 右侧面板组件
 * 
 * 包含：
 * - VariablesTabContent: 变量管理标签页
 * - SettingsTabContent: 基础配置标签页
 * - SettingsPanel: 面板容器
 */
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { VariableEditor } from './VariableEditor'
import { cn } from '@/lib/utils'
import type { TemplateForm, TemplateGroup, VariableItem } from '@/types/extendConfig'

// =============================================================================
// VariablesTabContent - 变量管理标签页
// =============================================================================

interface VariablesTabContentProps {
    variables: VariableItem[]
    onVariablesChange: (variables: VariableItem[]) => void
    /** 模板 ID，用于检测 API */
    templateId?: number | null
    /** 是否启用检测（仅 docx 模板） */
    enableDetection?: boolean
    /** 触发检测 */
    onDetectRequest?: () => void
    /** 是否正在检测 */
    isDetecting?: boolean
}

function VariablesTabContent({
    variables,
    onVariablesChange,
    templateId,
    enableDetection = false,
    onDetectRequest,
    isDetecting = false
}: VariablesTabContentProps) {
    const boundCount = variables.filter(v => v.location).length

    return (
        <TabsContent value="variables" className="flex-1 content-start overflow-hidden flex flex-col p-0 m-0 data-[state=inactive]:hidden data-[state=active]:flex">
            <div className="p-4 border-b border-manus-border bg-manus-tertiary/20">
                <div className="flex items-center justify-between mb-2">
                    <Label className="text-xs font-semibold uppercase text-manus-muted">绑定状态</Label>
                    <span className="text-xs text-manus-muted">
                        {boundCount} / {variables.length}
                    </span>
                </div>
                <div className="h-1.5 w-full bg-manus-tertiary rounded-full overflow-hidden">
                    <div
                        className="h-full bg-accent transition-all duration-500"
                        style={{ width: `${variables.length ? (boundCount / variables.length) * 100 : 0}%` }}
                    />
                </div>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
                <VariableEditor
                    value={variables}
                    onChange={onVariablesChange}
                    templateId={templateId}
                    enableDetection={enableDetection}
                    onDetectRequest={onDetectRequest}
                    isDetecting={isDetecting}
                />
            </div>
        </TabsContent>
    )
}

// =============================================================================
// SettingsTabContent - 基础配置标签页
// =============================================================================

interface SettingsTabContentProps {
    form: TemplateForm
    onFormChange: (form: TemplateForm) => void
    groups: TemplateGroup[]
    isEditing: boolean
    file: File | null
    onFileChange: (file: File | null) => void
}

function SettingsTabContent({
    form,
    onFormChange,
    groups,
    isEditing,
    file,
    onFileChange,
}: SettingsTabContentProps) {
    const updateField = <K extends keyof TemplateForm>(key: K, value: TemplateForm[K]) => {
        onFormChange({ ...form, [key]: value })
    }

    return (
        <TabsContent value="settings" className="flex-1 overflow-y-auto p-4 data-[state=inactive]:hidden">
            <div className="space-y-5">
                {/* Required Field: Template Name */}
                <div className="space-y-1.5">
                    <Label className="text-xs font-semibold text-manus-text flex items-center gap-1">
                        模板名称
                        <span className="text-red-500">*</span>
                    </Label>
                    <Input
                        value={form.name}
                        onChange={e => updateField('name', e.target.value)}
                        placeholder="请输入模板名称"
                        className={cn(
                            "h-9 bg-manus-secondary border-manus-border/50 focus:border-accent focus:ring-1 focus:ring-accent/30",
                            !form.name && "border-red-500/50 focus:border-red-500"
                        )}
                    />
                    {!form.name && (
                        <p className="text-[10px] text-red-500 mt-1">模板名称为必填项</p>
                    )}
                </div>

                {/* Group Selector */}
                <div className="space-y-1.5">
                    <Label className="text-xs font-semibold text-manus-text">分组</Label>
                    <select
                        value={form.group_id || ''}
                        onChange={(e) => updateField('group_id', e.target.value ? Number(e.target.value) : null)}
                        className="w-full h-9 px-3 bg-manus-secondary border border-manus-border/50 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30 text-manus-text rounded-md text-sm"
                    >
                        <option value="">未分组</option>
                        {groups.map(g => (
                            <option key={g.id} value={g.id}>{g.name}</option>
                        ))}
                    </select>
                </div>

                {/* Description */}
                <div className="space-y-1.5">
                    <Label className="text-xs font-semibold text-manus-text flex items-center gap-2">
                        功能描述
                        <span className="text-[10px] font-normal text-manus-muted">(可选)</span>
                    </Label>
                    <Input
                        value={form.description}
                        onChange={e => updateField('description', e.target.value)}
                        placeholder="简要描述模板用途"
                        className="h-9 bg-manus-secondary border-manus-border/50 focus:border-accent focus:ring-1 focus:ring-accent/30"
                    />
                </div>

                {/* Keywords */}
                <div className="space-y-1.5">
                    <Label className="text-xs font-semibold text-manus-text flex items-center gap-2">
                        关键词
                        <span className="text-[10px] font-normal text-manus-muted">(用于搜索)</span>
                    </Label>
                    <Input
                        value={form.keywords}
                        onChange={e => updateField('keywords', e.target.value)}
                        placeholder="多个关键词用逗号分隔"
                        className="h-9 bg-manus-secondary border-manus-border/50 focus:border-accent focus:ring-1 focus:ring-accent/30"
                    />
                </div>

                {/* File Re-select (for new templates) */}
                {!isEditing && (
                    <div className="pt-4 mt-2 border-t border-manus-border/30 space-y-1.5">
                        <Label className="text-xs font-semibold text-manus-text flex items-center gap-1">
                            重选文件
                            <span className="text-red-500">*</span>
                        </Label>
                        <div className="relative">
                            <Input
                                type="file"
                                accept=".docx,.xlsx"
                                onChange={e => onFileChange(e.target.files?.[0] || null)}
                                className="h-9 bg-manus-secondary border-manus-border/50 file:bg-manus-tertiary file:text-manus-text file:border-0 file:h-full file:px-3 file:mr-3 file:text-xs file:font-medium cursor-pointer"
                            />
                        </div>
                        {file && (
                            <p className="text-[10px] text-green-500 mt-1 flex items-center gap-1">
                                <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                                已选择: {file.name}
                            </p>
                        )}
                    </div>
                )}
            </div>
        </TabsContent>
    )
}

// =============================================================================
// SettingsPanel - 面板容器
// =============================================================================

interface SettingsPanelProps {
    form: TemplateForm
    onFormChange: (form: TemplateForm) => void
    variables: VariableItem[]
    onVariablesChange: (variables: VariableItem[]) => void
    groups: TemplateGroup[]
    isEditing: boolean
    file: File | null
    onFileChange: (file: File | null) => void
    /** 模板 ID，用于检测 API */
    templateId?: number | null
    /** 是否启用检测（仅 docx 模板） */
    enableDetection?: boolean
    /** 触发检测 */
    onDetectRequest?: () => void
    /** 是否正在检测 */
    isDetecting?: boolean
    /** 检测进度 (0-100) */
    detectionProgress?: number
    /** 检测阶段说明 */
    detectionStage?: string
}

export function SettingsPanel({
    form,
    onFormChange,
    variables,
    onVariablesChange,
    groups,
    isEditing,
    file,
    onFileChange,
    templateId,
    enableDetection = false,
    onDetectRequest,
    isDetecting = false,
    detectionProgress = 0,
    detectionStage = '',
}: SettingsPanelProps) {
    // 计算必填项是否有未填写
    const hasSettingsError = !form.name || (!isEditing && !file)
    const boundCount = variables.filter(v => v.location).length
    const totalVars = variables.length

    return (
        <div className="w-[360px] bg-manus border-l border-manus-border flex flex-col shadow-2xl z-10 relative">
            {/* 检测进度覆盖层 */}
            {isDetecting && (
                <div className="absolute inset-0 bg-manus/90 backdrop-blur-sm z-50 flex flex-col items-center justify-center gap-6">
                    {/* 动画图标 */}
                    <div className="relative w-20 h-20">
                        <div className="absolute inset-0 rounded-full border-4 border-accent/20" />
                        <div
                            className="absolute inset-0 rounded-full border-4 border-accent border-t-transparent animate-spin"
                            style={{ animationDuration: '1.2s' }}
                        />
                        <div className="absolute inset-0 flex items-center justify-center">
                            <span className="text-lg font-bold text-accent">
                                {Math.round(detectionProgress)}%
                            </span>
                        </div>
                    </div>

                    {/* 阶段说明 */}
                    <div className="text-center space-y-2">
                        <p className="text-sm font-medium text-manus-text">
                            {detectionStage || '正在分析模板...'}
                        </p>
                        <p className="text-xs text-manus-muted">
                            使用 AI 智能识别可填写区域
                        </p>
                    </div>

                    {/* 进度条 */}
                    <div className="w-48 h-1.5 bg-manus-tertiary rounded-full overflow-hidden">
                        <div
                            className="h-full bg-accent transition-all duration-500 ease-out"
                            style={{ width: `${detectionProgress}%` }}
                        />
                    </div>
                </div>
            )}

            <Tabs defaultValue="variables" className="flex-1 flex flex-col">
                <div className="flex-none px-4 py-3 border-b border-manus-border bg-manus-secondary/30">
                    <TabsList className="w-full grid grid-cols-2 h-10 p-1 bg-manus-tertiary rounded-xl gap-1">
                        {/* 变量管理 Tab */}
                        <TabsTrigger
                            value="variables"
                            className={cn(
                                "relative h-8 rounded-lg text-sm font-medium transition-all duration-200",
                                "data-[state=inactive]:text-manus-muted data-[state=inactive]:hover:text-manus-text data-[state=inactive]:hover:bg-manus-elevated/50",
                                "data-[state=active]:bg-accent data-[state=active]:text-white data-[state=active]:shadow-md data-[state=active]:shadow-accent/20"
                            )}
                        >
                            <span className="flex items-center gap-1.5">
                                变量管理
                                {totalVars > 0 && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-white/20 data-[state=inactive]:bg-manus-tertiary">
                                        {boundCount}/{totalVars}
                                    </span>
                                )}
                            </span>
                        </TabsTrigger>

                        {/* 基础配置 Tab */}
                        <TabsTrigger
                            value="settings"
                            className={cn(
                                "relative h-8 rounded-lg text-sm font-medium transition-all duration-200",
                                "data-[state=inactive]:text-manus-muted data-[state=inactive]:hover:text-manus-text data-[state=inactive]:hover:bg-manus-elevated/50",
                                "data-[state=active]:bg-accent data-[state=active]:text-white data-[state=active]:shadow-md data-[state=active]:shadow-accent/20"
                            )}
                        >
                            <span className="flex items-center gap-1.5">
                                基础配置
                                {/* 必填项未填提示徽标 */}
                                {hasSettingsError && (
                                    <span
                                        className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-red-500 rounded-full border-2 border-manus animate-pulse"
                                        title="有必填项未填写"
                                    />
                                )}
                            </span>
                        </TabsTrigger>
                    </TabsList>
                </div>

                <VariablesTabContent
                    variables={variables}
                    onVariablesChange={onVariablesChange}
                    templateId={templateId}
                    enableDetection={enableDetection}
                    onDetectRequest={onDetectRequest}
                    isDetecting={isDetecting}
                />

                <SettingsTabContent
                    form={form}
                    onFormChange={onFormChange}
                    groups={groups}
                    isEditing={isEditing}
                    file={file}
                    onFileChange={onFileChange}
                />
            </Tabs>
        </div>
    )
}

