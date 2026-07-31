import { useCallback, useEffect, useMemo, useState } from "react";
import { Copy, Pencil, Plus, Power, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/toast";
import { scienceAdminService } from "@/services/scienceAdminService";
import type {
  ScienceDevice,
  ScienceDeviceActivationCode,
  ScienceDeviceActivationCodeCreateResponse,
  ScienceDeviceUpsertRequest,
} from "@/types/scienceAdmin";

const EMPTY_EDIT_FORM: ScienceDeviceUpsertRequest = {
  device_id: "",
  name: "",
  is_active: true,
  kb_scope_mode: "files",
  kb_scope_dept_ids: [],
  kb_scope_file_ids: [],
};

function statusBadge(active: boolean): string {
  return active ? "border-black bg-black text-white" : "border-manus-border text-manus-text";
}

function activationCodeStatusBadge(status: string): string {
  if (status === "used") return "border-black bg-black text-white";
  if (status === "revoked") return "border-manus-border text-manus-muted";
  return "border-manus-border text-manus-text";
}

export default function DeviceTab() {
  const { toast } = useToast();
  const [devices, setDevices] = useState<ScienceDevice[]>([]);
  const [activationCodes, setActivationCodes] = useState<ScienceDeviceActivationCode[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [codeNameHint, setCodeNameHint] = useState("");
  const [createdCode, setCreatedCode] = useState<ScienceDeviceActivationCodeCreateResponse | null>(null);
  const [editingDevice, setEditingDevice] = useState<ScienceDevice | null>(null);
  const [editForm, setEditForm] = useState<ScienceDeviceUpsertRequest>({ ...EMPTY_EDIT_FORM });
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmTarget, setConfirmTarget] = useState<ScienceDevice | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ScienceDevice | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [deviceList, codeList] = await Promise.all([
        scienceAdminService.listDevices(),
        scienceAdminService.listDeviceActivationCodes(),
      ]);
      setDevices(deviceList);
      setActivationCodes(codeList);
    } catch (err) {
      toast({
        type: "error",
        title: "加载设备管理数据失败",
        description: err instanceof Error ? err.message : "未知错误",
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const pendingCodes = useMemo(
    () => activationCodes.filter((item) => item.status === "pending"),
    [activationCodes],
  );

  const generateActivationCode = async () => {
    setBusy(true);
    try {
      const result = await scienceAdminService.createDeviceActivationCode({
        name_hint: codeNameHint.trim() || undefined,
      });
      setCreatedCode(result);
      setCodeNameHint("");
      toast({ type: "success", title: "激活码已生成" });
      await loadData();
    } catch (err) {
      toast({
        type: "error",
        title: "生成激活码失败",
        description: err instanceof Error ? err.message : "未知错误",
      });
    } finally {
      setBusy(false);
    }
  };

  const copyText = async (value: string, successTitle: string) => {
    await navigator.clipboard.writeText(value);
    toast({ type: "info", title: successTitle });
  };

  const openEdit = (device: ScienceDevice) => {
    setEditingDevice(device);
    setEditForm({
      device_id: device.device_id,
      name: device.name ?? "",
      is_active: device.is_active,
      kb_scope_mode: "files",
      kb_scope_dept_ids: [],
      kb_scope_file_ids: [],
    });
  };

  const closeEdit = () => {
    setEditingDevice(null);
    setEditForm({ ...EMPTY_EDIT_FORM });
  };

  const saveDevice = async () => {
    if (!editingDevice) return;
    setBusy(true);
    try {
      await scienceAdminService.upsertDevice({
        ...editForm,
        device_id: editingDevice.device_id,
        name: editForm.name?.trim() || undefined,
        service_user_id: undefined,
        dept_id: undefined,
        kb_scope_mode: "files",
        kb_scope_dept_ids: [],
        kb_scope_file_ids: [],
      });
      toast({ type: "success", title: "设备已更新" });
      closeEdit();
      await loadData();
    } catch (err) {
      toast({
        type: "error",
        title: "更新设备失败",
        description: err instanceof Error ? err.message : "未知错误",
      });
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async () => {
    if (!confirmTarget) return;
    setBusy(true);
    try {
      await scienceAdminService.upsertDevice({
        device_id: confirmTarget.device_id,
        name: confirmTarget.name ?? undefined,
        service_user_id: undefined,
        dept_id: undefined,
        is_active: !confirmTarget.is_active,
        kb_scope_mode: "files",
        kb_scope_dept_ids: [],
        kb_scope_file_ids: [],
      });
      toast({ type: "success", title: confirmTarget.is_active ? "设备已停用" : "设备已启用" });
      setConfirmOpen(false);
      setConfirmTarget(null);
      await loadData();
    } catch (err) {
      toast({
        type: "error",
        title: "更新设备状态失败",
        description: err instanceof Error ? err.message : "未知错误",
      });
    } finally {
      setBusy(false);
    }
  };

  const deleteDevice = async () => {
    if (!deleteTarget) return;
    setBusy(true);
    try {
      await scienceAdminService.deleteDevice(deleteTarget.device_id);
      toast({ type: "success", title: "设备已删除" });
      setDeleteOpen(false);
      setDeleteTarget(null);
      if (editingDevice?.device_id === deleteTarget.device_id) {
        closeEdit();
      }
      await loadData();
    } catch (err) {
      toast({
        type: "error",
        title: "删除设备失败",
        description: err instanceof Error ? err.message : "未知错误",
      });
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
        <CardContent className="py-10 text-center text-manus-muted">加载设备管理数据中...</CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
        <CardHeader className="border-b border-manus-border/50">
          <CardTitle>生成激活码</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,1fr)_180px] md:items-end">
            <div className="space-y-2">
              <Label>设备名称（可选）</Label>
              <Input
                placeholder="例如：大厅 1 号机"
                value={codeNameHint}
                onChange={(e) => setCodeNameHint(e.target.value)}
              />
              <p className="text-xs text-manus-muted">
                不需要先创建设备。生成的激活码输入到 Kiosk 后，系统会自动创建设备并绑定到当前工作区。
              </p>
            </div>
            <Button onClick={() => void generateActivationCode()} disabled={busy} className="h-11 w-full bg-black text-white hover:bg-black/90">
              <Plus className="mr-2 h-4 w-4" />生成激活码
            </Button>
          </div>
          <div className="rounded-lg border border-manus-border bg-manus-tertiary/40 px-4 py-3 text-sm text-manus-muted">
            激活码是一机一码、一次性使用。设备首次激活成功后，会自动获得设备 ID 与 device token。
          </div>
        </CardContent>
      </Card>

      <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
        <CardHeader className="border-b border-manus-border/50 flex flex-row items-center justify-between">
          <CardTitle>激活码记录</CardTitle>
          <Badge variant="outline" className="border-manus-border text-manus-text">待激活 {pendingCodes.length}</Badge>
        </CardHeader>
        <CardContent>
          <div className="overflow-auto rounded-lg border border-manus-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-manus-tertiary text-manus-muted">
                  <th className="text-left py-3 px-4 font-medium">名称</th>
                  <th className="text-left py-3 px-4 font-medium">后四位</th>
                  <th className="text-left py-3 px-4 font-medium">状态</th>
                  <th className="text-left py-3 px-4 font-medium">创建时间</th>
                  <th className="text-left py-3 px-4 font-medium">绑定设备</th>
                </tr>
              </thead>
              <tbody>
                {activationCodes.length === 0 ? (
                  <tr><td colSpan={5} className="py-8 text-center text-manus-muted">暂无激活码记录</td></tr>
                ) : (
                  activationCodes.map((code) => (
                    <tr key={code.code_id} className="border-t border-manus-border hover:bg-manus-tertiary/50 transition-colors">
                      <td className="py-3 px-4">{code.name_hint || '未命名设备'}</td>
                      <td className="py-3 px-4 font-mono">{code.activation_key_last4}</td>
                      <td className="py-3 px-4">
                        <Badge variant="outline" className={activationCodeStatusBadge(code.status)}>
                          {code.status === 'used' ? '已使用' : code.status === 'revoked' ? '已作废' : '待激活'}
                        </Badge>
                      </td>
                      <td className="py-3 px-4 text-xs text-manus-muted">{new Date(code.created_at).toLocaleString('zh-CN')}</td>
                      <td className="py-3 px-4 font-mono text-xs text-manus-muted">{code.used_device_id || '-'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
        <CardHeader className="border-b border-manus-border/50">
          <CardTitle>已激活设备</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-auto rounded-lg border border-manus-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-manus-tertiary text-manus-muted">
                  <th className="text-left py-3 px-4 font-medium">设备 ID</th>
                  <th className="text-left py-3 px-4 font-medium">设备名称</th>
                  <th className="text-left py-3 px-4 font-medium">状态</th>
                  <th className="text-left py-3 px-4 font-medium">设备 Token</th>
                  <th className="text-left py-3 px-4 font-medium">激活码后四位</th>
                  <th className="text-left py-3 px-4 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {devices.length === 0 ? (
                  <tr><td colSpan={6} className="py-8 text-center text-manus-muted">暂无已激活设备</td></tr>
                ) : (
                  devices.map((device) => (
                    <tr key={device.device_id} className="border-t border-manus-border hover:bg-manus-tertiary/50 transition-colors">
                      <td className="py-3 px-4 font-mono">{device.device_id}</td>
                      <td className="py-3 px-4 font-medium">{device.name || '未命名设备'}</td>
                      <td className="py-3 px-4">
                        <Badge variant="outline" className={statusBadge(device.is_active)}>
                          {device.is_active ? '启用中' : '已停用'}
                        </Badge>
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          <code className="text-xs text-manus-muted">{device.device_token.slice(0, 10)}...</code>
                          <Button size="sm" variant="outline" onClick={() => void copyText(device.device_token, '设备 Token 已复制')} className="h-7 px-2 bg-manus-tertiary border-manus-border">
                            <Copy className="mr-1 h-3 w-3" />复制
                          </Button>
                        </div>
                      </td>
                      <td className="py-3 px-4 text-xs text-manus-muted">{device.activation_key_last4 || '-'}</td>
                      <td className="py-3 px-4">
                        <div className="flex flex-wrap gap-2">
                          <Button size="sm" variant="outline" onClick={() => openEdit(device)} className="h-7 px-2 bg-manus-tertiary border-manus-border">
                            <Pencil className="mr-1 h-3 w-3" />编辑
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setConfirmTarget(device);
                              setConfirmOpen(true);
                            }}
                            className="h-7 px-2 bg-manus-tertiary border-manus-border"
                          >
                            <Power className="mr-1 h-3 w-3" />{device.is_active ? '停用' : '启用'}
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setDeleteTarget(device);
                              setDeleteOpen(true);
                            }}
                            className="h-7 px-2 bg-manus-tertiary border-manus-border"
                          >
                            <Trash2 className="mr-1 h-3 w-3" />删除
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Dialog open={Boolean(createdCode)} onOpenChange={(open) => { if (!open) setCreatedCode(null); }}>
        <DialogContent className="bg-manus-secondary border-manus-border">
          <DialogHeader>
            <DialogTitle>激活码已生成</DialogTitle>
            <DialogDescription>
              这是完整激活码，只会展示这一次。请立即复制给 Kiosk 设备使用。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="rounded-lg border border-manus-border bg-manus-tertiary/40 px-4 py-3">
              <div className="text-xs text-manus-muted">设备名称</div>
              <div className="mt-1 text-manus-text">{createdCode?.name_hint || '未命名设备'}</div>
            </div>
            <div className="rounded-lg border border-manus-border bg-black px-4 py-4 font-mono text-lg tracking-widest text-white">
              {createdCode?.activation_key}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreatedCode(null)}>关闭</Button>
            <Button
              onClick={() => createdCode && void copyText(createdCode.activation_key, '激活码已复制')}
              className="bg-black text-white hover:bg-black/90"
            >
              <Copy className="mr-2 h-4 w-4" />复制激活码
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(editingDevice)} onOpenChange={(open) => { if (!open) closeEdit(); }}>
        <DialogContent className="bg-manus-secondary border-manus-border">
          <DialogHeader>
            <DialogTitle>编辑设备</DialogTitle>
            <DialogDescription>修改已激活设备的名称与启停状态。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>设备 ID</Label>
              <Input value={editingDevice?.device_id || ''} disabled />
            </div>
            <div className="space-y-2">
              <Label>设备名称</Label>
              <Input value={editForm.name ?? ''} onChange={(e) => setEditForm((prev) => ({ ...prev, name: e.target.value }))} />
            </div>
            <div className="flex items-center gap-2">
              <Label>状态</Label>
              <Switch checked={editForm.is_active} onCheckedChange={(value) => setEditForm((prev) => ({ ...prev, is_active: value }))} />
              <span className="text-sm text-manus-text">{editForm.is_active ? '启用' : '停用'}</span>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeEdit}>取消</Button>
            <Button onClick={() => void saveDevice()} disabled={busy} className="bg-black text-white hover:bg-black/90">保存修改</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={confirmTarget?.is_active ? '停用设备' : '启用设备'}
        description={confirmTarget ? `将${confirmTarget.is_active ? '停用' : '启用'}设备 ${confirmTarget.device_id}。` : undefined}
        onConfirm={() => void toggleActive()}
        loading={busy}
        confirmText={confirmTarget?.is_active ? '确认停用' : '确认启用'}
      />
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="删除设备"
        description={deleteTarget ? `将永久删除设备 ${deleteTarget.device_id}。此操作不可撤销。` : undefined}
        onConfirm={() => void deleteDevice()}
        loading={busy}
        confirmText="确认删除"
        variant="destructive"
      />
    </div>
  );
}
