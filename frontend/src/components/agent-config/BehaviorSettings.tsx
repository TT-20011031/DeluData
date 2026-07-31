/**
 * 智能体配置 - 行为设置组件
 */
import { RefreshCw, Shield } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'

interface BehaviorSettingsProps {
    maxRetries: number
    setMaxRetries: (val: number) => void
    alwaysConfirm: boolean
    setAlwaysConfirm: (val: boolean) => void
    isLoading: boolean
}

export function BehaviorSettings({
    maxRetries,
    setMaxRetries,
    alwaysConfirm,
    setAlwaysConfirm,
    isLoading
}: BehaviorSettingsProps) {
    return (
        <div className="grid gap-6 md:grid-cols-2">
            {/* 重试配置 */}
            <Card className="bg-manus-secondary/50 border-manus-border">
                <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                        <RefreshCw className="h-4 w-4 text-accent" />
                        自动重试
                    </CardTitle>
                    <CardDescription className="text-sm">
                        任务失败时自动重试的最大次数
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center gap-4">
                        <Input
                            type="number"
                            min={0}
                            max={10}
                            value={maxRetries}
                            onChange={(e) => setMaxRetries(Number(e.target.value))}
                            className="w-20 bg-manus-tertiary border-manus-border text-center"
                            disabled={isLoading}
                        />
                        <span className="text-sm text-manus-muted">次 (0 = 不重试)</span>
                    </div>
                </CardContent>
            </Card>

            {/* 执行确认 */}
            <Card className="bg-manus-secondary/50 border-manus-border">
                <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                        <Shield className="h-4 w-4 text-accent" />
                        执行确认
                    </CardTitle>
                    <CardDescription className="text-sm">
                        执行前是否需要用户确认
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center justify-between">
                        <div>
                            <Label className="font-medium">始终确认计划</Label>
                            <p className="text-xs text-manus-muted mt-0.5">
                                {alwaysConfirm ? '所有任务需确认' : '仅复杂任务需确认'}
                            </p>
                        </div>
                        <Switch
                            checked={alwaysConfirm}
                            onCheckedChange={setAlwaysConfirm}
                            disabled={isLoading}
                        />
                    </div>
                </CardContent>
            </Card>
        </div>
    )
}
