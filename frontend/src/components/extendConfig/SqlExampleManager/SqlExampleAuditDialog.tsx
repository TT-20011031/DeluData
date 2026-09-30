import { useCallback, useEffect, useState } from 'react'
import { Loader2, ShieldCheck } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { sqlExampleService, type AuditedSqlExample } from '@/services/sqlExampleService'

export function SqlExampleAuditDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
    const [items, setItems] = useState<AuditedSqlExample[]>([])
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState('')
    const [claimOwners, setClaimOwners] = useState<Record<number, string>>({})
    const [claimingId, setClaimingId] = useState<number | null>(null)
    const [ownerFilter, setOwnerFilter] = useState('')
    const [statusFilter, setStatusFilter] = useState('all')

    const loadItems = useCallback(() => {
        setIsLoading(true)
        setError('')
        sqlExampleService.audit()
            .then(setItems)
            .catch((reason) => setError(reason instanceof Error ? reason.message : '加载失败'))
            .finally(() => setIsLoading(false))
    }, [])

    useEffect(() => {
        if (!open) return
        loadItems()
    }, [open, loadItems])

    const claimOwner = async (item: AuditedSqlExample) => {
        const ownerId = (claimOwners[item.id] || '').trim()
        if (!ownerId) return
        setClaimingId(item.id)
        try {
            await sqlExampleService.claimOrphan(item.id, ownerId)
            await sqlExampleService.audit().then(setItems)
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : '分配失败')
        } finally {
            setClaimingId(null)
        }
    }
    const visibleItems = items.filter(item => (
        (!ownerFilter.trim() || (item.owner_id || '').toLowerCase().includes(ownerFilter.trim().toLowerCase()))
        && (statusFilter === 'all' || item.validation_status === statusFilter)
    ))

    return (
        <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
            <DialogContent className="bg-manus-secondary border-manus-border max-w-3xl">
                <DialogHeader>
                    <DialogTitle className="text-manus-text flex items-center gap-2">
                        <ShieldCheck className="h-5 w-5" />账号示例审计
                    </DialogTitle>
                    <DialogDescription className="text-manus-muted">
                        仅供查看。管理员不能修改其他账号的示例内容。
                    </DialogDescription>
                </DialogHeader>
                <div className="grid grid-cols-2 gap-2">
                    <Input
                        value={ownerFilter}
                        onChange={(event) => setOwnerFilter(event.target.value)}
                        placeholder="按账号 ID 筛选"
                        className="bg-manus border-manus-border text-manus-text"
                    />
                    <select
                        value={statusFilter}
                        onChange={(event) => setStatusFilter(event.target.value)}
                        className="h-9 rounded-md border border-manus-border bg-manus px-3 text-sm text-manus-text"
                    >
                        <option value="all">全部状态</option>
                        <option value="draft">草稿</option>
                        <option value="valid">已校验</option>
                        <option value="invalid">校验失败</option>
                        <option value="stale">需重新校验</option>
                    </select>
                </div>
                <div className="max-h-[60vh] overflow-y-auto space-y-2 py-2">
                    {isLoading && <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin" /></div>}
                    {error && <div className="rounded border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">{error}</div>}
                    {!isLoading && !error && visibleItems.length === 0 && <div className="py-8 text-center text-manus-muted">暂无示例</div>}
                    {visibleItems.map((item) => (
                        <div key={item.id} className="rounded-lg border border-manus-border bg-manus p-3">
                            <div className="flex items-start justify-between gap-4">
                                <div className="min-w-0">
                                    <div className="font-medium text-manus-text truncate">{item.question}</div>
                                    <div className="mt-1 text-xs text-manus-muted">归属账号：{item.owner_id || '待分配'}</div>
                                </div>
                                <span className="shrink-0 rounded-full bg-manus-tertiary px-2 py-1 text-xs text-manus-muted">
                                    {item.validation_status}
                                </span>
                            </div>
                            <div className="mt-2 text-xs text-manus-muted">
                                启用：{item.is_active ? '是' : '否'} · 匹配 {item.match_count || 0} 次
                            </div>
                            {(item.validation_errors || []).some(issue => issue.code === 'owner_missing') && (
                                <div className="mt-3 flex gap-2">
                                    <Input
                                        value={claimOwners[item.id] || ''}
                                        onChange={(event) => setClaimOwners(current => ({ ...current, [item.id]: event.target.value }))}
                                        placeholder="输入目标账号 ID"
                                        className="bg-manus-secondary border-manus-border text-manus-text"
                                    />
                                    <Button
                                        onClick={() => claimOwner(item)}
                                        disabled={claimingId === item.id || !(claimOwners[item.id] || '').trim()}
                                    >
                                        {claimingId === item.id && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                                        分配归属
                                    </Button>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            </DialogContent>
        </Dialog>
    )
}
