/**
 * 任务面板组件
 * 
 * 使用 Shadcn/UI Tabs, ScrollArea 组件
 */
import { Check, Loader2, X, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useMissionStore } from '@/stores/missionStore'
import { cn } from '@/lib/utils'

// 步骤状态图标
function StepStatusIcon({ status }: { status: string }) {
    switch (status) {
        case 'completed':
            return <Check className="h-4 w-4 text-accent" />
        case 'running':
            return <Loader2 className="h-4 w-4 text-warning animate-spin" />
        case 'failed':
            return <X className="h-4 w-4 text-error" />
        default:
            return <div className="h-2 w-2 rounded-full bg-wabi-subtle" />
    }
}

// 步骤列表
function StepsList() {
    const steps = useMissionStore((state) => state.steps)

    if (steps.length === 0) {
        return (
            <div className="flex flex-col items-center justify-center py-12 text-manus-subtle">
                <div className="w-12 h-12 rounded-full bg-manus-tertiary flex items-center justify-center mb-3">
                    <Check className="h-5 w-5" />
                </div>
                <p className="text-sm">等待任务开始...</p>
            </div>
        )
    }

    return (
        <div className="space-y-2">
            {steps.map((step, index) => (
                <div
                    key={step.id}
                    className={cn(
                        "flex items-start gap-3 p-3 rounded-lg transition-colors duration-200",
                        step.status === 'running' && "bg-wabi-hover"
                    )}
                >
                    <div className="mt-0.5">
                        <StepStatusIcon status={step.status} />
                    </div>
                    <div className="flex-1 min-w-0">
                        <div className="text-sm text-manus-text">{step.label}</div>
                        <div className="text-xs text-manus-subtle mt-0.5">
                            步骤 {index + 1}
                        </div>
                    </div>
                </div>
            ))}
        </div>
    )
}

// 思考日志
function ThinkingLogs() {
    const logs = useMissionStore((state) => state.thinkingLogs)

    if (logs.length === 0) {
        return (
            <div className="flex flex-col items-center justify-center py-12 text-manus-subtle">
                <p className="text-sm">暂无思考日志</p>
            </div>
        )
    }

    return (
        <div className="space-y-2 font-mono text-xs">
            {logs.map((log) => (
                <div
                    key={log.id}
                    className="p-3 bg-wabi rounded-lg border border-manus-border text-manus-muted"
                >
                    {log.content}
                </div>
            ))}
        </div>
    )
}

// Artifact 列表
function ArtifactsList() {
    const artifacts = useMissionStore((state) => state.artifacts)

    if (artifacts.length === 0) {
        return (
            <div className="flex flex-col items-center justify-center py-12 text-manus-subtle">
                <p className="text-sm">暂无输出</p>
            </div>
        )
    }

    return (
        <div className="space-y-2">
            {artifacts.map((artifact) => (
                <div
                    key={artifact.id}
                    className="p-3 bg-wabi rounded-lg border border-manus-border hover:border-manus-border-strong cursor-pointer transition-colors group"
                >
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <span className="text-sm text-manus-text">{artifact.title}</span>
                            <span className="text-xs px-1.5 py-0.5 bg-accent/20 text-accent rounded">
                                {artifact.type}
                            </span>
                        </div>
                        <ChevronRight className="h-4 w-4 text-manus-subtle group-hover:text-manus-muted transition-colors" />
                    </div>
                </div>
            ))}
        </div>
    )
}

export default function MissionPanel() {
    const { isActive, togglePanel } = useMissionStore()

    return (
        <div className="h-full w-80 bg-manus-secondary border-l border-manus-border flex flex-col">
            {/* 头部 */}
            <div className="h-14 flex items-center justify-between px-4 border-b border-manus-border">
                <div className="flex items-center gap-2">
                    <div className={cn(
                        "w-2 h-2 rounded-full transition-colors",
                        isActive ? "bg-moss animate-pulse" : "bg-wabi-subtle"
                    )} />
                    <span className="text-sm text-manus-text font-medium">DeluData 的电脑</span>
                </div>
                <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 hover:bg-manus-hover"
                    onClick={togglePanel}
                >
                    <ChevronRight className="h-4 w-4 text-manus-muted" />
                </Button>
            </div>

            {/* Tabs */}
            <Tabs defaultValue="steps" className="flex-1 flex flex-col">
                <TabsList className="w-full justify-start rounded-none border-b border-manus-border bg-transparent h-10 p-0">
                    <TabsTrigger
                        value="steps"
                        className="flex-1 rounded-none border-b-2 border-transparent data-[state=active]:border-accent data-[state=active]:bg-transparent data-[state=active]:shadow-none text-xs"
                    >
                        任务进度
                    </TabsTrigger>
                    <TabsTrigger
                        value="thinking"
                        className="flex-1 rounded-none border-b-2 border-transparent data-[state=active]:border-accent data-[state=active]:bg-transparent data-[state=active]:shadow-none text-xs"
                    >
                        思考
                    </TabsTrigger>
                    <TabsTrigger
                        value="artifacts"
                        className="flex-1 rounded-none border-b-2 border-transparent data-[state=active]:border-accent data-[state=active]:bg-transparent data-[state=active]:shadow-none text-xs"
                    >
                        输出
                    </TabsTrigger>
                </TabsList>

                <ScrollArea className="flex-1">
                    <div className="p-3">
                        <TabsContent value="steps" className="m-0">
                            <StepsList />
                        </TabsContent>
                        <TabsContent value="thinking" className="m-0">
                            <ThinkingLogs />
                        </TabsContent>
                        <TabsContent value="artifacts" className="m-0">
                            <ArtifactsList />
                        </TabsContent>
                    </div>
                </ScrollArea>
            </Tabs>
        </div>
    )
}
