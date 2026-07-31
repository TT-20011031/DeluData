/**
 * 任务面板骨架屏
 * 
 * 在 AI 规划任务时显示的加载状态
 */
import { Loader2 } from 'lucide-react'

export function SkeletonTaskPanel() {
    return (
        <div className="absolute bottom-full left-0 right-0 mb-2 px-1 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className="bg-manus-secondary/90 backdrop-blur-sm border border-accent/20 rounded-xl shadow-lg overflow-hidden w-full">
                {/* 骨架头部 */}
                <div className="flex items-center justify-between p-3 border-b border-manus-border/50">
                    <div className="flex items-center gap-3">
                        <Loader2 className="h-4 w-4 text-accent animate-spin" />
                        <span className="text-sm text-manus-text/70 animate-pulse font-medium">AI 正在规划任务路径...</span>
                    </div>
                </div>

                {/* 骨架内容 */}
                <div className="p-4 space-y-4">
                    {/* 摘要骨架 */}
                    <div className="space-y-2">
                        <div className="h-3 bg-gradient-to-r from-accent/25 via-accent/10 to-accent/25 rounded w-3/4 animate-pulse" />
                        <div className="h-3 bg-gradient-to-r from-accent/20 via-accent/5 to-accent/20 rounded w-1/2 animate-pulse" />
                    </div>

                    {/* 步骤列表骨架 */}
                    <div className="space-y-2.5 pt-2">
                        {[1, 2, 3].map((i) => (
                            <div key={i} className="flex flex-col gap-2 p-3 rounded-lg border border-accent/10 bg-manus-tertiary/40">
                                <div className="flex items-center gap-3">
                                    <div className="h-4 w-4 rounded-full bg-accent/10 animate-pulse" />
                                    <div className="h-4 bg-gradient-to-r from-accent/25 via-accent/10 to-accent/25 rounded w-2/3 animate-pulse" />
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    )
}
