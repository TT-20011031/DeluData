import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import {
  Building2,
  Loader2,
  Pencil,
  Plus,
  Power,
  PowerOff,
  Search,
  ShieldCheck,
  Users,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import api from "@/lib/api";

interface Workspace {
  id: string;
  name: string;
  code: string;
  plan: string;
  max_users: number;
  is_active: boolean;
  museum_enabled: boolean;
  kiosk_enabled: boolean;
  description: string | null;
  created_at: string;
}

interface WorkspaceAdmin {
  id: string;
  username: string;
  email: string | null;
  disabled: boolean;
}

type ModalMode = "create" | "edit";
type StatusFilter = "all" | "active" | "disabled";

interface WorkspaceFormData {
  name: string;
  code: string;
  plan: string;
  max_users: number;
  description: string;
  is_active: boolean;
  museum_enabled: boolean;
  kiosk_enabled: boolean;
  admin_username: string;
  admin_password: string;
  admin_email: string;
}

const PLAN_LABELS: Record<string, string> = {
  free: "免费版",
  pro: "专业版",
  enterprise: "企业版",
};

const PLAN_BADGES: Record<string, string> = {
  free: "bg-zinc-100 text-zinc-700",
  pro: "bg-blue-100 text-blue-700",
  enterprise: "bg-purple-100 text-purple-700",
};

function createEmptyForm(): WorkspaceFormData {
  return {
    name: "",
    code: "",
    plan: "free",
    max_users: 10,
    description: "",
    is_active: true,
    museum_enabled: false,
    kiosk_enabled: false,
    admin_username: "",
    admin_password: "",
    admin_email: "",
  };
}

function FeatureSwitch({
  enabled,
  label,
  onClick,
  disabled = false,
}: {
  enabled: boolean;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "px-2.5 py-1 rounded-full text-xs font-medium border transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
        enabled
          ? "bg-emerald-100 text-emerald-700 border-emerald-200"
          : "bg-zinc-100 text-zinc-500 border-zinc-200",
      )}
      title={`切换 ${label}`}
    >
      {enabled ? "已开启" : "已关闭"}
    </button>
  );
}

function MetricCard({
  title,
  value,
  icon,
  accentClass,
}: {
  title: string;
  value: number;
  icon: ReactNode;
  accentClass: string;
}) {
  return (
    <Card className="bg-manus-elevated border-manus-border">
      <CardContent className="p-4 flex items-center gap-4">
        <div className={cn("w-12 h-12 rounded-xl flex items-center justify-center", accentClass)}>
          {icon}
        </div>
        <div>
          <p className="text-sm text-manus-muted">{title}</p>
          <p className="text-2xl font-bold text-manus-text">{value}</p>
        </div>
      </CardContent>
    </Card>
  );
}

export default function TenantsPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [modalMode, setModalMode] = useState<ModalMode>("create");
  const [editingWorkspaceId, setEditingWorkspaceId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionWorkspaceId, setActionWorkspaceId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [formData, setFormData] = useState<WorkspaceFormData>(createEmptyForm());

  const isEditMode = modalMode === "edit";

  const fetchWorkspaces = async () => {
    try {
      const response = await api.get<Workspace[]>("/workspaces");
      setWorkspaces(response.data);
    } catch (err) {
      console.error("Failed to fetch workspaces:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchWorkspaces();
  }, []);

  const closeModal = () => {
    setShowModal(false);
    setModalMode("create");
    setEditingWorkspaceId(null);
    setDetailLoading(false);
    setSubmitting(false);
    setError("");
    setFormData(createEmptyForm());
  };

  const openCreateModal = () => {
    setModalMode("create");
    setEditingWorkspaceId(null);
    setError("");
    setFormData(createEmptyForm());
    setShowModal(true);
  };

  const openEditModal = async (workspace: Workspace) => {
    setModalMode("edit");
    setEditingWorkspaceId(workspace.id);
    setError("");
    setFormData({
      name: workspace.name,
      code: workspace.code,
      plan: workspace.plan,
      max_users: workspace.max_users,
      description: workspace.description ?? "",
      is_active: workspace.is_active,
      museum_enabled: workspace.museum_enabled,
      kiosk_enabled: workspace.kiosk_enabled,
      admin_username: "",
      admin_password: "",
      admin_email: "",
    });
    setShowModal(true);
    setDetailLoading(true);

    try {
      const [workspaceRes, adminRes] = await Promise.all([
        api.get<Workspace>(`/workspaces/${workspace.id}`),
        api.get<WorkspaceAdmin>(`/workspaces/${workspace.id}/admin`),
      ]);

      setFormData({
        name: workspaceRes.data.name,
        code: workspaceRes.data.code,
        plan: workspaceRes.data.plan,
        max_users: workspaceRes.data.max_users,
        description: workspaceRes.data.description ?? "",
        is_active: workspaceRes.data.is_active,
        museum_enabled: workspaceRes.data.museum_enabled,
        kiosk_enabled: workspaceRes.data.kiosk_enabled,
        admin_username: adminRes.data.username,
        admin_password: "",
        admin_email: adminRes.data.email ?? "",
      });
    } catch (err: any) {
      console.error("Failed to load workspace detail:", err);
      setError(err?.response?.data?.detail || "加载租户详情失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError("");

    try {
      if (isEditMode && editingWorkspaceId) {
        await api.put(`/workspaces/${editingWorkspaceId}/admin`, {
          username: formData.admin_username.trim(),
          email: formData.admin_email.trim() || null,
          ...(formData.admin_password.trim()
            ? { password: formData.admin_password.trim() }
            : {}),
        });

        await api.put(`/workspaces/${editingWorkspaceId}`, {
          name: formData.name.trim(),
          plan: formData.plan,
          max_users: formData.max_users,
          description: formData.description.trim() || null,
          is_active: formData.is_active,
          museum_enabled: formData.museum_enabled,
          kiosk_enabled: formData.kiosk_enabled,
        });
      } else {
        await api.post("/workspaces", {
          name: formData.name.trim(),
          code: formData.code.trim(),
          plan: formData.plan,
          max_users: formData.max_users,
          description: formData.description.trim() || null,
          museum_enabled: formData.museum_enabled,
          kiosk_enabled: formData.kiosk_enabled,
          admin_username: formData.admin_username.trim(),
          admin_password: formData.admin_password,
          admin_email: formData.admin_email.trim() || null,
        });
      }

      closeModal();
      await fetchWorkspaces();
    } catch (err: any) {
      setError(err?.response?.data?.detail || (isEditMode ? "更新失败" : "创建失败"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleToggleActive = async (workspace: Workspace) => {
    setActionWorkspaceId(workspace.id);
    try {
      if (workspace.is_active) {
        await api.post(`/workspaces/${workspace.id}/disable`);
      } else {
        await api.put(`/workspaces/${workspace.id}`, { is_active: true });
      }
      await fetchWorkspaces();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "状态更新失败");
    } finally {
      setActionWorkspaceId(null);
    }
  };

  const handleToggleFeature = async (
    workspace: Workspace,
    feature: "museum_enabled" | "kiosk_enabled",
  ) => {
    setActionWorkspaceId(workspace.id);
    try {
      await api.put(`/workspaces/${workspace.id}`, {
        [feature]: !workspace[feature],
      });
      await fetchWorkspaces();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "功能开关更新失败");
    } finally {
      setActionWorkspaceId(null);
    }
  };

  const totalQuota = useMemo(
    () => workspaces.reduce((sum, workspace) => sum + workspace.max_users, 0),
    [workspaces],
  );

  const filteredWorkspaces = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return workspaces.filter((workspace) => {
      if (statusFilter === "active" && !workspace.is_active) {
        return false;
      }
      if (statusFilter === "disabled" && workspace.is_active) {
        return false;
      }
      if (!keyword) {
        return true;
      }
      return (
        workspace.name.toLowerCase().includes(keyword) ||
        workspace.code.toLowerCase().includes(keyword) ||
        (workspace.description ?? "").toLowerCase().includes(keyword)
      );
    });
  }, [workspaces, search, statusFilter]);

  const formatDate = (value: string) => new Date(value).toLocaleString("zh-CN");

  return (
    <div className="animate-fade-in space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-manus-text mb-1">租户管理</h1>
          <p className="text-manus-muted">
            统一管理租户状态、Museum/Kiosk 开关，以及租户管理员账号信息。
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-sm text-manus-muted">总配额 {totalQuota}</div>
          <Button onClick={openCreateModal} className="gap-2">
            <Plus size={18} />
            新增租户
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          title="租户总数"
          value={workspaces.length}
          icon={<Building2 size={24} className="text-blue-500" />}
          accentClass="bg-blue-50"
        />
        <MetricCard
          title="活跃租户"
          value={workspaces.filter((workspace) => workspace.is_active).length}
          icon={<Power size={24} className="text-green-500" />}
          accentClass="bg-green-50"
        />
        <MetricCard
          title="Museum 已开通"
          value={workspaces.filter((workspace) => workspace.museum_enabled).length}
          icon={<ShieldCheck size={24} className="text-violet-500" />}
          accentClass="bg-violet-50"
        />
        <MetricCard
          title="Kiosk 已开通"
          value={workspaces.filter((workspace) => workspace.kiosk_enabled).length}
          icon={<Users size={24} className="text-amber-500" />}
          accentClass="bg-amber-50"
        />
      </div>

      <Card className="bg-manus-elevated border-manus-border">
        <CardContent className="p-4 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="relative w-full lg:max-w-sm">
            <Search
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-manus-muted"
            />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索企业名称、标识码、描述"
              className="pl-9 bg-manus-secondary border-manus-border"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {([
              ["all", "全部"],
              ["active", "仅活跃"],
              ["disabled", "仅禁用"],
            ] as const).map(([value, label]) => (
              <Button
                key={value}
                type="button"
                size="sm"
                variant={statusFilter === value ? "default" : "outline"}
                onClick={() => setStatusFilter(value)}
              >
                {label}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card className="bg-manus-elevated border-manus-border shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1080px]">
            <thead>
              <tr className="bg-manus-secondary border-b border-manus-border">
                <th className="px-5 py-4 text-left font-semibold text-manus-text">企业名称</th>
                <th className="px-5 py-4 text-left font-semibold text-manus-text">标识码</th>
                <th className="px-5 py-4 text-center font-semibold text-manus-text">套餐 / 配额</th>
                <th className="px-5 py-4 text-center font-semibold text-manus-text">Museum</th>
                <th className="px-5 py-4 text-center font-semibold text-manus-text">Kiosk</th>
                <th className="px-5 py-4 text-center font-semibold text-manus-text">状态</th>
                <th className="px-5 py-4 text-left font-semibold text-manus-text">创建时间</th>
                <th className="px-5 py-4 text-center font-semibold text-manus-text">操作</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={8} className="px-5 py-10 text-center text-manus-muted">
                    <span className="inline-flex items-center gap-2">
                      <Loader2 size={16} className="animate-spin" />
                      加载中...
                    </span>
                  </td>
                </tr>
              ) : filteredWorkspaces.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-5 py-10 text-center text-manus-muted">
                    当前筛选条件下没有租户
                  </td>
                </tr>
              ) : (
                filteredWorkspaces.map((workspace) => {
                  const rowLoading = actionWorkspaceId === workspace.id;
                  return (
                    <tr
                      key={workspace.id}
                      className="border-b border-manus-border hover:bg-manus-secondary/40 transition-colors"
                    >
                      <td className="px-5 py-4">
                        <div className="font-medium text-manus-text flex items-center gap-2">
                          {workspace.name}
                          {workspace.code === "default" ? (
                            <span className="px-2 py-0.5 rounded-full text-[11px] bg-zinc-100 text-zinc-600">
                              系统默认
                            </span>
                          ) : null}
                        </div>
                        {workspace.description ? (
                          <div className="text-xs text-manus-muted mt-1 line-clamp-2">
                            {workspace.description}
                          </div>
                        ) : null}
                      </td>
                      <td className="px-5 py-4">
                        <code className="px-2 py-1 bg-manus-secondary rounded text-sm">
                          {workspace.code}
                        </code>
                      </td>
                      <td className="px-5 py-4 text-center">
                        <div className="flex flex-col items-center gap-2">
                          <span
                            className={cn(
                              "px-2 py-1 rounded-full text-xs font-medium",
                              PLAN_BADGES[workspace.plan] || PLAN_BADGES.free,
                            )}
                          >
                            {PLAN_LABELS[workspace.plan] || workspace.plan}
                          </span>
                          <span className="text-xs text-manus-muted">
                            用户上限 {workspace.max_users}
                          </span>
                        </div>
                      </td>
                      <td className="px-5 py-4 text-center">
                        <FeatureSwitch
                          enabled={workspace.museum_enabled}
                          label="Museum"
                          disabled={rowLoading}
                          onClick={() => handleToggleFeature(workspace, "museum_enabled")}
                        />
                      </td>
                      <td className="px-5 py-4 text-center">
                        <FeatureSwitch
                          enabled={workspace.kiosk_enabled}
                          label="Kiosk"
                          disabled={rowLoading}
                          onClick={() => handleToggleFeature(workspace, "kiosk_enabled")}
                        />
                      </td>
                      <td className="px-5 py-4 text-center">
                        <span
                          className={cn(
                            "inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium",
                            workspace.is_active
                              ? "bg-green-100 text-green-700"
                              : "bg-red-100 text-red-700",
                          )}
                        >
                          {workspace.is_active ? "活跃" : "禁用"}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-sm text-manus-muted">
                        {formatDate(workspace.created_at)}
                      </td>
                      <td className="px-5 py-4">
                        <div className="flex items-center justify-center gap-2">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="gap-1"
                            onClick={() => void openEditModal(workspace)}
                          >
                            <Pencil size={14} />
                            编辑
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => void handleToggleActive(workspace)}
                            disabled={rowLoading || (workspace.code === "default" && workspace.is_active)}
                            className={cn(
                              "gap-1",
                              workspace.is_active
                                ? "text-orange-500 hover:text-orange-600 hover:bg-orange-50"
                                : "text-green-500 hover:text-green-600 hover:bg-green-50",
                            )}
                            title={workspace.is_active ? "禁用租户" : "启用租户"}
                          >
                            {rowLoading ? (
                              <Loader2 size={14} className="animate-spin" />
                            ) : workspace.is_active ? (
                              <PowerOff size={14} />
                            ) : (
                              <Power size={14} />
                            )}
                            {workspace.is_active ? "禁用" : "启用"}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {showModal ? (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-[1000] p-4"
          onClick={closeModal}
        >
          <Card
            className="w-[720px] max-w-[95vw] max-h-[92vh] overflow-y-auto bg-manus-elevated border-manus-border shadow-xl animate-slide-up"
            onClick={(e) => e.stopPropagation()}
          >
            <CardHeader className="border-b border-manus-border">
              <CardTitle className="text-xl text-manus-text">
                {isEditMode ? "编辑租户" : "新增租户"}
              </CardTitle>
              <div className="text-sm text-manus-muted">
                {isEditMode
                  ? "修改租户基础信息、功能开关和管理员账号。"
                  : "创建租户并初始化管理员账号。"}
              </div>
            </CardHeader>
            <CardContent className="p-6">
              {error ? (
                <div className="p-3 mb-4 bg-red-50 text-red-600 rounded-lg text-sm">{error}</div>
              ) : null}
              {detailLoading ? (
                <div className="py-12 text-center text-manus-muted">
                  <span className="inline-flex items-center gap-2">
                    <Loader2 size={16} className="animate-spin" />
                    加载租户详情...
                  </span>
                </div>
              ) : (
                <form onSubmit={handleSubmit} className="space-y-6">
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <h3 className="font-medium text-manus-text">租户信息</h3>
                      {isEditMode ? (
                        <div className="text-xs text-manus-muted">
                          租户标识码固定为 <code>{formData.code}</code>
                        </div>
                      ) : null}
                    </div>
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="workspace-name">企业名称 *</Label>
                        <Input
                          id="workspace-name"
                          value={formData.name}
                          onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
                          required
                          className="bg-manus-secondary border-manus-border"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="workspace-code">标识码 *</Label>
                        <Input
                          id="workspace-code"
                          value={formData.code}
                          onChange={(e) => setFormData((prev) => ({ ...prev, code: e.target.value }))}
                          required
                          readOnly={isEditMode}
                          pattern="^[a-zA-Z0-9_-]+$"
                          className={cn(
                            "bg-manus-secondary border-manus-border",
                            isEditMode && "cursor-not-allowed opacity-80",
                          )}
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="workspace-plan">套餐</Label>
                        <select
                          id="workspace-plan"
                          value={formData.plan}
                          onChange={(e) => setFormData((prev) => ({ ...prev, plan: e.target.value }))}
                          className="w-full h-10 px-3 rounded-md bg-manus-secondary border border-manus-border text-manus-text"
                        >
                          <option value="free">免费版</option>
                          <option value="pro">专业版</option>
                          <option value="enterprise">企业版</option>
                        </select>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="workspace-max-users">用户上限</Label>
                        <Input
                          id="workspace-max-users"
                          type="number"
                          min={1}
                          max={10000}
                          value={formData.max_users}
                          onChange={(e) =>
                            setFormData((prev) => ({
                              ...prev,
                              max_users: Math.max(1, parseInt(e.target.value || "1", 10)),
                            }))
                          }
                          className="bg-manus-secondary border-manus-border"
                        />
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="workspace-description">描述</Label>
                      <textarea
                        id="workspace-description"
                        value={formData.description}
                        onChange={(e) =>
                          setFormData((prev) => ({ ...prev, description: e.target.value }))
                        }
                        rows={3}
                        className="w-full rounded-md border border-manus-border bg-manus-secondary px-3 py-2 text-sm text-manus-text outline-none focus-visible:ring-1 focus-visible:ring-ring"
                        placeholder="填写租户用途、项目说明或运维备注"
                      />
                    </div>
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                      <button
                        type="button"
                        onClick={() =>
                          setFormData((prev) => ({ ...prev, is_active: !prev.is_active }))
                        }
                        disabled={formData.code === "default" && formData.is_active}
                        className={cn(
                          "rounded-lg border px-3 py-3 text-left transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
                          formData.is_active
                            ? "border-green-200 bg-green-50 text-green-700"
                            : "border-red-200 bg-red-50 text-red-700",
                        )}
                      >
                        <div className="text-xs opacity-80">租户状态</div>
                        <div className="mt-1 font-medium">{formData.is_active ? "活跃" : "禁用"}</div>
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setFormData((prev) => ({
                            ...prev,
                            museum_enabled: !prev.museum_enabled,
                          }))
                        }
                        className={cn(
                          "rounded-lg border px-3 py-3 text-left transition-colors",
                          formData.museum_enabled
                            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                            : "border-zinc-200 bg-zinc-50 text-zinc-600",
                        )}
                      >
                        <div className="text-xs opacity-80">Museum 访问</div>
                        <div className="mt-1 font-medium">
                          {formData.museum_enabled ? "已开通" : "已关闭"}
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setFormData((prev) => ({
                            ...prev,
                            kiosk_enabled: !prev.kiosk_enabled,
                          }))
                        }
                        className={cn(
                          "rounded-lg border px-3 py-3 text-left transition-colors",
                          formData.kiosk_enabled
                            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                            : "border-zinc-200 bg-zinc-50 text-zinc-600",
                        )}
                      >
                        <div className="text-xs opacity-80">Kiosk 访问</div>
                        <div className="mt-1 font-medium">
                          {formData.kiosk_enabled ? "已开通" : "已关闭"}
                        </div>
                      </button>
                    </div>
                  </div>

                  <div className="space-y-4 border-t border-manus-border pt-5">
                    <h3 className="font-medium text-manus-text">租户管理员</h3>
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="admin-username">管理员账号 *</Label>
                        <Input
                          id="admin-username"
                          value={formData.admin_username}
                          onChange={(e) =>
                            setFormData((prev) => ({ ...prev, admin_username: e.target.value }))
                          }
                          required
                          className="bg-manus-secondary border-manus-border"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="admin-email">管理员邮箱</Label>
                        <Input
                          id="admin-email"
                          type="email"
                          value={formData.admin_email}
                          onChange={(e) =>
                            setFormData((prev) => ({ ...prev, admin_email: e.target.value }))
                          }
                          className="bg-manus-secondary border-manus-border"
                        />
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="admin-password">
                        {isEditMode ? "重置密码（留空则不修改）" : "管理员密码 *"}
                      </Label>
                      <Input
                        id="admin-password"
                        type="password"
                        value={formData.admin_password}
                        onChange={(e) =>
                          setFormData((prev) => ({ ...prev, admin_password: e.target.value }))
                        }
                        required={!isEditMode}
                        minLength={6}
                        className="bg-manus-secondary border-manus-border"
                      />
                    </div>
                  </div>

                  <div className="flex justify-end gap-3 pt-2">
                    <Button type="button" variant="outline" onClick={closeModal}>
                      取消
                    </Button>
                    <Button type="submit" disabled={submitting}>
                      {submitting ? (
                        <>
                          <Loader2 size={16} className="animate-spin" />
                          {isEditMode ? "保存中..." : "创建中..."}
                        </>
                      ) : isEditMode ? (
                        "保存修改"
                      ) : (
                        "创建租户"
                      )}
                    </Button>
                  </div>
                </form>
              )}
            </CardContent>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
