import { CheckCircle, Database, Loader2, TestTube, XCircle } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

import type { ConnectionConfig, ConnectionTestMessage } from '../types'

interface DatabaseConnectionFormProps {
    config: ConnectionConfig
    isLoading: boolean
    isTesting: boolean
    error: string | null
    whitelistBlocked: boolean
    testMessage: ConnectionTestMessage | null
    onConfigChange: (field: keyof ConnectionConfig, value: string | number) => void
    onTestConnection: () => Promise<void>
    onConnect: () => Promise<void>
}

export function DatabaseConnectionForm({
    config,
    isLoading,
    isTesting,
    error,
    whitelistBlocked,
    testMessage,
    onConfigChange,
    onTestConnection,
    onConnect,
}: DatabaseConnectionFormProps) {
    const isActionDisabled = isLoading || isTesting || !config.host || !config.database

    return (
        <div className="flex-1 flex flex-col h-full bg-manus p-6">
            <div className="max-w-2xl mx-auto w-full space-y-6">
                <div className="animate-fade-in">
                    <h1 className="text-2xl font-semibold text-manus-text flex items-center gap-2">
                        <Database className="h-6 w-6 text-accent" />
                        数据库配置
                    </h1>
                    <p className="text-manus-muted mt-1">
                        配置数据库连接，用于 Text-to-SQL 查询和数据浏览
                    </p>
                </div>

                <Card className="bg-manus-secondary border-manus-border animate-slide-up">
                    <CardHeader>
                        <CardTitle className="text-manus-text text-lg">连接信息</CardTitle>
                        <CardDescription className="text-manus-muted">
                            请输入 MySQL 数据库连接信息
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label className="text-sm text-manus-muted mb-1.5 block">主机地址</label>
                                <Input
                                    value={config.host}
                                    onChange={(e) => onConfigChange('host', e.target.value)}
                                    placeholder="localhost or IP"
                                    className="bg-manus-tertiary border-manus-border text-manus-text transition-all duration-200 focus:ring-2 focus:ring-accent/50"
                                />
                            </div>
                            <div>
                                <label className="text-sm text-manus-muted mb-1.5 block">端口</label>
                                <Input
                                    type="number"
                                    value={config.port}
                                    onChange={(e) => onConfigChange('port', e.target.value)}
                                    placeholder="3306"
                                    className="bg-manus-tertiary border-manus-border text-manus-text transition-all duration-200 focus:ring-2 focus:ring-accent/50"
                                />
                            </div>
                        </div>

                        <div>
                            <label className="text-sm text-manus-muted mb-1.5 block">用户名</label>
                            <Input
                                value={config.username}
                                onChange={(e) => onConfigChange('username', e.target.value)}
                                placeholder="root"
                                className="bg-manus-tertiary border-manus-border text-manus-text transition-all duration-200 focus:ring-2 focus:ring-accent/50"
                            />
                        </div>

                        <div>
                            <label className="text-sm text-manus-muted mb-1.5 block">密码</label>
                            <Input
                                type="password"
                                value={config.password}
                                onChange={(e) => onConfigChange('password', e.target.value)}
                                placeholder="输入密码"
                                className="bg-manus-tertiary border-manus-border text-manus-text transition-all duration-200 focus:ring-2 focus:ring-accent/50"
                            />
                        </div>

                        <div>
                            <label className="text-sm text-manus-muted mb-1.5 block">数据库名</label>
                            <Input
                                value={config.database}
                                onChange={(e) => onConfigChange('database', e.target.value)}
                                placeholder="输入数据库名"
                                className="bg-manus-tertiary border-manus-border text-manus-text transition-all duration-200 focus:ring-2 focus:ring-accent/50"
                            />
                        </div>

                        {error && (
                            <div className="flex items-center gap-2 p-3 rounded-lg bg-error/10 text-error animate-fade-in">
                                <XCircle className="h-4 w-4 shrink-0" />
                                <span className="text-sm">
                                    {whitelistBlocked
                                        ? '此数据库未通过白名单，请联系管理员进行准入审批。'
                                        : error}
                                </span>
                            </div>
                        )}

                        {testMessage && !error && (
                            <div
                                className={cn(
                                    'flex items-center gap-2 p-3 rounded-lg animate-fade-in text-sm',
                                    testMessage.success ? 'bg-success/10 text-success' : 'bg-error/10 text-error',
                                )}
                            >
                                {testMessage.success ? (
                                    <CheckCircle className="h-4 w-4 shrink-0" />
                                ) : (
                                    <XCircle className="h-4 w-4 shrink-0" />
                                )}
                                <span>
                                    {testMessage.code === 'WHITELIST_BLOCKED'
                                        ? '此数据库未通过白名单，请联系管理员进行准入审批。'
                                        : testMessage.message}
                                </span>
                            </div>
                        )}

                        <div className="flex items-center gap-3">
                            <Button
                                variant="outline"
                                onClick={onTestConnection}
                                disabled={isActionDisabled}
                                className="flex-1 bg-manus-tertiary border-manus-border hover:bg-manus-hover text-manus-text transition-all duration-200"
                            >
                                {isTesting ? (
                                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                ) : (
                                    <TestTube className="h-4 w-4 mr-2" />
                                )}
                                测试连接
                            </Button>
                            <Button
                                onClick={onConnect}
                                disabled={isActionDisabled}
                                className="flex-1 bg-accent hover:bg-accent/90 text-white transition-all duration-200 hover:shadow-lg hover:shadow-accent/20"
                            >
                                {isLoading ? (
                                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                ) : (
                                    <Database className="h-4 w-4 mr-2" />
                                )}
                                保存并连接
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    )
}
