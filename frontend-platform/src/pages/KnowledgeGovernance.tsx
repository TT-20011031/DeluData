import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
  AlertCircle,
  Building2,
  FolderPlus,
  HardDrive,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  Trash2,
  Upload,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import api from '@/lib/api';

type QuotaMode = 'unlimited' | '1gb' | '5gb' | '50gb' | 'custom';
type UploadSizeMode = 'unlimited' | '10mb' | '50mb' | '100mb' | 'custom';

interface GovernanceSummary {
  workspace_id: string;
  workspace_code?: string | null;
  workspace_name?: string | null;
  storage_quota_bytes?: number | null;
  max_upload_file_size_bytes?: number | null;
  storage_used_bytes: number;
  storage_remaining_bytes?: number | null;
  storage_usage_ratio?: number | null;
  file_count: number;
  last_upload_at?: string | null;
  upload_enabled: boolean;
  delete_enabled: boolean;
  rename_enabled: boolean;
  move_enabled: boolean;
  create_folder_enabled: boolean;
}

interface GovernanceDetail extends GovernanceSummary {
  note?: string | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

interface GovernanceFormState {
  upload_enabled: boolean;
  delete_enabled: boolean;
  rename_enabled: boolean;
  move_enabled: boolean;
  create_folder_enabled: boolean;
  quota_mode: QuotaMode;
  custom_quota_gb: string;
  upload_size_mode: UploadSizeMode;
  custom_upload_size_mb: string;
  note: string;
}

const ONE_MB = 1024 ** 2;
const ONE_GB = 1024 ** 3;
const TEN_MB = 10 * ONE_MB;
const FIFTY_MB = 50 * ONE_MB;
const HUNDRED_MB = 100 * ONE_MB;
const FIVE_GB = 5 * ONE_GB;
const FIFTY_GB = 50 * ONE_GB;

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

function formatBytes(bytes?: number | null): string {
  if (bytes == null || bytes <= 0) {
    return '0 B';
  }

  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return unitIndex === 0 ? `${Math.round(value)} ${units[unitIndex]}` : `${value.toFixed(1)} ${units[unitIndex]}`;
}

function formatUsageRatio(value?: number | null): string {
  if (value == null) {
    return '不限额';
  }
  return `${Math.round(value * 100)}%`;
}

function resolveQuotaForm(quotaBytes?: number | null): Pick<GovernanceFormState, 'quota_mode' | 'custom_quota_gb'> {
  if (!quotaBytes || quotaBytes <= 0) {
    return { quota_mode: 'unlimited', custom_quota_gb: '' };
  }
  if (quotaBytes === ONE_GB) {
    return { quota_mode: '1gb', custom_quota_gb: '' };
  }
  if (quotaBytes === FIVE_GB) {
    return { quota_mode: '5gb', custom_quota_gb: '' };
  }
  if (quotaBytes === FIFTY_GB) {
    return { quota_mode: '50gb', custom_quota_gb: '' };
  }
  return {
    quota_mode: 'custom',
    custom_quota_gb: (quotaBytes / ONE_GB).toFixed(2).replace(/\.00$/, ''),
  };
}

function resolveUploadSizeForm(
  maxUploadBytes?: number | null,
): Pick<GovernanceFormState, 'upload_size_mode' | 'custom_upload_size_mb'> {
  if (!maxUploadBytes || maxUploadBytes <= 0) {
    return { upload_size_mode: 'unlimited', custom_upload_size_mb: '' };
  }
  if (maxUploadBytes === TEN_MB) {
    return { upload_size_mode: '10mb', custom_upload_size_mb: '' };
  }
  if (maxUploadBytes === FIFTY_MB) {
    return { upload_size_mode: '50mb', custom_upload_size_mb: '' };
  }
  if (maxUploadBytes === HUNDRED_MB) {
    return { upload_size_mode: '100mb', custom_upload_size_mb: '' };
  }
  return {
    upload_size_mode: 'custom',
    custom_upload_size_mb: (maxUploadBytes / ONE_MB).toFixed(2).replace(/\.00$/, ''),
  };
}

function normalizeForm(detail: GovernanceDetail): GovernanceFormState {
  const quota = resolveQuotaForm(detail.storage_quota_bytes);
  const uploadSize = resolveUploadSizeForm(detail.max_upload_file_size_bytes);
  return {
    upload_enabled: Boolean(detail.upload_enabled),
    delete_enabled: Boolean(detail.delete_enabled),
    rename_enabled: Boolean(detail.rename_enabled),
    move_enabled: Boolean(detail.move_enabled),
    create_folder_enabled: Boolean(detail.create_folder_enabled),
    quota_mode: quota.quota_mode,
    custom_quota_gb: quota.custom_quota_gb,
    upload_size_mode: uploadSize.upload_size_mode,
    custom_upload_size_mb: uploadSize.custom_upload_size_mb,
    note: detail.note || '',
  };
}

function buildQuotaBytes(form: GovernanceFormState): number | null {
  if (form.quota_mode === 'unlimited') {
    return null;
  }
  if (form.quota_mode === '1gb') {
    return ONE_GB;
  }
  if (form.quota_mode === '5gb') {
    return FIVE_GB;
  }
  if (form.quota_mode === '50gb') {
    return FIFTY_GB;
  }

  const parsed = Number(form.custom_quota_gb);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error('自定义额度必须大于 0 GB');
  }
  return Math.round(parsed * ONE_GB);
}

function buildMaxUploadFileSizeBytes(form: GovernanceFormState): number | null {
  if (form.upload_size_mode === 'unlimited') {
    return null;
  }
  if (form.upload_size_mode === '10mb') {
    return TEN_MB;
  }
  if (form.upload_size_mode === '50mb') {
    return FIFTY_MB;
  }
  if (form.upload_size_mode === '100mb') {
    return HUNDRED_MB;
  }

  const parsed = Number(form.custom_upload_size_mb);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error('自定义单文件上传上限必须大于 0 MB');
  }
  return Math.round(parsed * ONE_MB);
}

function PermissionToggle({
  label,
  description,
  enabled,
  onToggle,
  disabled = false,
  icon,
}: {
  label: string;
  description: string;
  enabled: boolean;
  onToggle: () => void;
  disabled?: boolean;
  icon: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between rounded-xl border border-manus-border bg-manus-secondary px-4 py-3 gap-4">
      <div className="flex items-center gap-3 min-w-0">
        <div className="w-10 h-10 rounded-xl bg-zinc-100 text-zinc-700 flex items-center justify-center shrink-0">
          {icon}
        </div>
        <div className="min-w-0">
          <div className="font-medium text-manus-text">{label}</div>
          <div className="text-sm text-manus-muted">{description}</div>
        </div>
      </div>
      <Button
        type="button"
        variant={enabled ? 'default' : 'outline'}
        size="sm"
        onClick={onToggle}
        disabled={disabled}
        className="shrink-0 min-w-[84px]"
      >
        {enabled ? '已开启' : '已关闭'}
      </Button>
    </div>
  );
}

export default function KnowledgeGovernancePage() {
  const [summaries, setSummaries] = useState<GovernanceSummary[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState('');
  const [detail, setDetail] = useState<GovernanceDetail | null>(null);
  const [form, setForm] = useState<GovernanceFormState | null>(null);
  const [search, setSearch] = useState('');
  const [isLoadingList, setIsLoadingList] = useState(true);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const detailRequestSeqRef = useRef(0);
  const selectedWorkspaceIdRef = useRef(selectedWorkspaceId);

  useEffect(() => {
    selectedWorkspaceIdRef.current = selectedWorkspaceId;
  }, [selectedWorkspaceId]);

  const selectedSummary = useMemo(
    () => summaries.find((item) => item.workspace_id === selectedWorkspaceId) || null,
    [selectedWorkspaceId, summaries],
  );

  const filteredSummaries = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) {
      return summaries;
    }
    return summaries.filter((item) => {
      return (
        (item.workspace_name || '').toLowerCase().includes(keyword) ||
        (item.workspace_code || '').toLowerCase().includes(keyword)
      );
    });
  }, [search, summaries]);

  const totalUsedBytes = useMemo(
    () => summaries.reduce((sum, item) => sum + (item.storage_used_bytes || 0), 0),
    [summaries],
  );

  const limitedCount = useMemo(
    () =>
      summaries.filter(
        (item) => Boolean(item.storage_quota_bytes) || Boolean(item.max_upload_file_size_bytes),
      ).length,
    [summaries],
  );

  const governedCount = useMemo(
    () =>
      summaries.filter(
        (item) =>
          Boolean(item.storage_quota_bytes) ||
          Boolean(item.max_upload_file_size_bytes) ||
          !item.upload_enabled ||
          !item.delete_enabled ||
          !item.rename_enabled ||
          !item.move_enabled ||
          !item.create_folder_enabled,
      ).length,
    [summaries],
  );

  const fetchSummaries = useCallback(async () => {
    setIsLoadingList(true);
    setError('');
    try {
      const response = await api.get<GovernanceSummary[]>('/knowledge-governance/workspaces', {
        params: { skip: 0, limit: 200 },
      });
      const nextSummaries = response.data || [];
      setSummaries(nextSummaries);
      if (nextSummaries.length > 0 && !selectedWorkspaceId) {
        setSelectedWorkspaceId(nextSummaries[0].workspace_id);
      }
      if (
        nextSummaries.length > 0 &&
        selectedWorkspaceId &&
        !nextSummaries.some((item) => item.workspace_id === selectedWorkspaceId)
      ) {
        setSelectedWorkspaceId(nextSummaries[0].workspace_id);
      }
    } catch (errorUnknown) {
      setError(extractErrorMessage(errorUnknown, '加载知识库治理列表失败'));
    } finally {
      setIsLoadingList(false);
    }
  }, [selectedWorkspaceId]);

  const fetchDetail = useCallback(async (workspaceId: string) => {
    if (!workspaceId) {
      setDetail(null);
      setForm(null);
      return;
    }

    const requestSeq = ++detailRequestSeqRef.current;
    setIsLoadingDetail(true);
    setError('');
    setSuccess('');
    setDetail(null);
    setForm(null);
    try {
      const response = await api.get<GovernanceDetail>(
        `/workspaces/${workspaceId}/knowledge-governance`,
      );
      if (requestSeq !== detailRequestSeqRef.current) {
        return;
      }
      setDetail(response.data);
      setForm(normalizeForm(response.data));
    } catch (errorUnknown) {
      if (requestSeq !== detailRequestSeqRef.current) {
        return;
      }
      setError(extractErrorMessage(errorUnknown, '加载治理配置失败'));
    } finally {
      if (requestSeq === detailRequestSeqRef.current) {
        setIsLoadingDetail(false);
      }
    }
  }, []);

  useEffect(() => {
    fetchSummaries();
  }, [fetchSummaries]);

  useEffect(() => {
    if (selectedWorkspaceId) {
      fetchDetail(selectedWorkspaceId);
    }
  }, [fetchDetail, selectedWorkspaceId]);

  const handleSave = async () => {
    if (!selectedWorkspaceId || !form) return;

    const workspaceId = selectedWorkspaceId;

    setIsSaving(true);
    setError('');
    setSuccess('');

    try {
      const payload = {
        upload_enabled: form.upload_enabled,
        delete_enabled: form.delete_enabled,
        rename_enabled: form.rename_enabled,
        move_enabled: form.move_enabled,
        create_folder_enabled: form.create_folder_enabled,
        storage_quota_bytes: buildQuotaBytes(form),
        max_upload_file_size_bytes: buildMaxUploadFileSizeBytes(form),
        note: form.note.trim() || null,
      };
      const response = await api.put<GovernanceDetail>(
        `/workspaces/${workspaceId}/knowledge-governance`,
        payload,
      );
      if (selectedWorkspaceIdRef.current !== workspaceId) {
        await fetchSummaries();
        return;
      }
      setDetail(response.data);
      setForm(normalizeForm(response.data));
      setSuccess('知识库治理配置保存成功');
      await fetchSummaries();
    } catch (errorUnknown) {
      setError(extractErrorMessage(errorUnknown, '保存治理配置失败'));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="animate-fade-in space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-manus-text mb-1 flex items-center gap-2">
            <HardDrive size={24} />
            知识库治理
          </h1>
          <p className="text-manus-muted">
            平台端统一控制租户的知识库额度、单文件上传上限，以及上传、删除、重命名、移动等动作权限。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => {
              void fetchSummaries();
              if (selectedWorkspaceId) {
                void fetchDetail(selectedWorkspaceId);
              }
            }}
            disabled={isLoadingList || isLoadingDetail || isSaving}
            className="gap-2"
          >
            <RefreshCw size={16} className={isLoadingList || isLoadingDetail ? 'animate-spin' : ''} />
            刷新
          </Button>
          <Button
            onClick={handleSave}
            disabled={!selectedWorkspaceId || !form || isLoadingDetail || isSaving}
            className="gap-2"
          >
            {isSaving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
            保存配置
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-manus-elevated border-manus-border">
          <CardContent className="p-4 flex items-center gap-3">
            <div className="w-11 h-11 rounded-xl bg-blue-50 text-blue-500 flex items-center justify-center">
              <Building2 size={20} />
            </div>
            <div>
              <p className="text-sm text-manus-muted">租户数量</p>
              <p className="text-2xl font-bold text-manus-text">{summaries.length}</p>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-manus-elevated border-manus-border">
          <CardContent className="p-4 flex items-center gap-3">
            <div className="w-11 h-11 rounded-xl bg-emerald-50 text-emerald-500 flex items-center justify-center">
              <HardDrive size={20} />
            </div>
            <div>
              <p className="text-sm text-manus-muted">总已用空间</p>
              <p className="text-2xl font-bold text-manus-text">{formatBytes(totalUsedBytes)}</p>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-manus-elevated border-manus-border">
          <CardContent className="p-4 flex items-center gap-3">
            <div className="w-11 h-11 rounded-xl bg-amber-50 text-amber-500 flex items-center justify-center">
              <AlertCircle size={20} />
            </div>
            <div>
              <p className="text-sm text-manus-muted">受控租户</p>
              <p className="text-2xl font-bold text-manus-text">
                {governedCount}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {(error || success) && (
        <div className="space-y-2">
          {error && <div className="p-3 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>}
          {success && <div className="p-3 rounded-lg bg-green-50 text-green-700 text-sm">{success}</div>}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[1.25fr_1fr] gap-6">
        <Card className="bg-manus-elevated border-manus-border shadow-sm">
          <CardHeader className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <CardTitle>租户空间总览</CardTitle>
              <div className="text-sm text-manus-muted">限额租户 {limitedCount} 个</div>
            </div>
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索租户名称或代码"
              className="bg-manus-secondary"
            />
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-y border-manus-border bg-manus-secondary/70">
                    <th className="px-4 py-3 text-left text-sm font-semibold text-manus-text">租户</th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-manus-text">已用 / 总额</th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-manus-text">使用率</th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-manus-text">权限状态</th>
                  </tr>
                </thead>
                <tbody>
                  {isLoadingList ? (
                    <tr>
                      <td colSpan={4} className="px-4 py-16 text-center text-manus-muted">
                        <Loader2 size={20} className="animate-spin inline-block mr-2" />
                        正在加载租户治理数据
                      </td>
                    </tr>
                  ) : filteredSummaries.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-4 py-16 text-center text-manus-muted">
                        暂无可显示的租户
                      </td>
                    </tr>
                  ) : (
                    filteredSummaries.map((item) => {
                      const isSelected = item.workspace_id === selectedWorkspaceId;
                      const hasRestriction =
                        Boolean(item.storage_quota_bytes) ||
                        Boolean(item.max_upload_file_size_bytes) ||
                        !item.upload_enabled ||
                        !item.delete_enabled ||
                        !item.rename_enabled ||
                        !item.move_enabled ||
                        !item.create_folder_enabled;
                      return (
                        <tr
                          key={item.workspace_id}
                          onClick={() => setSelectedWorkspaceId(item.workspace_id)}
                          className={cn(
                            'border-b border-manus-border cursor-pointer transition-colors',
                            isSelected ? 'bg-blue-50/70' : 'hover:bg-manus-secondary/50',
                          )}
                        >
                          <td className="px-4 py-4 align-top">
                            <div className="font-medium text-manus-text">{item.workspace_name || '未命名租户'}</div>
                            <div className="text-sm text-manus-muted">{item.workspace_code || item.workspace_id}</div>
                            <div className="text-xs text-manus-muted mt-1">{item.file_count} 个文件</div>
                            {item.max_upload_file_size_bytes ? (
                              <div className="text-xs text-manus-muted mt-1">
                                单文件上限 {formatBytes(item.max_upload_file_size_bytes)}
                              </div>
                            ) : null}
                          </td>
                          <td className="px-4 py-4 align-top">
                            <div className="text-manus-text">
                              {formatBytes(item.storage_used_bytes)} /{' '}
                              {item.storage_quota_bytes ? formatBytes(item.storage_quota_bytes) : '不限额'}
                            </div>
                            <div className="text-xs text-manus-muted mt-1">
                              剩余 {item.storage_remaining_bytes == null ? '不限额' : formatBytes(item.storage_remaining_bytes)}
                            </div>
                          </td>
                          <td className="px-4 py-4 align-top">
                            <div className="text-manus-text">{formatUsageRatio(item.storage_usage_ratio)}</div>
                            <div className="mt-2 h-2 rounded-full bg-zinc-100 overflow-hidden">
                              <div
                                className={cn(
                                  'h-full transition-all',
                                  (item.storage_usage_ratio || 0) >= 0.95
                                    ? 'bg-red-500'
                                    : (item.storage_usage_ratio || 0) >= 0.8
                                      ? 'bg-amber-500'
                                      : 'bg-emerald-500',
                                )}
                                style={{ width: `${Math.round((item.storage_usage_ratio || 0) * 100)}%` }}
                              />
                            </div>
                          </td>
                          <td className="px-4 py-4 align-top">
                            <div className="flex flex-wrap gap-2">
                              <span className={cn(
                                'px-2 py-1 rounded-full text-xs font-medium',
                                item.upload_enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-zinc-100 text-zinc-500',
                              )}>
                                上传
                              </span>
                              <span className={cn(
                                'px-2 py-1 rounded-full text-xs font-medium',
                                item.delete_enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-zinc-100 text-zinc-500',
                              )}>
                                删除
                              </span>
                              <span className={cn(
                                'px-2 py-1 rounded-full text-xs font-medium',
                                hasRestriction ? 'bg-amber-100 text-amber-700' : 'bg-blue-100 text-blue-700',
                              )}>
                                {hasRestriction ? '受限' : '标准'}
                              </span>
                            </div>
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

        <Card className="bg-manus-elevated border-manus-border shadow-sm">
          <CardHeader>
            <CardTitle>治理配置</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {!selectedWorkspaceId ? (
              <div className="text-manus-muted text-sm">请选择左侧租户进行配置</div>
            ) : isLoadingDetail || !detail || !form ? (
              <div className="py-16 text-center text-manus-muted">
                <Loader2 size={20} className="animate-spin inline-block mr-2" />
                正在加载治理详情
              </div>
            ) : (
              <>
                <div className="rounded-xl border border-manus-border bg-manus-secondary p-4 space-y-3">
                  <div>
                    <div className="text-lg font-semibold text-manus-text">
                      {detail.workspace_name || selectedSummary?.workspace_name || '未命名租户'}
                    </div>
                    <div className="text-sm text-manus-muted">
                      {detail.workspace_code || selectedSummary?.workspace_code || detail.workspace_id}
                    </div>
                  </div>
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div>
                        <div className="text-manus-muted">当前已用</div>
                        <div className="font-medium text-manus-text">{formatBytes(detail.storage_used_bytes)}</div>
                      </div>
                    <div>
                      <div className="text-manus-muted">总额度</div>
                        <div className="font-medium text-manus-text">
                          {detail.storage_quota_bytes ? formatBytes(detail.storage_quota_bytes) : '不限额'}
                        </div>
                      </div>
                      <div>
                        <div className="text-manus-muted">单文件上限</div>
                        <div className="font-medium text-manus-text">
                          {detail.max_upload_file_size_bytes
                            ? formatBytes(detail.max_upload_file_size_bytes)
                            : '不限额'}
                        </div>
                      </div>
                      <div>
                        <div className="text-manus-muted">文件数量</div>
                        <div className="font-medium text-manus-text">{detail.file_count}</div>
                      </div>
                    </div>
                  <div>
                    <div className="flex items-center justify-between text-xs text-manus-muted mb-2">
                      <span>使用率</span>
                      <span>{formatUsageRatio(detail.storage_usage_ratio)}</span>
                    </div>
                    <div className="h-2 rounded-full bg-zinc-100 overflow-hidden">
                      <div
                        className="h-full bg-blue-500 transition-all"
                        style={{ width: `${Math.round((detail.storage_usage_ratio || 0) * 100)}%` }}
                      />
                    </div>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>空间额度</Label>
                  <select
                    value={form.quota_mode}
                    onChange={(event) =>
                      setForm((prev) => prev ? { ...prev, quota_mode: event.target.value as QuotaMode } : prev)
                    }
                    disabled={isSaving}
                    className="w-full h-10 px-3 rounded-md border border-manus-border bg-manus-secondary text-manus-text"
                  >
                    <option value="unlimited">不限额</option>
                    <option value="1gb">1 GB</option>
                    <option value="5gb">5 GB</option>
                    <option value="50gb">50 GB</option>
                    <option value="custom">自定义</option>
                  </select>
                  {form.quota_mode === 'custom' && (
                    <Input
                      type="number"
                      min={0.1}
                      step={0.1}
                      value={form.custom_quota_gb}
                      onChange={(event) =>
                        setForm((prev) => prev ? { ...prev, custom_quota_gb: event.target.value } : prev)
                      }
                      placeholder="请输入自定义额度（GB）"
                      disabled={isSaving}
                    />
                  )}
                </div>

                <div className="space-y-2">
                  <Label>单文件上传上限</Label>
                  <select
                    value={form.upload_size_mode}
                    onChange={(event) =>
                      setForm((prev) =>
                        prev ? { ...prev, upload_size_mode: event.target.value as UploadSizeMode } : prev,
                      )
                    }
                    disabled={isSaving}
                    className="w-full h-10 px-3 rounded-md border border-manus-border bg-manus-secondary text-manus-text"
                  >
                    <option value="unlimited">不限额</option>
                    <option value="10mb">10 MB</option>
                    <option value="50mb">50 MB</option>
                    <option value="100mb">100 MB</option>
                    <option value="custom">自定义</option>
                  </select>
                  {form.upload_size_mode === 'custom' && (
                    <Input
                      type="number"
                      min={1}
                      step={1}
                      value={form.custom_upload_size_mb}
                      onChange={(event) =>
                        setForm((prev) =>
                          prev ? { ...prev, custom_upload_size_mb: event.target.value } : prev,
                        )
                      }
                      placeholder="请输入自定义上限（MB）"
                      disabled={isSaving}
                    />
                  )}
                  <div className="text-xs text-manus-muted">
                    超过上限的文件将被前端拦截，后端也会再次校验。
                  </div>
                </div>

                <div className="space-y-3">
                  <PermissionToggle
                    label="允许上传"
                    description="控制租户是否可以向知识库新增文件。"
                    enabled={form.upload_enabled}
                    onToggle={() =>
                      setForm((prev) => prev ? { ...prev, upload_enabled: !prev.upload_enabled } : prev)
                    }
                    disabled={isSaving}
                    icon={<Upload size={18} />}
                  />
                  <PermissionToggle
                    label="允许删除"
                    description="控制单删、批量删除以及文件夹删除。"
                    enabled={form.delete_enabled}
                    onToggle={() =>
                      setForm((prev) => prev ? { ...prev, delete_enabled: !prev.delete_enabled } : prev)
                    }
                    disabled={isSaving}
                    icon={<Trash2 size={18} />}
                  />
                  <PermissionToggle
                    label="允许重命名"
                    description="控制文件与文件夹的重命名。"
                    enabled={form.rename_enabled}
                    onToggle={() =>
                      setForm((prev) => prev ? { ...prev, rename_enabled: !prev.rename_enabled } : prev)
                    }
                    disabled={isSaving}
                    icon={<Pencil size={18} />}
                  />
                  <PermissionToggle
                    label="允许移动"
                    description="控制拖拽移动与目录迁移。"
                    enabled={form.move_enabled}
                    onToggle={() =>
                      setForm((prev) => prev ? { ...prev, move_enabled: !prev.move_enabled } : prev)
                    }
                    disabled={isSaving}
                    icon={<HardDrive size={18} />}
                  />
                  <PermissionToggle
                    label="允许新建文件夹"
                    description="控制新建根目录和子文件夹。"
                    enabled={form.create_folder_enabled}
                    onToggle={() =>
                      setForm((prev) => prev ? { ...prev, create_folder_enabled: !prev.create_folder_enabled } : prev)
                    }
                    disabled={isSaving}
                    icon={<FolderPlus size={18} />}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="governance-note">备注</Label>
                  <textarea
                    id="governance-note"
                    value={form.note}
                    onChange={(event) =>
                      setForm((prev) => prev ? { ...prev, note: event.target.value } : prev)
                    }
                    disabled={isSaving}
                    rows={4}
                    className="w-full rounded-md border border-manus-border bg-manus-secondary text-manus-text px-3 py-2 text-sm resize-y"
                    placeholder="可记录这是演示租户、试用租户或运营备注"
                  />
                  <div className="text-xs text-manus-muted">
                    {detail.updated_at
                      ? `最近更新：${new Date(detail.updated_at).toLocaleString('zh-CN')}`
                      : '尚未保存过治理配置'}
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
