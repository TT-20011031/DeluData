import { useCallback, useEffect, useState } from 'react';
import { Link2, Pencil } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/toast';
import { scienceAdminService } from '@/services/scienceAdminService';
import type { ScienceCoupon, ScienceCouponUpsertRequest } from '@/types/scienceAdmin';
import { formatDateTimeCN, toDatetimeLocal, toIso } from '@/utils/dateFormatter';

const EMPTY_FORM: ScienceCouponUpsertRequest = {
  title: '',
  description: '',
  merchant_name: '',
  redeem_link: '',
  total_stock: 0,
  expires_at: undefined,
  status: 'active',
};

export default function CouponTab() {
  const { toast } = useToast();
  const [coupons, setCoupons] = useState<ScienceCoupon[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<ScienceCouponUpsertRequest>({ ...EMPTY_FORM });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [expiresInput, setExpiresInput] = useState('');

  const loadCoupons = useCallback(async () => {
    setLoading(true);
    try {
      setCoupons(await scienceAdminService.listCoupons());
    } catch (err) {
      toast({
        type: 'error',
        title: '加载券模板失败',
        description: err instanceof Error ? err.message : '未知错误',
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void loadCoupons();
  }, [loadCoupons]);

  const handleEdit = (coupon: ScienceCoupon) => {
    setEditingId(coupon.id);
    setForm({
      title: coupon.title,
      description: coupon.description ?? '',
      merchant_name: coupon.merchant_name ?? '',
      redeem_link: coupon.redeem_link ?? '',
      total_stock: coupon.total_stock,
      expires_at: coupon.expires_at ?? undefined,
      status: coupon.status,
    });
    setExpiresInput(toDatetimeLocal(coupon.expires_at));
  };

  const handleCancel = () => {
    setEditingId(null);
    setForm({ ...EMPTY_FORM });
    setExpiresInput('');
  };

  const handleSave = async () => {
    if (!form.title.trim()) {
      toast({ type: 'warning', title: '请填写券标题' });
      return;
    }
    if (form.total_stock < 0) {
      toast({ type: 'warning', title: '库存不能为负数' });
      return;
    }

    setBusy(true);
    try {
      const payload: ScienceCouponUpsertRequest = {
        ...form,
        title: form.title.trim(),
        description: form.description?.trim() || null,
        merchant_name: form.merchant_name?.trim() || null,
        redeem_link: form.redeem_link?.trim() || null,
        expires_at: toIso(expiresInput),
      };
      if (editingId) {
        await scienceAdminService.updateCoupon(editingId, payload);
      } else {
        await scienceAdminService.createCoupon(payload);
      }
      toast({ type: 'success', title: editingId ? '券模板已更新' : '券模板已创建' });
      handleCancel();
      await loadCoupons();
    } catch (err) {
      toast({
        type: 'error',
        title: '保存券模板失败',
        description: err instanceof Error ? err.message : '未知错误',
      });
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
        <CardContent className="py-10 text-center text-manus-muted">加载券模板中...</CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
        <CardHeader className="border-b border-manus-border/50">
          <CardTitle>{editingId ? '编辑券模板' : '新建券模板'}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>券标题 <span className="text-red-400">*</span></Label>
              <Input
                placeholder="如：科技馆周边 8 折券"
                value={form.title}
                onChange={(e) => setForm((prev) => ({ ...prev, title: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>商家名称</Label>
              <Input
                placeholder="如：科技馆纪念品商店"
                value={form.merchant_name ?? ''}
                onChange={(e) => setForm((prev) => ({ ...prev, merchant_name: e.target.value }))}
              />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label>领取链接</Label>
              <Input
                placeholder="https://merchant.example.com/redeem/abc123"
                value={form.redeem_link ?? ''}
                onChange={(e) => setForm((prev) => ({ ...prev, redeem_link: e.target.value }))}
              />
              <p className="text-xs text-manus-muted">Kiosk 奖励页会优先把这个链接渲染成二维码。</p>
            </div>
            <div className="space-y-2">
              <Label>总库存</Label>
              <Input
                type="number"
                min={0}
                value={form.total_stock}
                onChange={(e) => setForm((prev) => ({ ...prev, total_stock: Number(e.target.value) }))}
              />
            </div>
            <div className="space-y-2">
              <Label>过期时间</Label>
              <Input type="datetime-local" value={expiresInput} onChange={(e) => setExpiresInput(e.target.value)} />
            </div>
          </div>

          <div className="space-y-2">
            <Label>说明</Label>
            <Textarea
              placeholder="填写优惠内容、使用说明或注意事项"
              value={form.description ?? ''}
              onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
              className="min-h-[90px] border border-manus-border"
            />
          </div>

          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <Label>状态</Label>
              <Switch
                checked={form.status === 'active'}
                onCheckedChange={(value) => setForm((prev) => ({ ...prev, status: value ? 'active' : 'inactive' }))}
              />
              <span className="text-sm text-manus-muted">{form.status === 'active' ? '上架中' : '已下架'}</span>
            </div>
            <div className="flex-1" />
            <div className="flex gap-2">
              {editingId ? (
                <Button variant="outline" onClick={handleCancel} className="bg-manus-tertiary border-manus-border">
                  取消
                </Button>
              ) : null}
              <Button onClick={() => void handleSave()} disabled={busy} className="bg-black text-white hover:bg-black/90 shadow-sm shadow-black/20">
                {editingId ? '更新券模板' : '创建券模板'}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
        <CardHeader className="border-b border-manus-border/50">
          <CardTitle>券模板列表</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-auto rounded-lg border border-manus-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-manus-tertiary text-manus-muted">
                  <th className="px-4 py-3 text-left font-medium">标题 / 说明</th>
                  <th className="px-4 py-3 text-left font-medium">商家 / 链接</th>
                  <th className="px-4 py-3 text-left font-medium">创建时间</th>
                  <th className="px-3 py-3 text-right font-medium">总库存</th>
                  <th className="px-3 py-3 text-right font-medium">已发放</th>
                  <th className="px-3 py-3 text-right font-medium">已核销</th>
                  <th className="px-3 py-3 text-right font-medium">剩余</th>
                  <th className="px-4 py-3 text-left font-medium">过期时间</th>
                  <th className="px-4 py-3 text-left font-medium">状态</th>
                  <th className="px-4 py-3 text-left font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {coupons.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="py-8 text-center text-manus-muted">
                      暂无券模板
                    </td>
                  </tr>
                ) : (
                  coupons.map((coupon) => {
                    const remaining = coupon.total_stock - coupon.issued_count;
                    return (
                      <tr key={coupon.id} className="border-t border-manus-border hover:bg-manus-tertiary/50 transition-colors">
                        <td className="px-4 py-3">
                          <div className="font-medium">{coupon.title}</div>
                          {coupon.description ? (
                            <div className="mt-0.5 max-w-[260px] truncate text-xs text-manus-muted" title={coupon.description}>
                              {coupon.description}
                            </div>
                          ) : null}
                        </td>
                        <td className="px-4 py-3">
                          <div className="text-manus-text">{coupon.merchant_name || '-'}</div>
                          {coupon.redeem_link ? (
                            <div className="mt-1 flex items-center gap-1 text-xs text-manus-muted">
                              <Link2 className="h-3 w-3" />
                              <span className="max-w-[240px] truncate" title={coupon.redeem_link}>
                                {coupon.redeem_link}
                              </span>
                            </div>
                          ) : (
                            <div className="mt-1 text-xs text-manus-muted">未配置领取链接</div>
                          )}
                        </td>
                        <td className="px-4 py-3 text-xs text-manus-muted">{formatDateTimeCN(coupon.created_at)}</td>
                        <td className="px-3 py-3 text-right">{coupon.total_stock}</td>
                        <td className="px-3 py-3 text-right text-manus-text">{coupon.issued_count}</td>
                        <td className="px-3 py-3 text-right text-manus-text">{coupon.redeemed_count}</td>
                        <td className="px-3 py-3 text-right">
                          <span className={remaining <= 0 ? 'font-bold text-manus-text' : 'text-manus-text'}>
                            {remaining}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-xs text-manus-muted">{formatDateTimeCN(coupon.expires_at)}</td>
                        <td className="px-4 py-3">
                          <Badge className={coupon.status === 'active' ? 'border-black bg-black text-white' : 'border-manus-border text-manus-text'}>
                            {coupon.status === 'active' ? '上架中' : '已下架'}
                          </Badge>
                        </td>
                        <td className="px-4 py-3">
                          <Button size="sm" variant="outline" onClick={() => handleEdit(coupon)} className="h-7 px-2 bg-manus-tertiary border-manus-border">
                            <Pencil className="mr-1 h-3 w-3" />编辑
                          </Button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
