import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Database, FileText, Folder, Loader2, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { knowledgeService } from "@/services/knowledgeService";
import { scienceAdminService } from "@/services/scienceAdminService";
import type { FolderNode, DepartmentOption } from "@/types/knowledge";
import type { KnowledgeScope } from "@/types/scienceAdmin";

const EMPTY_SCOPE: KnowledgeScope = {
  file_ids: [],
  visibilities: [],
  dept_ids: [],
};

const VISIBILITY_OPTIONS = [
  { key: "public", label: "公开资料", description: "所有访问范围内用户可见" },
  { key: "dept", label: "部门资料", description: "按部门过滤资料" },
  { key: "private", label: "个人资料", description: "个人上传的资料" },
] as const;
const ALL_VISIBILITY_KEYS = VISIBILITY_OPTIONS.map((item) => item.key);

interface PathItem {
  id: string;
  name: string;
}

function normalizeScope(scope: KnowledgeScope): KnowledgeScope {
  return {
    file_ids: Array.from(new Set(scope.file_ids.filter(Boolean))),
    visibilities: Array.from(new Set(scope.visibilities.filter(Boolean))),
    dept_ids: Array.from(new Set(scope.dept_ids.filter(Boolean))),
  };
}

export default function KnowledgeScopeTab() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [treeLoading, setTreeLoading] = useState(false);
  const [departmentsLoading, setDepartmentsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [savedScope, setSavedScope] = useState<KnowledgeScope>(EMPTY_SCOPE);
  const [draftScope, setDraftScope] = useState<KnowledgeScope>(EMPTY_SCOPE);
  const [departments, setDepartments] = useState<DepartmentOption[]>([]);
  const [tree, setTree] = useState<FolderNode[]>([]);
  const [path, setPath] = useState<PathItem[]>([]);

  const selectedDeptIds = useMemo(
    () => draftScope.dept_ids.map((item) => Number(item)).filter((item) => Number.isFinite(item)),
    [draftScope.dept_ids],
  );
  const effectiveVisibilities = useMemo(
    () => (draftScope.visibilities.length > 0 ? draftScope.visibilities : ALL_VISIBILITY_KEYS),
    [draftScope.visibilities],
  );

  const loadScope = useCallback(async () => {
    setLoading(true);
    try {
      const result = normalizeScope(await scienceAdminService.getKnowledgeScope());
      setSavedScope(result);
      setDraftScope(result);
    } catch (err) {
      toast({
        type: "error",
        title: "加载检索范围失败",
        description: err instanceof Error ? err.message : "未知错误",
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  const loadDepartments = useCallback(async () => {
    setDepartmentsLoading(true);
    try {
      const result = await knowledgeService.fetchDepartments();
      setDepartments(result);
    } catch {
      setDepartments([]);
    } finally {
      setDepartmentsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadScope();
    void loadDepartments();
  }, [loadDepartments, loadScope]);

  const loadVisibleTree = useCallback(async () => {
    if (!pickerOpen) {
      return;
    }
    setTreeLoading(true);
    setLoadError(null);
    try {
      const payload: { visibilities?: string[]; dept_ids?: number[] } = {
        visibilities: effectiveVisibilities,
      };
      if (effectiveVisibilities.includes("dept") && selectedDeptIds.length > 0) {
        payload.dept_ids = selectedDeptIds;
      }
      const result = await knowledgeService.fetchVisibleStructure(payload);
      setTree(Array.isArray(result) ? result : []);
    } catch (err) {
      setTree([]);
      setLoadError(err instanceof Error ? err.message : "加载文件范围失败");
    } finally {
      setTreeLoading(false);
    }
  }, [effectiveVisibilities, pickerOpen, selectedDeptIds]);

  useEffect(() => {
    void loadVisibleTree();
  }, [loadVisibleTree]);

  useEffect(() => {
    if (!pickerOpen) {
      setPath([]);
    }
  }, [pickerOpen]);

  useEffect(() => {
    if (!pickerOpen) return;
    setPath((prev) => {
      let nodes = tree;
      const next: PathItem[] = [];
      for (const item of prev) {
        const folder = nodes.find((node) => node.type === "folder" && node.id === item.id);
        if (!folder) break;
        next.push({ id: folder.id, name: folder.name });
        nodes = Array.isArray(folder.children) ? folder.children : [];
      }
      return next;
    });
  }, [pickerOpen, tree]);

  const currentNodes = useMemo(() => {
    let nodes = tree;
    for (const item of path) {
      const folder = nodes.find((node) => node.type === "folder" && node.id === item.id);
      if (!folder) {
        return [] as FolderNode[];
      }
      nodes = Array.isArray(folder.children) ? folder.children : [];
    }
    return nodes;
  }, [path, tree]);

  const folderNodes = useMemo(
    () => currentNodes.filter((node) => node.type === "folder").sort((a, b) => a.name.localeCompare(b.name, "zh-CN")),
    [currentNodes],
  );
  const fileNodes = useMemo(
    () => currentNodes.filter((node) => node.type === "file").sort((a, b) => a.name.localeCompare(b.name, "zh-CN")),
    [currentNodes],
  );

  const visibleLabels = useMemo(
    () =>
      draftScope.visibilities.length === 0
        ? ["全部资料"]
        : VISIBILITY_OPTIONS.filter((item) => draftScope.visibilities.includes(item.key)).map((item) => item.label),
    [draftScope.visibilities],
  );

  const isDirty = useMemo(() => {
    const left = JSON.stringify(normalizeScope(savedScope));
    const right = JSON.stringify(normalizeScope(draftScope));
    return left !== right;
  }, [draftScope, savedScope]);

  const toggleVisibility = (key: (typeof VISIBILITY_OPTIONS)[number]["key"]) => {
    setDraftScope((prev) => {
      let visibilities: string[];
      if (prev.visibilities.length === 0) {
        visibilities = [key];
      } else if (prev.visibilities.includes(key)) {
        visibilities = prev.visibilities.filter((item) => item !== key);
      } else {
        visibilities = [...prev.visibilities, key];
      }
      return normalizeScope({
        ...prev,
        visibilities,
        dept_ids: visibilities.includes("dept") ? prev.dept_ids : [],
      });
    });
  };

  const toggleDept = (deptId: number) => {
    const deptKey = String(deptId);
    setDraftScope((prev) => {
      const dept_ids = prev.dept_ids.includes(deptKey)
        ? prev.dept_ids.filter((item) => item !== deptKey)
        : [...prev.dept_ids, deptKey];
      return normalizeScope({ ...prev, dept_ids });
    });
  };

  const toggleFile = (fileId: string) => {
    setDraftScope((prev) => {
      const file_ids = prev.file_ids.includes(fileId)
        ? prev.file_ids.filter((item) => item !== fileId)
        : [...prev.file_ids, fileId];
      return normalizeScope({ ...prev, file_ids });
    });
  };

  const enterFolder = (node: FolderNode) => {
    if (node.type !== "folder") return;
    setPath((prev) => [...prev, { id: node.id, name: node.name }]);
  };

  const selectedCurrentFiles = fileNodes.filter((node) => draftScope.file_ids.includes(node.id));
  const allCurrentFilesSelected = fileNodes.length > 0 && selectedCurrentFiles.length === fileNodes.length;

  const toggleCurrentDirectoryFiles = () => {
    setDraftScope((prev) => {
      const next = new Set(prev.file_ids);
      if (allCurrentFilesSelected) {
        fileNodes.forEach((node) => next.delete(node.id));
      } else {
        fileNodes.forEach((node) => next.add(node.id));
      }
      return normalizeScope({ ...prev, file_ids: Array.from(next) });
    });
  };

  const openPicker = () => {
    setDraftScope(savedScope);
    setPickerOpen(true);
  };

  const closePicker = () => {
    setDraftScope(savedScope);
    setPickerOpen(false);
  };

  const handleSave = async (): Promise<boolean> => {
    setSaving(true);
    try {
      const payload = normalizeScope({
        ...draftScope,
        dept_ids: draftScope.visibilities.includes("dept") ? draftScope.dept_ids : [],
      });
      const saved = normalizeScope(await scienceAdminService.updateKnowledgeScope(payload));
      setSavedScope(saved);
      setDraftScope(saved);
      toast({ type: "success", title: "检索范围已更新" });
      return true;
    } catch (err) {
      toast({
        type: "error",
        title: "保存检索范围失败",
        description: err instanceof Error ? err.message : "未知错误",
      });
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAndClose = async () => {
    const saved = await handleSave();
    if (saved) {
      setPickerOpen(false);
    }
  };

  const renderNodeRow = (node: FolderNode) => {
    if (node.type === "folder") {
      return (
        <button
          key={node.id}
          type="button"
          onClick={() => enterFolder(node)}
          className="w-full flex items-center gap-3 rounded-lg border border-transparent px-3 py-2 text-left hover:border-manus-border hover:bg-manus-tertiary transition-colors"
        >
          <Folder className="h-4 w-4 text-manus-muted shrink-0" />
          <span className="flex-1 truncate">{node.name}</span>
          <ChevronRight className="h-4 w-4 text-manus-muted" />
        </button>
      );
    }

    const checked = draftScope.file_ids.includes(node.id);
    return (
      <label
        key={node.id}
        className={cn(
          "flex items-center gap-3 rounded-lg border px-3 py-2 text-sm transition-colors cursor-pointer",
          checked
            ? "border-black bg-black text-white"
            : "border-transparent hover:border-manus-border hover:bg-manus-tertiary",
        )}
      >
        <Checkbox checked={checked} onCheckedChange={() => toggleFile(node.id)} />
        <FileText className="h-4 w-4 text-manus-muted shrink-0" />
        <span className="truncate flex-1">{node.name}</span>
      </label>
    );
  };

  return (
    <>
      <Card className="bg-manus-secondary border-manus-border/60 rounded-xl shadow-lg shadow-black/10">
        <CardHeader className="border-b border-manus-border/50">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <CardTitle>检索范围</CardTitle>
              <p className="text-sm text-manus-muted mt-1">
                这里统一控制科技馆问答的知识检索范围。设备页不再单独设置范围。
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={() => void loadScope()} disabled={saving || loading}>
                <RefreshCw className="h-4 w-4 mr-2" />
                刷新
              </Button>
              <Button onClick={() => void handleSave()} disabled={saving || loading || !isDirty}>
                {saving ? "保存中..." : "保存范围"}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {loading ? (
            <div className="py-8 text-center text-manus-muted">加载检索范围中...</div>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <div className="rounded-lg border border-manus-border bg-manus-tertiary/40 p-4 space-y-3">
                  <Label>当前资料范围</Label>
                  <div className="flex flex-wrap gap-2">
                    {visibleLabels.map((label) => (
                      <Badge key={label} variant="outline" className="border-manus-border text-manus-text">
                        {label}
                      </Badge>
                    ))}
                  </div>
                </div>
                <div className="rounded-lg border border-manus-border bg-manus-tertiary/40 p-4 space-y-3">
                  <Label>部门过滤</Label>
                  <div className="flex flex-wrap gap-2">
                    {savedScope.dept_ids.length === 0 ? (
                      <span className="text-sm text-manus-muted">未限制部门</span>
                    ) : (
                      savedScope.dept_ids.slice(0, 8).map((deptId) => (
                        <Badge key={deptId} variant="outline" className="border-manus-border text-manus-text">
                          {deptId}
                        </Badge>
                      ))
                    )}
                  </div>
                </div>
                <div className="rounded-lg border border-manus-border bg-manus-tertiary/40 p-4 space-y-3">
                  <Label>文件范围</Label>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline" className="border-manus-border text-manus-text">
                      文件 {savedScope.file_ids.length}
                    </Badge>
                    {savedScope.file_ids.slice(0, 3).map((fileId) => (
                      <Badge key={fileId} variant="outline" className="border-manus-border text-manus-muted">
                        {fileId}
                      </Badge>
                    ))}
                    {savedScope.file_ids.length === 0 ? (
                      <span className="text-sm text-manus-muted">未限制具体文件</span>
                    ) : null}
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-manus-border bg-manus-tertiary/30 px-4 py-3">
                <div className="text-sm text-manus-muted">
                  点击「配置范围与文件」后，可以在同一个弹窗里切换资料可见性、部门和文件，不需要退出重来。
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="outline" onClick={() => void loadScope()} disabled={saving}>
                    <RefreshCw className="h-4 w-4 mr-2" />
                    刷新
                  </Button>
                  <Button onClick={openPicker} className="bg-black text-white hover:bg-black/90">
                    <Database className="h-4 w-4 mr-2" />
                    配置范围与文件
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Dialog open={pickerOpen} onOpenChange={(open) => (open ? openPicker() : closePicker())}>
        <DialogContent className="bg-manus-secondary border-manus-border max-w-4xl">
          <DialogHeader>
            <DialogTitle>选择检索文件</DialogTitle>
            <DialogDescription>
              在这个弹窗里可以直接调整资料范围和文件选择，不需要退出后再改。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-3 rounded-lg border border-manus-border bg-manus-tertiary/30 p-4">
              <div className="space-y-2">
                <Label>资料可见性</Label>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => setDraftScope((prev) => normalizeScope({ ...prev, visibilities: [], dept_ids: [] }))}
                    className={cn(
                      "rounded-full border px-3 py-1.5 text-sm font-medium transition-colors",
                      draftScope.visibilities.length === 0
                        ? "border-black bg-black text-white"
                        : "border-manus-border bg-manus-secondary text-manus-text hover:bg-manus-hover",
                    )}
                  >
                    全部资料
                  </button>
                  {VISIBILITY_OPTIONS.map((option) => {
                    const active = draftScope.visibilities.includes(option.key);
                    return (
                      <button
                        key={option.key}
                        type="button"
                        onClick={() => toggleVisibility(option.key)}
                        className={cn(
                          "rounded-full border px-3 py-1.5 text-sm font-medium transition-colors",
                          active
                            ? "border-black bg-black text-white"
                            : "border-manus-border bg-manus-secondary text-manus-text hover:bg-manus-hover",
                        )}
                      >
                        {option.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {(draftScope.visibilities.length === 0 || draftScope.visibilities.includes("dept")) ? (
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <Label>部门过滤</Label>
                    <span className="text-xs text-manus-muted">
                      {departmentsLoading ? "部门加载中..." : `已选 ${draftScope.dept_ids.length} 个部门`}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {departments.map((dept) => {
                      const active = draftScope.dept_ids.includes(String(dept.id));
                      return (
                        <button
                          key={dept.id}
                          type="button"
                          onClick={() => toggleDept(dept.id)}
                          className={cn(
                            "rounded-full border px-3 py-1.5 text-sm transition-colors",
                            active
                              ? "border-black bg-black text-white"
                              : "border-manus-border bg-manus-secondary text-manus-text hover:bg-manus-hover",
                          )}
                        >
                          {dept.name}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </div>

            <div className="flex items-center justify-between gap-3 rounded-lg border border-manus-border bg-manus-tertiary/50 px-4 py-3 text-sm">
              <div className="flex flex-wrap items-center gap-2 min-w-0">
                <span className="text-manus-muted">当前位置：</span>
                <button
                  type="button"
                  onClick={() => setPath([])}
                  className="text-manus-text hover:text-manus-text"
                >
                  根目录
                </button>
                {path.map((item, index) => (
                  <span key={item.id} className="flex items-center gap-2 min-w-0">
                    <ChevronRight className="h-3 w-3 text-manus-muted shrink-0" />
                    <button
                      type="button"
                      onClick={() => setPath((prev) => prev.slice(0, index + 1))}
                      className="truncate text-manus-text hover:text-manus-text"
                    >
                      {item.name}
                    </button>
                  </span>
                ))}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Button variant="outline" size="sm" onClick={toggleCurrentDirectoryFiles} disabled={fileNodes.length === 0}>
                  {allCurrentFilesSelected ? "取消当前目录文件" : "全选当前目录文件"}
                </Button>
                <Button variant="outline" size="sm" onClick={() => setPath((prev) => prev.slice(0, -1))} disabled={path.length === 0}>
                  <ChevronLeft className="h-4 w-4 mr-1" />
                  返回上级
                </Button>
              </div>
            </div>

            {loadError ? <div className="text-sm text-red-400">{loadError}</div> : null}

            <ScrollArea className="h-[420px] rounded-lg border border-manus-border bg-manus-tertiary/30 p-3">
              {treeLoading ? (
                <div className="h-full flex items-center justify-center text-manus-muted">
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  正在加载文件树...
                </div>
              ) : currentNodes.length === 0 ? (
                <div className="h-full flex items-center justify-center text-manus-muted text-sm">
                  当前条件下没有可选文件
                </div>
              ) : (
                <div className="space-y-2">
                  {folderNodes.map(renderNodeRow)}
                  {fileNodes.map(renderNodeRow)}
                </div>
              )}
            </ScrollArea>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={closePicker}>
              取消
            </Button>
            <Button onClick={() => void handleSaveAndClose()} disabled={saving} className="bg-black text-white hover:bg-black/90">
              {saving ? "保存中..." : "保存当前范围"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
