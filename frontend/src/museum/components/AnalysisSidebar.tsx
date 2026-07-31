/**
 * 智能客群洞察侧边栏
 * 
 * 博物馆导览页面左侧面板，包含：
 * - 场景快照（上传图片预览）
 * - 分析步骤展示
 * - 人物洞察卡片
 * - 策略建议面板
 */
import { useRef, useState } from 'react'
import { generateId } from '@/utils/id'
import { Upload, Users, Target, Loader2, ImagePlus, X, CheckCircle2, Circle, Camera } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { useGuideStore } from '../stores/guideStore'
import { PersonCard } from './PersonCard'
import { StrategyPanel } from './StrategyPanel'
import { CameraCapture } from './CameraCapture'
import type { SceneImage } from '../types'

interface AnalysisSidebarProps {
    onImageUpload: (file: File) => Promise<void>
    isAnalyzing: boolean
    className?: string
}

export function AnalysisSidebar({
    onImageUpload,
    isAnalyzing,
    className,
}: AnalysisSidebarProps) {
    const fileInputRef = useRef<HTMLInputElement>(null)
    const [isDragOver, setIsDragOver] = useState(false)

    const {
        sceneImage,
        sceneAnalysis,
        analysisSteps,
        highlightedPersonId,
        setSceneImage,
        setHighlightedPersonId,
    } = useGuideStore()

    const handleFileSelect = async (file: File) => {
        if (!file.type.startsWith('image/')) return

        // 创建预览
        const previewUrl = URL.createObjectURL(file)
        const newImage: SceneImage = {
            id: generateId(),
            url: previewUrl,
            thumbnailUrl: previewUrl,
            uploadedAt: Date.now(),
        }
        setSceneImage(newImage)

        // 触发上传和分析
        await onImageUpload(file)
    }

    const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]
        if (file) handleFileSelect(file)
        e.target.value = ''
    }

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault()
        setIsDragOver(false)
        const file = e.dataTransfer.files?.[0]
        if (file) handleFileSelect(file)
    }

    const handleClearImage = () => {
        setSceneImage(null)
        useGuideStore.getState().setSceneAnalysis(null)
        useGuideStore.getState().clearAnalysisSteps()
    }

    const hasAnalysis = sceneAnalysis !== null

    return (
        <div className={cn("flex flex-col h-full bg-manus-secondary border-r border-manus-border", className)}>
            {/* 标题 */}
            <div className="h-14 flex items-center px-4 border-b border-manus-border shrink-0">
                <div className="flex items-center gap-2">
                    <Users className="h-4 w-4 text-accent" />
                    <span className="text-sm font-medium text-manus-text">智能客群洞察</span>
                </div>
            </div>

            <ScrollArea className="flex-1">
                <div className="p-4 space-y-4">
                    {/* 场景快照区 */}
                    <div className="space-y-2">
                        <div className="text-xs font-medium text-manus-muted uppercase tracking-wide">
                            场景快照
                        </div>

                        {sceneImage ? (
                            // 已上传图片
                            <div className="relative group">
                                <div className="aspect-video rounded-xl overflow-hidden bg-manus-tertiary border border-manus-border">
                                    <img
                                        src={sceneImage.url}
                                        alt="场景"
                                        className="w-full h-full object-cover"
                                    />
                                    {/* 人物边框高亮 (当有分析结果时) */}
                                    {sceneAnalysis?.persons.map(person => (
                                        <div
                                            key={person.id}
                                            className={cn(
                                                "absolute border-2 rounded transition-all pointer-events-none",
                                                person.id === highlightedPersonId
                                                    ? "border-accent bg-accent/20"
                                                    : person.id === sceneAnalysis.target_person_id
                                                        ? "border-accent/50"
                                                        : "border-transparent"
                                            )}
                                            style={{
                                                left: `${person.bbox[0] * 100}%`,
                                                top: `${person.bbox[1] * 100}%`,
                                                width: `${(person.bbox[2] - person.bbox[0]) * 100}%`,
                                                height: `${(person.bbox[3] - person.bbox[1]) * 100}%`,
                                            }}
                                        />
                                    ))}
                                </div>
                                {/* 清除按钮 */}
                                <button
                                    onClick={handleClearImage}
                                    className="absolute top-2 right-2 p-1 bg-black/50 hover:bg-black/70 rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
                                >
                                    <X className="h-3 w-3 text-white" />
                                </button>
                                {/* 分析中遮罩 */}
                                {isAnalyzing && (
                                    <div className="absolute inset-0 flex items-center justify-center bg-black/50 rounded-xl">
                                        <div className="flex items-center gap-2 text-white">
                                            <Loader2 className="h-4 w-4 animate-spin" />
                                            <span className="text-sm">分析中...</span>
                                        </div>
                                    </div>
                                )}
                            </div>
                        ) : (
                            // 上传/抓拍区域
                            <div
                                className={cn(
                                    "aspect-video rounded-xl border-2 border-dashed transition-all",
                                    "flex flex-col items-center justify-center gap-3",
                                    isDragOver
                                        ? "border-accent bg-accent/5"
                                        : "border-manus-border bg-manus-tertiary"
                                )}
                                onDragOver={(e) => { e.preventDefault(); setIsDragOver(true) }}
                                onDragLeave={() => setIsDragOver(false)}
                                onDrop={handleDrop}
                            >
                                <div className="flex items-center gap-4">
                                    {/* 抓拍按钮 */}
                                    <CameraCapture
                                        onCapture={handleFileSelect}
                                        size="md"
                                        variant="outline"
                                    />
                                    {/* 上传按钮 */}
                                    <button
                                        onClick={() => fileInputRef.current?.click()}
                                        className="w-14 h-14 rounded-full flex items-center justify-center bg-transparent hover:bg-manus-secondary/50 border border-manus-border/50 transition-colors"
                                        title="上传图片"
                                    >
                                        <ImagePlus className="h-6 w-6 text-manus-text" />
                                    </button>
                                </div>
                                <div className="flex items-center gap-3 text-xs text-manus-muted">
                                    <span className="flex items-center gap-1">
                                        <Camera className="h-3 w-3" /> 抓拍
                                    </span>
                                    <span className="text-manus-border">|</span>
                                    <span className="flex items-center gap-1">
                                        <ImagePlus className="h-3 w-3" /> 上传
                                    </span>
                                </div>
                            </div>
                        )}

                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="image/*"
                            className="hidden"
                            onChange={handleFileInputChange}
                        />
                    </div>

                    {/* 分析步骤 */}
                    {analysisSteps.length > 0 && (
                        <div className="space-y-2">
                            <div className="text-xs font-medium text-manus-muted uppercase tracking-wide">
                                分析过程
                            </div>
                            <div className="space-y-1">
                                {analysisSteps.map((step) => (
                                    <div
                                        key={step.step_id}
                                        className="flex items-center gap-2 text-xs"
                                    >
                                        {step.status === 'done' ? (
                                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
                                        ) : step.status === 'running' ? (
                                            <Loader2 className="h-3.5 w-3.5 text-accent animate-spin shrink-0" />
                                        ) : (
                                            <Circle className="h-3.5 w-3.5 text-manus-subtle shrink-0" />
                                        )}
                                        <span className={cn(
                                            step.status === 'done' ? "text-manus-muted" : "text-manus-text"
                                        )}>
                                            {step.title}
                                        </span>
                                        {step.description && step.status === 'done' && (
                                            <span className="text-manus-subtle truncate">
                                                · {step.description}
                                            </span>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* 洞察结果 */}
                    {hasAnalysis && (
                        <>
                            {/* 群体关系 */}
                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <div className="text-xs font-medium text-manus-muted uppercase tracking-wide">
                                        洞察分析
                                    </div>
                                    <div className="flex items-center gap-1 text-xs text-manus-muted">
                                        <Users className="h-3 w-3" />
                                        <span>识别 {sceneAnalysis.total_count} 人</span>
                                    </div>
                                </div>

                                {/* 群体关系标签 */}
                                <div className="flex items-center gap-2">
                                    <span className="text-xs text-manus-subtle">群体关系:</span>
                                    <span className="text-sm font-medium text-manus-text">
                                        {sceneAnalysis.group_dynamic}
                                    </span>
                                </div>
                            </div>

                            {/* 人物卡片列表 */}
                            <div className="space-y-2">
                                {sceneAnalysis.persons
                                    .sort((a, b) => b.final_weight - a.final_weight)
                                    .map((person) => (
                                        <PersonCard
                                            key={person.id}
                                            person={person}
                                            isTarget={person.id === sceneAnalysis.target_person_id}
                                            isHighlighted={person.id === highlightedPersonId}
                                            onMouseEnter={() => setHighlightedPersonId(person.id)}
                                            onMouseLeave={() => setHighlightedPersonId(null)}
                                        />
                                    ))}
                            </div>

                            {/* 策略建议 */}
                            <div className="space-y-2">
                                <div className="flex items-center gap-2">
                                    <Target className="h-3.5 w-3.5 text-accent" />
                                    <span className="text-xs font-medium text-manus-muted uppercase tracking-wide">
                                        服务策略
                                    </span>
                                </div>
                                <StrategyPanel
                                    suggestedTone={sceneAnalysis.suggested_tone}
                                    engagementStrategy={sceneAnalysis.engagement_strategy}
                                />
                            </div>
                        </>
                    )}

                    {/* 空状态提示 */}
                    {!sceneImage && !isAnalyzing && (
                        <div className="py-8 text-center">
                            <Upload className="h-8 w-8 text-manus-muted mx-auto mb-2" />
                            <p className="text-xs text-manus-muted">
                                上传游客照片<br />获取智能服务建议
                            </p>
                        </div>
                    )}
                </div>
            </ScrollArea>
        </div>
    )
}

export default AnalysisSidebar
