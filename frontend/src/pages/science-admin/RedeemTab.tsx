import { useCallback, useEffect, useState } from 'react';

import { ConfirmDialog } from '@/components/ConfirmDialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/components/ui/toast';
import { scienceAdminService } from '@/services/scienceAdminService';
import type { ScienceCouponIssuance } from '@/types/scienceAdmin';
import { formatDateTimeCN } from '@/utils/dateFormatter';

const STATUS_MAP: Record<string, { label: string; className: string }> = {
  issued: { label: '已发放', className: 'border-manus-border text-manus-text' },
  redeemed: { label: '已核销', className: 'border-black bg-black text-white' },
  expired: { label: '已过期', className: 'border-manus-border text-manus-muted' },
};

export default function RedeemTab() {
  const { toast } = useToast();
  const [issuances, setIssuances] = useState<ScienceCouponIssuance[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [redeemForm, setRedeemForm] = useState({ issuance_id: '', coupon_code: '', note: '' });
  const [confirmOpen, setConfirmOpen] = useState(false);

  const loadIssuances = useCallback(async () => {
    setLoading(true);
    try {
      setIssuances(await scienceAdminService.listCouponIssuances());
    } catch (err) {
      toast({ type: 'error', title: '加载核销记录失败', description: err instanceof Error ? err.message : '未知错误' });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void loadIssuances();
  }, [loadIssuances]);

  const handleRedeem = async () => {
    setBusy(true);
    try {
      const result = await scienceAdminService.redeemCoupon({
        issuance_id: redeemForm.issuance_id || undefined,
        coupon_code: redeemForm.coupon_code || undefined,
        note: redeemForm.note || undefined,
      });
      if (!result.success) throw new Error(result.message || '核销失败');
      toast({ type: 'success', title: '核销成功', description: `券码：${result.coupon_code}` });
      setRedeemForm({ issuance_id: '', coupon_code: '', note: '' });
      setConfirmOpen(false);
      await loadIssuances();
    } catch (err) {
      toast({ type: 'error', title: '核销失败', description: err instanceof Error ? err.message : '未知错误' });
    } finally {
      setBusy(false);
    }
  };

  const tryRedeem = () => {
    if (!redeemForm.issuance_id && !redeemForm.coupon_code) {
      toast({ type: 'warning', title: '请填写发放记录 ID 或券码（至少一项）' });
      return;
    }
    setConfirmOpen(true);
  };

  if (loading) {
    return <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10"><CardContent className="py-10 text-center text-manus-muted">加载核销记录中...</CardContent></Card>;
  }

  return (
    <div className="space-y-6">
      <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
        <CardHeader className="border-b border-manus-border/50"><CardTitle>执行核销</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>发放记录 ID</Label>
              <Input placeholder="issuance UUID（二选一）" value={redeemForm.issuance_id} onChange={(e) => setRedeemForm((s) => ({ ...s, issuance_id: e.target.value }))} />
            </div>
            <div className="space-y-2">
              <Label>券码</Label>
              <Input placeholder="coupon code（二选一）" value={redeemForm.coupon_code} onChange={(e) => setRedeemForm((s) => ({ ...s, coupon_code: e.target.value }))} />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label>备注</Label>
              <Input placeholder="可选的核销备注" value={redeemForm.note} onChange={(e) => setRedeemForm((s) => ({ ...s, note: e.target.value }))} />
            </div>
          </div>
          <div className="flex justify-end">
            <Button onClick={tryRedeem} disabled={busy} className="bg-black text-white hover:bg-black/90 shadow-sm shadow-black/20">执行核销</Button>
          </div>
        </CardContent>
      </Card>

      <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
        <CardHeader className="border-b border-manus-border/50"><CardTitle>发放与核销记录</CardTitle></CardHeader>
        <CardContent>
          <div className="overflow-auto rounded-lg border border-manus-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-manus-tertiary text-manus-muted">
                  <th className="text-left py-3 px-4 font-medium">记录 ID</th>
                  <th className="text-left py-3 px-4 font-medium">券码</th>
                  <th className="text-left py-3 px-4 font-medium">设备 ID</th>
                  <th className="text-left py-3 px-4 font-medium">状态</th>
                  <th className="text-left py-3 px-4 font-medium">发放时间</th>
                  <th className="text-left py-3 px-4 font-medium">核销时间</th>
                  <th className="text-left py-3 px-4 font-medium">过期时间</th>
                </tr>
              </thead>
              <tbody>
                {issuances.length === 0 ? (
                  <tr><td colSpan={7} className="py-8 text-center text-manus-muted">暂无发放记录</td></tr>
                ) : (
                  issuances.map((issuance) => {
                    const status = STATUS_MAP[issuance.status] || { label: issuance.status, className: 'border-manus-border text-manus-text' };
                    return (
                      <tr key={issuance.id} className="border-t border-manus-border hover:bg-manus-tertiary/50 transition-colors">
                        <td className="py-3 px-4 font-mono text-xs">{issuance.id.slice(0, 8)}...</td>
                        <td className="py-3 px-4 font-mono">{issuance.coupon_code}</td>
                        <td className="py-3 px-4 font-mono text-xs">{issuance.device_id.slice(0, 12)}...</td>
                        <td className="py-3 px-4"><Badge className={status.className}>{status.label}</Badge></td>
                        <td className="py-3 px-4 text-xs">{formatDateTimeCN(issuance.issued_at)}</td>
                        <td className="py-3 px-4 text-xs">{formatDateTimeCN(issuance.redeemed_at)}</td>
                        <td className="py-3 px-4 text-xs">{formatDateTimeCN(issuance.expires_at)}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="确认执行核销"
        description={`即将核销 ${redeemForm.coupon_code || redeemForm.issuance_id}，此操作不可撤销。`}
        onConfirm={() => void handleRedeem()}
        loading={busy}
        confirmText="确认核销"
        variant="destructive"
      />
    </div>
  );
}
