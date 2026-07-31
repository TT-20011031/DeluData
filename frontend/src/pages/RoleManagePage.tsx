/**
 * 角色权限管理页面
 * 
 * 角色CRUD、权限矩阵配置、数据范围设置
 */
import { useState, useEffect, useCallback } from 'react'
import { Navigate } from 'react-router-dom'
import { Shield, Plus, Trash2, Edit2, Loader2, RefreshCw, Check, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { getAuthHeader, useAuthStore } from '@/stores/authStore'
import { cn } from '@/lib/utils'
import { ConfirmDialog } from '@/components/ConfirmDialog'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

const FORCED_SYSTEM_ROLE_PERMISSIONS: Record<string, string[]> = {
    Member: ['database:query'],
}

function getForcedPermissions(role: { name: string; is_system?: boolean }) {
    return role.is_system ? (FORCED_SYSTEM_ROLE_PERMISSIONS[role.name] || []) : []
}

function isForcedPermission(permCode: string, role: { name: string; is_system?: boolean }) {
    return getForcedPermissions(role).includes(permCode)
}

function withForcedPermissions<T extends { name: string; is_system?: boolean; permissions: string[] }>(role: T): T {
    const forced = getForcedPermissions(role)
    if (forced.length === 0) return role
    return {
        ...role,
        permissions: Array.from(new Set([...role.permissions, ...forced])),
    }
}

// 数据范围选项
const DATA_SCOPE_OPTIONS = [
    { value: 1, label: '全部数据', desc: '可查看所有数据' },
    { value: 2, label: '本部门及下属', desc: '可查看本部门及下级部门数据' },
    { value: 3, label: '本部门', desc: '仅可查看本部门数据' },
    { value: 4, label: '仅本人', desc: '仅可查看自己的数据' },
]

interface Role {
    id: number
    name: string
    description: string | null
    workspace_id: string | null
    is_system: boolean
    permissions: string[]
    data_scope: number
}

interface Permission {
    code: string
    module: string
    description: string
}

interface PermissionGroup {
    [module: string]: Permission[]
}

export default function RoleManagePage() {
    const { user: currentUser } = useAuthStore()
    const [roles, setRoles] = useState<Role[]>([])
    const [permissions, setPermissions] = useState<PermissionGroup>({})
    const [isLoading, setIsLoading] = useState(true)
    const [selectedRole, setSelectedRole] = useState<Role | null>(null)

    // 对话框状态
    const [showCreateDialog, setShowCreateDialog] = useState(false)
    const [showEditDialog, setShowEditDialog] = useState(false)
    const [newRole, setNewRole] = useState({ name: '', description: '', data_scope: 4, permissions: [] as string[] })
    const [editRole, setEditRole] = useState<Role | null>(null)
    const [isSubmitting, setIsSubmitting] = useState(false)

    // 权限检查
    if (!currentUser?.permissions?.some((code) => code === '*' || code === 'role:manage')) {
        return <Navigate to="/" replace />
    }

    // 加载角色列表
    const loadRoles = useCallback(async () => {
        setIsLoading(true)
        try {
            const response = await fetch(`${API_BASE_URL}/rbac/roles`, {
                headers: getAuthHeader(),
            })
            if (response.ok) {
                const data = await response.json()
                setRoles((data.roles || []).map(withForcedPermissions))
            }
        } catch (error) {
            console.error('加载角色失败:', error)
        }
        setIsLoading(false)
    }, [])

    // 加载权限列表
    const loadPermissions = useCallback(async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/rbac/permissions/grouped`, {
                headers: getAuthHeader(),
            })
            if (response.ok) {
                const data = await response.json()
                setPermissions(data)
            }
        } catch (error) {
            console.error('加载权限失败:', error)
        }
    }, [])

    useEffect(() => {
        loadRoles()
        loadPermissions()
    }, [loadRoles, loadPermissions])

    // 创建角色
    const handleCreateRole = async () => {
        if (!newRole.name) return
        setIsSubmitting(true)
        try {
            const response = await fetch(`${API_BASE_URL}/rbac/roles`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify(newRole),
            })
            if (response.ok) {
                setShowCreateDialog(false)
                setNewRole({ name: '', description: '', data_scope: 4, permissions: [] })
                loadRoles()
            }
        } catch (error) {
            console.error('创建角色失败:', error)
        }
        setIsSubmitting(false)
    }

    // 更新角色
    const handleUpdateRole = async () => {
        if (!editRole) return
        setIsSubmitting(true)
        try {
            const response = await fetch(`${API_BASE_URL}/rbac/roles/${editRole.id}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify({
                    name: editRole.name,
                    description: editRole.description,
                    data_scope: editRole.data_scope,
                    permissions: withForcedPermissions(editRole).permissions,
                }),
            })
            if (response.ok) {
                setShowEditDialog(false)
                setEditRole(null)
                loadRoles()
            }
        } catch (error) {
            console.error('更新角色失败:', error)
        }
        setIsSubmitting(false)
    }

    // 删除确认状态
    const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
    const [roleToDeleteId, setRoleToDeleteId] = useState<number | null>(null)
    const [isDeleting, setIsDeleting] = useState(false)

    // 点击删除按钮
    const handleDeleteRole = (roleId: number) => {
        setRoleToDeleteId(roleId)
        setConfirmDeleteOpen(true)
    }

    // 确认删除
    const handleConfirmDelete = async () => {
        if (!roleToDeleteId) return

        setIsDeleting(true)
        try {
            const response = await fetch(`${API_BASE_URL}/rbac/roles/${roleToDeleteId}`, {
                method: 'DELETE',
                headers: getAuthHeader(),
            })
            if (response.ok) {
                loadRoles()
                setConfirmDeleteOpen(false)
                setRoleToDeleteId(null)
            }
        } catch (error) {
            console.error('删除角色失败:', error)
        } finally {
            setIsDeleting(false)
        }
    }

    // 切换权限
    const togglePermission = (
        permCode: string,
        role: { name?: string; is_system?: boolean; permissions: string[] },
        setRole: (r: any) => void,
    ) => {
        if (role.name && isForcedPermission(permCode, { name: role.name, is_system: role.is_system })) {
            return
        }
        const newPerms = role.permissions.includes(permCode)
            ? role.permissions.filter(p => p !== permCode)
            : [...role.permissions, permCode]
        setRole(role.name ? withForcedPermissions({ ...role, name: role.name, permissions: newPerms }) : { ...role, permissions: newPerms })
    }

    if (isLoading) {
        return (
            <div className="flex-1 flex items-center justify-center bg-manus">
                <Loader2 className="h-8 w-8 animate-spin text-accent" />
            </div>
        )
    }

    return (
        <div className="flex-1 flex flex-col h-full bg-manus p-6">
            <div className="max-w-6xl mx-auto w-full space-y-6">
                {/* 标题 */}
                <div className="flex items-center justify-between">
                    <div>
                        <h1 className="text-2xl font-semibold text-manus-text flex items-center gap-2">
                            <Shield className="h-6 w-6 text-accent" />
                            角色权限管理
                        </h1>
                        <p className="text-manus-muted mt-1">配置角色的功能权限和数据范围</p>
                    </div>
                    <div className="flex gap-2">
                        <Button variant="outline" onClick={loadRoles} className="bg-manus-tertiary border-manus-border">
                            <RefreshCw className="h-4 w-4 mr-2" />
                            刷新
                        </Button>
                        <Button onClick={() => setShowCreateDialog(true)} className="bg-accent hover:bg-accent/90 text-white transition-all active:scale-95">
                            <Plus className="h-4 w-4 mr-2" />
                            新建角色
                        </Button>
                    </div>
                </div>

                {/* 角色列表 */}
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {roles.map(role => (
                        <Card key={role.id} className={cn(
                            "bg-manus-secondary border-manus-border hover:border-accent/30 cursor-pointer transition-all",
                            selectedRole?.id === role.id && "ring-2 ring-accent/50"
                        )} onClick={() => setSelectedRole(role)}>
                            <CardHeader className="pb-2">
                                <div className="flex items-center justify-between">
                                    <CardTitle className="text-manus-text text-lg flex items-center gap-2">
                                        <Shield className="h-5 w-5 text-accent" />
                                        {role.name}
                                    </CardTitle>
                                    {role.is_system ? (
                                        <span className="text-xs bg-accent/20 text-accent px-2 py-1 rounded">系统</span>
                                    ) : (
                                        <div className="flex gap-1">
                                            <Button variant="ghost" size="sm" onClick={(e) => {
                                                e.stopPropagation()
                                                setEditRole(withForcedPermissions(role))
                                                setShowEditDialog(true)
                                            }} className="hover:bg-blue-600 hover:text-white">
                                                <Edit2 className="h-4 w-4" />
                                            </Button>
                                            <Button variant="ghost" size="sm" onClick={(e) => {
                                                e.stopPropagation()
                                                handleDeleteRole(role.id)
                                            }} className="hover:bg-red-600 hover:text-white text-red-400">
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    )}
                                </div>
                            </CardHeader>
                            <CardContent>
                                <p className="text-sm text-manus-muted mb-3">{role.description || '暂无描述'}</p>
                                <div className="flex flex-wrap gap-1 mb-2">
                                    <span className="text-xs bg-blue-500/20 text-blue-400 px-2 py-0.5 rounded">
                                        {DATA_SCOPE_OPTIONS.find(o => o.value === role.data_scope)?.label || '仅本人'}
                                    </span>
                                    <span className="text-xs bg-green-500/20 text-green-400 px-2 py-0.5 rounded">
                                        {role.permissions.length} 项权限
                                    </span>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>

                {/* 选中角色的权限详情 */}
                {selectedRole && (
                    <Card className="bg-manus-secondary border-manus-border">
                        <CardHeader>
                            <CardTitle className="text-manus-text">
                                {selectedRole.name} - 权限详情
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                {Object.entries(permissions).map(([module, perms]) => (
                                    <div key={module} className="p-3 rounded-lg bg-manus-tertiary">
                                        <h4 className="font-medium text-manus-text mb-2">{module}</h4>
                                        <div className="space-y-1">
                                            {perms.map((perm: Permission) => (
                                                <div key={perm.code} className="flex items-center gap-2 text-sm">
                                                    {selectedRole.permissions.includes(perm.code) ? (
                                                        <Check className="h-4 w-4 text-green-400" />
                                                    ) : (
                                                        <X className="h-4 w-4 text-manus-muted" />
                                                    )}
                                                    <span className={cn(
                                                        selectedRole.permissions.includes(perm.code) ? "text-manus-text" : "text-manus-muted"
                                                    )}>
                                                        {perm.description}
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </CardContent>
                    </Card>
                )}
            </div>

            {/* 新建角色对话框 */}
            <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
                <DialogContent className="bg-manus-secondary border-manus-border max-w-2xl">
                    <DialogHeader>
                        <DialogTitle className="text-manus-text">新建角色</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-4 py-4">
                        <div className="grid grid-cols-2 gap-4">
                            <Input
                                placeholder="角色名称"
                                value={newRole.name}
                                onChange={(e) => setNewRole({ ...newRole, name: e.target.value })}
                                className="bg-manus-tertiary border-manus-border"
                            />
                            <select
                                value={newRole.data_scope}
                                onChange={(e) => setNewRole({ ...newRole, data_scope: parseInt(e.target.value) })}
                                className="p-2 rounded bg-manus-tertiary border border-manus-border text-manus-text"
                            >
                                {DATA_SCOPE_OPTIONS.map(opt => (
                                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                                ))}
                            </select>
                        </div>
                        <Input
                            placeholder="角色描述"
                            value={newRole.description}
                            onChange={(e) => setNewRole({ ...newRole, description: e.target.value })}
                            className="bg-manus-tertiary border-manus-border"
                        />
                        <div className="border border-manus-border rounded-lg p-4">
                            <div className="flex items-center justify-between mb-3">
                                <h4 className="font-medium text-manus-text">权限配置</h4>
                                <div className="flex gap-2">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        onClick={() => {
                                            const allPerms = Object.values(permissions).flat().map((p: Permission) => p.code)
                                            setNewRole({ ...newRole, permissions: allPerms })
                                        }}
                                        className="text-xs h-7"
                                    >
                                        全选
                                    </Button>
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        onClick={() => setNewRole({ ...newRole, permissions: [] })}
                                        className="text-xs h-7"
                                    >
                                        取消全选
                                    </Button>
                                </div>
                            </div>
                            <ScrollArea className="h-[200px]">
                                <div className="grid grid-cols-2 gap-4">
                                    {Object.entries(permissions).map(([module, perms]) => (
                                        <div key={module}>
                                            <h5 className="text-sm font-medium text-accent mb-2">{module}</h5>
                                            <div className="space-y-1">
                                                {perms.map((perm: Permission) => (
                                                    <label key={perm.code} className="flex items-center gap-2 text-sm cursor-pointer">
                                                        <input
                                                            type="checkbox"
                                                            checked={newRole.permissions.includes(perm.code)}
                                                            onChange={() => togglePermission(perm.code, newRole, setNewRole)}
                                                            className="rounded"
                                                        />
                                                        <span className="text-manus-text">{perm.description}</span>
                                                    </label>
                                                ))}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </ScrollArea>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowCreateDialog(false)}>取消</Button>
                        <Button onClick={handleCreateRole} disabled={isSubmitting} className="bg-accent hover:bg-accent/90 text-white">
                            {isSubmitting && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                            创建
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* 编辑角色对话框 */}
            <Dialog open={showEditDialog} onOpenChange={setShowEditDialog}>
                <DialogContent className="bg-manus-secondary border-manus-border max-w-2xl">
                    <DialogHeader>
                        <DialogTitle className="text-manus-text">编辑角色</DialogTitle>
                    </DialogHeader>
                    {editRole && (
                        <div className="space-y-4 py-4">
                            <div className="grid grid-cols-2 gap-4">
                                <Input
                                    placeholder="角色名称"
                                    value={editRole.name}
                                    onChange={(e) => setEditRole({ ...editRole, name: e.target.value })}
                                    className="bg-manus-tertiary border-manus-border"
                                    disabled={editRole.is_system}
                                />
                                <select
                                    value={editRole.data_scope}
                                    onChange={(e) => setEditRole({ ...editRole, data_scope: parseInt(e.target.value) })}
                                    className="p-2 rounded bg-manus-tertiary border border-manus-border text-manus-text"
                                >
                                    {DATA_SCOPE_OPTIONS.map(opt => (
                                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                                    ))}
                                </select>
                            </div>
                            <Input
                                placeholder="角色描述"
                                value={editRole.description || ''}
                                onChange={(e) => setEditRole({ ...editRole, description: e.target.value })}
                                className="bg-manus-tertiary border-manus-border"
                            />
                            <div className="border border-manus-border rounded-lg p-4">
                                <div className="flex items-center justify-between mb-3">
                                    <h4 className="font-medium text-manus-text">权限配置</h4>
                                    <div className="flex gap-2">
                                        <Button
                                            type="button"
                                            variant="outline"
                                            size="sm"
                                            onClick={() => {
                                                const allPerms = Object.values(permissions).flat().map((p: Permission) => p.code)
                                                setEditRole({ ...editRole, permissions: allPerms })
                                            }}
                                            className="text-xs h-7"
                                        >
                                            全选
                                        </Button>
                                        <Button
                                            type="button"
                                            variant="outline"
                                            size="sm"
                                            onClick={() => setEditRole(withForcedPermissions({ ...editRole, permissions: [] }))}
                                            className="text-xs h-7"
                                        >
                                            取消全选
                                        </Button>
                                    </div>
                                </div>
                                <ScrollArea className="h-[200px]">
                                    <div className="grid grid-cols-2 gap-4">
                                        {Object.entries(permissions).map(([module, perms]) => (
                                            <div key={module}>
                                                <h5 className="text-sm font-medium text-accent mb-2">{module}</h5>
                                                <div className="space-y-1">
                                                    {perms.map((perm: Permission) => (
                                                        <label key={perm.code} className="flex items-center gap-2 text-sm cursor-pointer">
                                                            <input
                                                                type="checkbox"
                                                                checked={editRole.permissions.includes(perm.code) || isForcedPermission(perm.code, editRole)}
                                                                onChange={() => togglePermission(perm.code, editRole, setEditRole)}
                                                                disabled={isForcedPermission(perm.code, editRole)}
                                                                className="rounded"
                                                            />
                                                            <span className="text-manus-text">{perm.description}</span>
                                                        </label>
                                                    ))}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </ScrollArea>
                            </div>
                        </div>
                    )}
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowEditDialog(false)}>取消</Button>
                        <Button onClick={handleUpdateRole} disabled={isSubmitting} className="bg-accent hover:bg-accent/90 text-white">
                            {isSubmitting && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                            保存
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* 确认删除对话框 */}
            <ConfirmDialog
                open={confirmDeleteOpen}
                onOpenChange={setConfirmDeleteOpen}
                title="删除角色"
                description={
                    <div className="space-y-2">
                        <p>确定删除该角色吗？此操作无法撤销。</p>
                        <p className="text-red-500 text-xs">注意：删除后关联该角色的用户将失去相应权限。</p>
                    </div>
                }
                onConfirm={handleConfirmDelete}
                loading={isDeleting}
                variant="destructive"
                confirmText="删除"
            />
        </div>
    )
}
