/**
 * Dry Run 测试弹窗
 */
import { useState } from 'react'
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Loader2, Play, CheckCircle2, Brain } from 'lucide-react'
import { skillService, type DryRunResponse } from '@/services/skillService'

interface DryRunDialogProps {
    open: boolean
    onClose: () => void
    skillId: string | null
    skillTitle: string
}

/**
 * Worker 标签映射
 * 
 * TODO: 未来版本应从后端 API 动态获取工具元数据
 * 与后端 SkillToolType 枚举保持同步（参考 prompts/supervisor.yaml）
 */
const WORKER_LABELS: Record<string, string> = {
    sql_worker: 'SQL 查询',
    doc_worker: '知识库检索',
    chart_worker: '图表生成',
    office_worker: '文档处理',
}

export function SkillDryRunDialog({
    open,
    onClose,
    skillId,
    skillTitle,
}: DryRunDialogProps) {
    const [testQuery, setTestQuery] = useState('')
    const [isRunning, setIsRunning] = useState(false)
    const [result, setResult] = useState<DryRunResponse | null>(null)
    const [error, setError] = useState<string | null>(null)

    const handleRun = async () => {
        if (!skillId || !testQuery.trim()) return

        setIsRunning(true)
        setError(null)
        setResult(null)

        try {
            const response = await skillService.dryRun({
                skill_id: skillId,
                test_query: testQuery,
            })
            setResult(response)
        } catch (e) {
            setError(String(e))
        } finally {
            setIsRunning(false)
        }
    }

    const handleClose = () => {
        setTestQuery('')
        setResult(null)
        setError(null)
        onClose()
    }

    return (
        <Dialog open={open} onOpenChange={(open) => !open && handleClose()}>
            <DialogContent className="bg-manus-secondary border-manus-border max-w-2xl max-h-[80vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle className="text-manus-text flex items-center gap-2">
                        <Play className="h-5 w-5 text-accent" />
                        测试 DeluSkills:「{skillTitle}」
                    </DialogTitle>
                </DialogHeader>

                <div className="space-y-4 py-4">
                    {/* 输入 */}
                    <div className="space-y-2">
                        <Label className="text-manus-text">测试问题</Label>
                        <div className="flex gap-2">
                            <Input
                                value={testQuery}
                                onChange={(e) => setTestQuery(e.target.value)}
                                placeholder="输入一个测试问题，查看 AI 如何规划执行步骤..."
                                className="bg-manus border-manus-border text-manus-text flex-1"
                                disabled={isRunning}
                                onKeyDown={(e) => e.key === 'Enter' && handleRun()}
                            />
                            <Button
                                onClick={handleRun}
                                disabled={isRunning || !testQuery.trim()}
                                className="bg-accent hover:bg-accent/90 text-white"
                            >
                                {isRunning ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    <Play className="h-4 w-4" />
                                )}
                            </Button>
                        </div>
                    </div>

                    {/* 错误 */}
                    {error && (
                        <div className="p-3 rounded bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
                            {error}
                        </div>
                    )}

                    {/* 结果 */}
                    {result && (
                        <div className="space-y-4">
                            {/* 思考过程 */}
                            {result.thinking && (
                                <div className="p-3 rounded bg-manus border border-manus-border">
                                    <div className="flex items-center gap-2 text-sm text-manus-muted mb-2">
                                        <Brain className="h-4 w-4" />
                                        AI 思考过程
                                    </div>
                                    <p className="text-sm text-manus-text whitespace-pre-wrap">
                                        {result.thinking}
                                    </p>
                                </div>
                            )}

                            {/* 预测步骤 */}
                            <div className="space-y-2">
                                <div className="flex items-center gap-2 text-sm text-manus-muted">
                                    <CheckCircle2 className="h-4 w-4 text-green-500" />
                                    预测执行计划（{result.predicted_steps.length} 步）
                                </div>
                                <div className="space-y-2">
                                    {result.predicted_steps.map((step, index) => (
                                        <div
                                            key={index}
                                            className="flex items-start gap-3 p-3 bg-manus rounded border border-manus-border"
                                        >
                                            <span className="w-6 h-6 rounded-full bg-green-500 text-white text-xs flex items-center justify-center shrink-0">
                                                {step.step_id}
                                            </span>
                                            <div className="flex-1 min-w-0">
                                                <p className="text-sm text-manus-text">
                                                    {step.instruction}
                                                </p>
                                                <div className="flex items-center gap-3 mt-1">
                                                    <span className="px-2 py-0.5 text-xs rounded bg-manus-tertiary text-manus-muted">
                                                        {WORKER_LABELS[step.worker] || step.worker}
                                                    </span>
                                                    {Object.keys(step.params).length > 0 && (
                                                        <code className="text-xs text-manus-muted font-mono">
                                                            {JSON.stringify(step.params).slice(0, 50)}...
                                                        </code>
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </DialogContent>
        </Dialog>
    )
}
