/**
 * 任务步骤项组件（递归渲染支持嵌套）
 * 
 * 支持：
 * - 主任务卡片渲染
 * - 行内反思气泡
 * - 重试子任务递归展示
 */
import { Loader2, CheckCircle2, Circle, AlertCircle, ChevronDown, ChevronUp, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getWorkerLabel } from '@/config/workerConfig'
import type { TaskStep, SubStep } from '@/types/chat'
import { useState } from 'react'

interface StepItemProps {
    step: TaskStep
    depth?: number  // 嵌套深度
    isRetry?: boolean  // 是否为重试任务
}

export function StepItem({ step, depth = 0, isRetry = false }: StepItemProps) {
    const [isCollapsed, setIsCollapsed] = useState(false)

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'completed':
                return <CheckCircle2 className="h-4 w-4 text-success" />
            case 'running':
                return <Loader2 className="h-4 w-4 text-warning animate-spin" />
            case 'error':
                return <AlertCircle className="h-4 w-4 text-error" />
            case 'waiting':
                return <Circle className="h-4 w-4 text-accent/50 animate-pulse" />
            case 'loading':
                return <Loader2 className="h-4 w-4 text-accent/50 animate-spin" />
            case 'cancelled':
                return <AlertCircle className="h-4 w-4 text-manus-muted/50" />
            case 'skipped':
                return <XCircle className="h-4 w-4 text-manus-muted/60" />
            case 'invalid_data':
                return <AlertCircle className="h-4 w-4 text-slate-300" />
            default:
                return <Circle className="h-4 w-4 text-manus-muted" />
        }
    }

    // [优化] 限制最大缩进深度，避免窄屏下显示问题
    const effectiveDepth = Math.min(depth, 3)

    return (
        <div
            className={cn(
                "animate-in fade-in slide-in-from-left-2 duration-300",
                effectiveDepth > 0 && "ml-4 border-l-2 border-accent/20 pl-3"
            )}
        >
            {/* 主任务卡片 - loading 状态显示骨架屏 */}
            <div
                data-running={step.status === 'running' ? 'true' : undefined}
                className={cn(
                    "relative overflow-hidden flex flex-col gap-2 p-3 rounded-lg transition-colors border border-transparent",
                    step.status === 'running' && "bg-warning/5 border-warning/20",
                    step.status === 'completed' && "bg-success/5 border-success/20",
                    step.status === 'error' && "bg-error/5 border-error/20",
                    step.status === 'waiting' && "bg-manus-tertiary/30 border-dashed border-accent/30 opacity-60",
                    step.status === 'loading' && "bg-manus-tertiary/40 backdrop-blur-sm border border-accent/10",
                    step.status === 'cancelled' && "bg-manus-tertiary/20 opacity-40",
                    step.status === 'skipped' && "bg-manus-tertiary/20 border-dashed border-manus-muted/30 opacity-50",
                    step.status === 'invalid_data' && "bg-slate-500/10 border-slate-400/30",
                    (!step.status || step.status === 'pending') && "bg-manus-tertiary/50",
                    isRetry && "bg-amber-500/5 border-amber-500/20"
                )}
            >
                {/* 骨架屏状态 */}
                {step.status === 'loading' ? (
                    <div className="flex items-center gap-3">
                        <Loader2 className="h-4 w-4 text-accent animate-spin" />
                        <div className="flex-1 space-y-2">
                            <div className="h-4 bg-gradient-to-r from-accent/25 via-accent/10 to-accent/25 rounded animate-pulse" style={{ width: '75%' }} />
                            <div className="h-3 bg-gradient-to-r from-accent/20 via-accent/5 to-accent/20 rounded animate-pulse" style={{ width: '50%' }} />
                        </div>
                        <div className="h-5 w-16 bg-accent/10 rounded animate-pulse" />
                    </div>
                ) : (
                    <div className="flex items-center gap-3">
                        {getStatusIcon(step.status)}
                        <span className={cn(
                            "text-sm flex-1",
                            step.status === 'completed' && "text-manus-muted line-through",
                            step.status === 'skipped' && "text-manus-muted/70 line-through italic",
                            step.status === 'invalid_data' && "text-manus-muted"
                        )}>
                            {isRetry && <span className="text-xs text-amber-500 mr-1">🔄</span>}
                            {step.description}
                        </span>
                        <span className="text-xs text-manus-subtle px-2 py-0.5 bg-manus-tertiary rounded">
                            {getWorkerLabel(step.worker)}
                        </span>
                    </div>
                )}

                {/* 子步骤（DeluSQL 进度） */}
                {step.subSteps && step.subSteps.length > 0 && (
                    <div className="ml-7 mt-2">
                        <button
                            onClick={() => setIsCollapsed(!isCollapsed)}
                            className="flex items-center gap-1 text-xs text-manus-muted hover:text-manus-text mb-1"
                        >
                            {isCollapsed ? (
                                <ChevronUp className="h-3 w-3" />
                            ) : (
                                <ChevronDown className="h-3 w-3" />
                            )}
                            <span>{isCollapsed ? '展开详情' : '收起详情'}</span>
                        </button>
                        {!isCollapsed && (
                            <div className="space-y-1 border-l-2 border-accent/20 pl-3">
                                {step.subSteps.map((sub: SubStep, subIndex: number) => (
                                    <div
                                        key={`${step.step_id}-${sub.id}-${subIndex}`}
                                        className="flex items-center gap-2 text-xs"
                                    >
                                        {sub.status === 'done' && <CheckCircle2 className="h-3 w-3 text-success" />}
                                        {sub.status === 'running' && <Loader2 className="h-3 w-3 text-warning animate-spin" />}
                                        {sub.status === 'error' && <AlertCircle className="h-3 w-3 text-error" />}
                                        {sub.status === 'pending' && <Circle className="h-3 w-3 text-manus-muted" />}
                                        <span className={cn(
                                            "text-manus-subtle",
                                            sub.status === 'done' && "text-manus-muted"
                                        )}>
                                            {sub.name}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {step.status === 'invalid_data' && (
                    <div className="step-invalid-overlay absolute inset-0 z-10 rounded-lg backdrop-blur-sm flex items-center justify-center">
                        <div className="step-invalid-badge flex items-center gap-2 px-3 py-1.5 rounded-full shadow-md animate-in zoom-in-95 duration-300">
                            <AlertCircle className="h-4 w-4" />
                            <span className="text-xs font-semibold tracking-wide">拦截：数据无效</span>
                        </div>
                    </div>
                )}
            </div>

            {/* 行内反思气泡 - 移除 ml-4 使其与任务卡片等宽 */}
            {step.inlineThoughts?.map(thought => (
                <div
                    key={thought.id}
                    className={cn(
                        "flex items-start gap-2 p-3 rounded-lg mt-2 animate-in fade-in slide-in-from-left-2 duration-300",
                        thought.verdict === 'pass' && "bg-accent/5",
                        (thought.verdict === 'fail' || thought.verdict === 'partial') && "bg-amber-500/10",
                        thought.verdict === 'break' && "bg-green-500/10",
                        thought.verdict === 'loading' && "bg-manus-tertiary/40 border border-accent/10"
                    )}
                >
                    {thought.verdict === 'loading' ? (
                        <div className="flex items-center gap-2 w-full">
                            <Loader2 className="h-4 w-4 text-accent animate-spin flex-shrink-0" />
                            <div className="flex-1 space-y-2">
                                <div className="h-3 bg-gradient-to-r from-accent/25 via-accent/10 to-accent/25 rounded animate-pulse" style={{ width: '85%' }} />
                                <div className="h-3 bg-gradient-to-r from-accent/20 via-accent/5 to-accent/20 rounded animate-pulse" style={{ width: '65%' }} />
                            </div>
                        </div>
                    ) : (
                        <>
                            {(thought.verdict === 'fail' || thought.verdict === 'partial') && (
                                <AlertCircle className="h-4 w-4 text-amber-500 flex-shrink-0 mt-0.5" />
                            )}
                            {thought.verdict === 'break' && (
                                <CheckCircle2 className="h-4 w-4 text-green-500 flex-shrink-0 mt-0.5" />
                            )}
                            {thought.verdict === 'pass' && (
                                <CheckCircle2 className="h-4 w-4 text-accent flex-shrink-0 mt-0.5" />
                            )}
                            <p className="text-sm text-manus-text break-words">{thought.thought}</p>
                        </>
                    )}
                </div>
            ))}
        </div>
    )
}
