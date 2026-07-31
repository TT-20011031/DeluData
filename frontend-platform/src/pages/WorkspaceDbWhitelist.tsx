import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle,
    Building2,
    CheckCircle2,
    Database,
    Loader2,
    Plus,
    RefreshCw,
    Save,
    ShieldCheck,
    Trash2,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import api from '@/lib/api';

interface WorkspaceItem {
    id: string;
    name: string;
    code: string;
}

interface EndpointDraft {
    host: string;
    port: string;
}

interface WhitelistResponse {
    workspace_id: string;
    workspace_code?: string;
    workspace_name?: string;
    is_enabled: boolean;
    allowed_endpoints: Array<{ host: string; port: number }>;
    note?: string | null;
}

interface WhitelistFormState {
    is_enabled: boolean;
    allowed_endpoints: EndpointDraft[];
    note: string;
}

function extractErrorMessage(error: unknown, fallback: string): string {
    const normalizeDetail = (detail: unknown): string | null => {
        if (typeof detail === 'string') {
            return detail;
        }
        if (Array.isArray(detail)) {
            const messages = detail
                .map((item) => {
                    if (typeof item === 'string') return item;
                    if (item && typeof item === 'object' && 'msg' in item) {
                        const msg = (item as { msg?: unknown }).msg;
                        if (typeof msg === 'string') return msg;
                    }
                    return null;
                })
                .filter((msg): msg is string => Boolean(msg));
            if (messages.length > 0) {
                return messages.join('; ');
            }
            return null;
        }
        if (detail && typeof detail === 'object') {
            if ('msg' in detail && typeof (detail as { msg?: unknown }).msg === 'string') {
                return (detail as { msg: string }).msg;
            }
            return JSON.stringify(detail);
        }
        return null;
    };

    if (axios.isAxiosError(error)) {
        const rawDetail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
        return normalizeDetail(rawDetail) || error.message || fallback;
    }
    if (error instanceof Error) {
        return error.message;
    }
    return fallback;
}

function normalizeFromResponse(data: WhitelistResponse): WhitelistFormState {
    return {
        is_enabled: Boolean(data.is_enabled),
        allowed_endpoints: (data.allowed_endpoints || []).map((item) => ({
            host: item.host,
            port: String(item.port),
        })),
        note: data.note || '',
    };
}

export default function WorkspaceDbWhitelistPage() {
    const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([]);
    const [selectedWorkspaceId, setSelectedWorkspaceId] = useState('');
    const [form, setForm] = useState<WhitelistFormState>({
        is_enabled: false,
        allowed_endpoints: [],
        note: '',
    });
    const [isLoadingWorkspaces, setIsLoadingWorkspaces] = useState(true);
    const [isLoadingConfig, setIsLoadingConfig] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');

    const selectedWorkspace = useMemo(
        () => workspaces.find((workspace) => workspace.id === selectedWorkspaceId) || null,
        [selectedWorkspaceId, workspaces],
    );

    const fetchWorkspaces = useCallback(async () => {
        setIsLoadingWorkspaces(true);
        setError('');

        try {
            const response = await api.get<WorkspaceItem[]>('/workspaces', {
                params: { skip: 0, limit: 100 },
            });
            const list = response.data || [];
            setWorkspaces(list);
            if (list.length > 0 && !selectedWorkspaceId) {
                setSelectedWorkspaceId(list[0].id);
            }
        } catch (errorUnknown) {
            setError(extractErrorMessage(errorUnknown, '加载租户列表失败'));
        } finally {
            setIsLoadingWorkspaces(false);
        }
    }, [selectedWorkspaceId]);

    const fetchWhitelistConfig = useCallback(async (workspaceId: string) => {
        if (!workspaceId) return;

        setIsLoadingConfig(true);
        setError('');
        setSuccess('');

        try {
            const response = await api.get<WhitelistResponse>(
                `/workspaces/${workspaceId}/db-whitelist`,
            );
            setForm(normalizeFromResponse(response.data));
        } catch (errorUnknown) {
            setError(extractErrorMessage(errorUnknown, '加载白名单配置失败'));
        } finally {
            setIsLoadingConfig(false);
        }
    }, []);

    useEffect(() => {
        fetchWorkspaces();
    }, [fetchWorkspaces]);

    useEffect(() => {
        if (selectedWorkspaceId) {
            fetchWhitelistConfig(selectedWorkspaceId);
        }
    }, [fetchWhitelistConfig, selectedWorkspaceId]);

    const updateEndpoint = (index: number, key: keyof EndpointDraft, value: string) => {
        setForm((prev) => ({
            ...prev,
            allowed_endpoints: prev.allowed_endpoints.map((endpoint, idx) =>
                idx === index ? { ...endpoint, [key]: value } : endpoint,
            ),
        }));
    };

    const addEndpoint = () => {
        setForm((prev) => ({
            ...prev,
            allowed_endpoints: [...prev.allowed_endpoints, { host: '', port: '3306' }],
        }));
    };

    const removeEndpoint = (index: number) => {
        setForm((prev) => ({
            ...prev,
            allowed_endpoints: prev.allowed_endpoints.filter((_, idx) => idx !== index),
        }));
    };

    const buildEndpointPayload = (): Array<{ host: string; port: number }> => {
        const dedup = new Map<string, { host: string; port: number }>();

        for (let index = 0; index < form.allowed_endpoints.length; index += 1) {
            const endpoint = form.allowed_endpoints[index];
            const host = endpoint.host.trim().toLowerCase();
            const portRaw = endpoint.port.trim();

            if (!host && !portRaw) continue;
            if (!host) throw new Error(`第 ${index + 1} 行主机不能为空`);

            const port = portRaw ? Number(portRaw) : 3306;
            if (!Number.isInteger(port) || port < 1 || port > 65535) {
                throw new Error(`第 ${index + 1} 行端口无效，请输入 1-65535`);
            }
            dedup.set(`${host}:${port}`, { host, port });
        }

        return Array.from(dedup.values());
    };

    const handleSave = async () => {
        if (!selectedWorkspaceId) return;

        setIsSaving(true);
        setError('');
        setSuccess('');

        try {
            const payload = {
                is_enabled: form.is_enabled,
                allowed_endpoints: buildEndpointPayload(),
                note: form.note.trim() || null,
            };
            const response = await api.put<WhitelistResponse>(
                `/workspaces/${selectedWorkspaceId}/db-whitelist`,
                payload,
            );
            setForm(normalizeFromResponse(response.data));
            setSuccess('白名单配置保存成功');
        } catch (errorUnknown) {
            setError(extractErrorMessage(errorUnknown, '保存白名单配置失败'));
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <div className="animate-fade-in">
            <div className="flex justify-between items-center mb-6">
                <div>
                    <h1 className="text-2xl font-bold text-manus-text mb-1 flex items-center gap-2">
                        <ShieldCheck size={24} />
                        SQL 白名单管理
                    </h1>
                    <p className="text-manus-muted">
                        平台端按租户统一管理数据库白名单准入策略
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Button
                        variant="outline"
                        onClick={() => fetchWhitelistConfig(selectedWorkspaceId)}
                        disabled={!selectedWorkspaceId || isLoadingConfig || isSaving}
                        className="gap-2"
                    >
                        <RefreshCw size={16} className={isLoadingConfig ? 'animate-spin' : ''} />
                        刷新
                    </Button>
                    <Button
                        onClick={handleSave}
                        disabled={!selectedWorkspaceId || isLoadingConfig || isSaving}
                        className="gap-2"
                    >
                        {isSaving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                        保存
                    </Button>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                <Card className="bg-manus-elevated border-manus-border">
                    <CardContent className="p-4 flex items-center gap-3">
                        <div className="w-11 h-11 rounded-xl bg-blue-50 text-blue-500 flex items-center justify-center">
                            <Building2 size={20} />
                        </div>
                        <div>
                            <p className="text-sm text-manus-muted">租户数量</p>
                            <p className="text-2xl font-bold text-manus-text">{workspaces.length}</p>
                        </div>
                    </CardContent>
                </Card>
                <Card className="bg-manus-elevated border-manus-border">
                    <CardContent className="p-4 flex items-center gap-3">
                        <div className="w-11 h-11 rounded-xl bg-green-50 text-green-500 flex items-center justify-center">
                            <Database size={20} />
                        </div>
                        <div>
                            <p className="text-sm text-manus-muted">当前端点数</p>
                            <p className="text-2xl font-bold text-manus-text">
                                {form.allowed_endpoints.length}
                            </p>
                        </div>
                    </CardContent>
                </Card>
                <Card className="bg-manus-elevated border-manus-border">
                    <CardContent className="p-4 flex items-center gap-3">
                        <div className="w-11 h-11 rounded-xl bg-purple-50 text-purple-500 flex items-center justify-center">
                            {form.is_enabled ? <CheckCircle2 size={20} /> : <AlertCircle size={20} />}
                        </div>
                        <div>
                            <p className="text-sm text-manus-muted">白名单状态</p>
                            <p
                                className={cn(
                                    'text-2xl font-bold',
                                    form.is_enabled ? 'text-green-600' : 'text-manus-text',
                                )}
                            >
                                {form.is_enabled ? '已启用' : '未启用'}
                            </p>
                        </div>
                    </CardContent>
                </Card>
            </div>

            <Card className="bg-manus-elevated border-manus-border shadow-sm">
                <CardHeader>
                    <CardTitle>租户白名单配置</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    {error && (
                        <div className="p-3 bg-red-50 text-red-700 rounded-lg text-sm">{error}</div>
                    )}
                    {success && (
                        <div className="p-3 bg-green-50 text-green-700 rounded-lg text-sm">{success}</div>
                    )}

                    <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-3 items-end">
                        <div className="space-y-2">
                            <Label htmlFor="workspace-select">选择租户</Label>
                            <select
                                id="workspace-select"
                                value={selectedWorkspaceId}
                                onChange={(event) => setSelectedWorkspaceId(event.target.value)}
                                disabled={isLoadingWorkspaces || isLoadingConfig || isSaving}
                                className="w-full h-10 px-3 rounded-md border border-manus-border bg-manus-secondary text-manus-text"
                            >
                                {isLoadingWorkspaces ? (
                                    <option value="">加载中...</option>
                                ) : workspaces.length === 0 ? (
                                    <option value="">暂无租户</option>
                                ) : (
                                    workspaces.map((workspace) => (
                                        <option key={workspace.id} value={workspace.id}>
                                            {workspace.name} ({workspace.code})
                                        </option>
                                    ))
                                )}
                            </select>
                        </div>
                        <Button
                            variant={form.is_enabled ? 'outline' : 'default'}
                            onClick={() =>
                                setForm((prev) => ({ ...prev, is_enabled: !prev.is_enabled }))
                            }
                            disabled={!selectedWorkspaceId || isLoadingConfig || isSaving}
                        >
                            {form.is_enabled ? '关闭白名单' : '启用白名单'}
                        </Button>
                    </div>

                    <div className="border border-manus-border rounded-lg overflow-hidden">
                        <div className="flex items-center justify-between bg-manus-secondary px-4 py-3 border-b border-manus-border">
                            <div className="text-sm font-semibold text-manus-text">白名单端点</div>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={addEndpoint}
                                disabled={!selectedWorkspaceId || isLoadingConfig || isSaving}
                                className="gap-1"
                            >
                                <Plus size={14} />
                                添加端点
                            </Button>
                        </div>

                        {form.allowed_endpoints.length === 0 ? (
                            <div className="px-5 py-10 text-center text-manus-muted">
                                暂无白名单端点
                            </div>
                        ) : (
                            <table className="w-full">
                                <thead>
                                    <tr className="bg-manus-secondary/50 border-b border-manus-border">
                                        <th className="px-5 py-3 text-left text-sm font-semibold text-manus-text">
                                            主机
                                        </th>
                                        <th className="px-5 py-3 text-left text-sm font-semibold text-manus-text">
                                            端口
                                        </th>
                                        <th className="px-5 py-3 text-center text-sm font-semibold text-manus-text">
                                            操作
                                        </th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {form.allowed_endpoints.map((endpoint, index) => (
                                        <tr key={`${index}-${endpoint.host}-${endpoint.port}`} className="border-b border-manus-border">
                                            <td className="px-5 py-3">
                                                <Input
                                                    value={endpoint.host}
                                                    onChange={(event) =>
                                                        updateEndpoint(index, 'host', event.target.value)
                                                    }
                                                    placeholder="db.example.com 或 10.0.0.12"
                                                    disabled={!selectedWorkspaceId || isLoadingConfig || isSaving}
                                                />
                                            </td>
                                            <td className="px-5 py-3">
                                                <Input
                                                    type="number"
                                                    min={1}
                                                    max={65535}
                                                    value={endpoint.port}
                                                    onChange={(event) =>
                                                        updateEndpoint(index, 'port', event.target.value)
                                                    }
                                                    placeholder="3306"
                                                    disabled={!selectedWorkspaceId || isLoadingConfig || isSaving}
                                                />
                                            </td>
                                            <td className="px-5 py-3 text-center">
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    onClick={() => removeEndpoint(index)}
                                                    disabled={!selectedWorkspaceId || isLoadingConfig || isSaving}
                                                    className="h-8 w-8 text-red-500 hover:text-red-600 hover:bg-red-50"
                                                >
                                                    <Trash2 size={16} />
                                                </Button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="whitelist-note">备注</Label>
                        <textarea
                            id="whitelist-note"
                            value={form.note}
                            onChange={(event) =>
                                setForm((prev) => ({ ...prev, note: event.target.value }))
                            }
                            rows={3}
                            placeholder="例如：仅允许生产只读库接入"
                            disabled={!selectedWorkspaceId || isLoadingConfig || isSaving}
                            className="w-full rounded-md border border-manus-border bg-manus-secondary px-3 py-2 text-sm text-manus-text"
                        />
                    </div>

                    {selectedWorkspace && (
                        <div className="text-xs text-manus-muted">
                            当前租户：{selectedWorkspace.name}（{selectedWorkspace.code}）
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
