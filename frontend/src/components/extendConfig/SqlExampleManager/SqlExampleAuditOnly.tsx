import { useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { SqlExampleAuditDialog } from './SqlExampleAuditDialog'

export function SqlExampleAuditOnly() {
    const [open, setOpen] = useState(false)
    return (
        <>
            <Card className="bg-manus-secondary border-manus-border">
                <CardHeader>
                    <CardTitle className="text-manus-text">SQL 示例账号审计</CardTitle>
                    <CardDescription className="text-manus-muted">
                        当前账号没有数据库问数权限，不能维护自己的示例，但可以只读审计其他账号的配置。
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Button onClick={() => setOpen(true)}>
                        <ShieldCheck className="h-4 w-4 mr-2" />打开审计列表
                    </Button>
                </CardContent>
            </Card>
            <SqlExampleAuditDialog open={open} onClose={() => setOpen(false)} />
        </>
    )
}
