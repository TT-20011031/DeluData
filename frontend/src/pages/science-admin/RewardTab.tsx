/**
 * 奖励规则 Tab
 *
 * 功能：配置答对题数要求、计算周期、关联优惠券
 * 改造项：中文 Label、coupon_id 下拉选择、Switch
 */
import { useEffect, useState, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/components/ui/toast';
import { scienceAdminService } from '@/services/scienceAdminService';
import type { ScienceRewardPolicy, ScienceCoupon } from '@/types/scienceAdmin';

export default function RewardTab() {
    const { toast } = useToast();
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);

    const [policy, setPolicy] = useState<ScienceRewardPolicy>({
        required_correct_count: 3,
        period_hours: 24,
        max_claims_per_period: 1,
        coupon_id: '',
        is_active: true,
    });
    const [coupons, setCoupons] = useState<ScienceCoupon[]>([]);

    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [rewardConfig, couponList] = await Promise.all([
                scienceAdminService.getRewardPolicy(),
                scienceAdminService.listCoupons(),
            ]);
            setPolicy({ ...rewardConfig, coupon_id: rewardConfig.coupon_id ?? '' });
            setCoupons(couponList);
        } catch (err) {
            toast({ type: 'error', title: '加载奖励规则失败', description: err instanceof Error ? err.message : '未知错误' });
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => { void loadData(); }, [loadData]);

    const handleSave = async () => {
        if (policy.required_correct_count < 1) {
            toast({ type: 'warning', title: '答对题数至少为 1' });
            return;
        }
        if (policy.period_hours < 1) {
            toast({ type: 'warning', title: '计算周期至少为 1 小时' });
            return;
        }
        setBusy(true);
        try {
            const result = await scienceAdminService.updateRewardPolicy({
                ...policy,
                coupon_id: policy.coupon_id || null,
            });
            setPolicy({ ...result, coupon_id: result.coupon_id ?? '' });
            toast({ type: 'success', title: '奖励规则已保存' });
        } catch (err) {
            toast({ type: 'error', title: '保存失败', description: err instanceof Error ? err.message : '未知错误' });
        } finally {
            setBusy(false);
        }
    };

    if (loading) {
        return <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10"><CardContent className="py-10 text-center text-manus-muted">加载奖励规则中...</CardContent></Card>;
    }

    return (
        <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
            <CardHeader className="border-b border-manus-border/50"><CardTitle>奖励规则配置</CardTitle></CardHeader>
            <CardContent className="space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <Label>答对题数要求</Label>
                        <Input
                            type="number"
                            min={1}
                            value={policy.required_correct_count}
                            onChange={(e) => setPolicy((s) => ({ ...s, required_correct_count: Number(e.target.value) }))}
                        />
                        <p className="text-xs text-manus-muted">访客需要答对多少题才能获得奖励</p>
                    </div>
                    <div className="space-y-2">
                        <Label>券有效期 <span className="text-manus-muted text-xs">(小时)</span></Label>
                        <Input
                            type="number"
                            min={1}
                            value={policy.period_hours}
                            onChange={(e) => setPolicy((s) => ({ ...s, period_hours: Number(e.target.value) }))}
                        />
                        <p className="text-xs text-manus-muted">发放的优惠券在此时间后过期</p>
                    </div>
                    <div className="space-y-2">
                        <Label>周期内最大领取次数</Label>
                        <Input
                            type="number"
                            min={1}
                            value={policy.max_claims_per_period}
                            onChange={(e) => setPolicy((s) => ({ ...s, max_claims_per_period: Number(e.target.value) }))}
                        />
                        <p className="text-xs text-manus-muted">同一访客在计算周期内最多可领取奖励的次数</p>
                    </div>
                    <div className="space-y-2">
                        <Label>关联优惠券 <span className="text-manus-muted text-xs">(可选)</span></Label>
                        <Select
                            value={policy.coupon_id || '_none_'}
                            onValueChange={(v) => setPolicy((s) => ({ ...s, coupon_id: v === '_none_' ? '' : v }))}
                        >
                            <SelectTrigger><SelectValue placeholder="不关联优惠券" /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="_none_">不关联优惠券</SelectItem>
                                {coupons.map((c) => (
                                    <SelectItem key={c.id} value={c.id}>{c.title} ({c.merchant_name || '无商家'}) — 库存 {c.total_stock - c.issued_count}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                </div>

                <div className="flex items-center gap-4">
                    <div className="flex items-center gap-2">
                        <Label>规则状态</Label>
                        <Switch checked={policy.is_active} onCheckedChange={(v) => setPolicy((s) => ({ ...s, is_active: v }))} />
                        <span className="text-sm text-manus-muted">{policy.is_active ? '已启用' : '已停用'}</span>
                    </div>
                    <div className="flex-1" />
                    <Button onClick={() => void handleSave()} disabled={busy} className="bg-accent text-white hover:bg-accent/90 shadow-sm shadow-accent/25">保存奖励规则</Button>
                </div>
            </CardContent>
        </Card>
    );
}
