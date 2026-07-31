/**
 * 用户管理页面
 * 
 * 用户CRUD、多角色分配、部门分配
 * 左树右表交互模式：左侧部门列表 + 右侧用户列表
 */
import { useState, useEffect, useCallback } from 'react'
import { Navigate } from 'react-router-dom'
import { Users, Plus, Trash2, Loader2, RefreshCw, Settings, Building2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { getAuthHeader, useAuthStore } from '@/stores/authStore'
import { cn } from '@/lib/utils'
import { ConfirmDialog } from '@/components/ConfirmDialog'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

// ========== 类型定义 (适配后端多角色+部门) ==========

interface Role {
    id: number
    name: string
    description: string | null
}

interface Department {
    id: number
    name: string
    // children?: Department[] // Flattened, no children needed
}

interface User {
    id: string
    username: string
    email: string | null
    disabled: boolean
    roles: Role[]  // 多角色
    department_id: number | null
    department?: { id: number; name: string }
}

export default function UserManagePage() {
    const { user: currentUser } = useAuthStore()
    const [users, setUsers] = useState<User[]>([])
    const [roles, setRoles] = useState<Role[]>([])
    const [departments, setDepartments] = useState<Department[]>([])
    const [isLoading, setIsLoading] = useState(true)

    // [左树右表] 部门筛选状态
    const [selectedDeptId, setSelectedDeptId] = useState<number | null>(null)

    // 创建用户 (扩展支持角色和部门)
    const [showCreateDialog, setShowCreateDialog] = useState(false)
    const [newUser, setNewUser] = useState({
        username: '',
        password: '',
        email: '',
        roleIds: [] as number[],
        departmentId: null as number | null
    })
    const [isCreating, setIsCreating] = useState(false)

    // 编辑用户配置
    const [showEditDialog, setShowEditDialog] = useState(false)
    const [editUser, setEditUser] = useState<User | null>(null)
    const [selectedRoleIds, setSelectedRoleIds] = useState<number[]>([])
    const [editDeptId, setEditDeptId] = useState<number | null>(null)
    const [isSaving, setIsSaving] = useState(false)

    // 计算筛选后的用户列表
    const filteredUsers = (() => {
        if (selectedDeptId === null) {
            return users  // 未选择部门时显示全部
        }
        return users.filter(u => u.department_id === selectedDeptId)
    })()

    // 权限检查
    if (!currentUser?.permissions?.some((code) => code === '*' || code === 'user:manage')) {
        return <Navigate to="/" replace />
    }

    // 加载用户列表
    const loadUsers = useCallback(async () => {
        setIsLoading(true)
        try {
            const response = await fetch(`${API_BASE_URL}/admin/users`, {
                headers: getAuthHeader(),
            })
            if (response.ok) {
                const data = await response.json()
                // 临时适配：将旧格式转为新格式
                const adaptedUsers = (data.users || []).map((u: any) => ({
                    ...u,
                    roles: u.roles || (u.role ? [{ id: 0, name: u.role }] : []),
                    department_id: u.department_id || null,
                }))
                setUsers(adaptedUsers)
            }
        } catch (error) {
            console.error('加载用户失败:', error)
        }
        setIsLoading(false)
    }, [])

    // 加载角色列表
    const loadRoles = useCallback(async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/rbac/roles`, {
                headers: getAuthHeader(),
            })
            if (response.ok) {
                const data = await response.json()
                setRoles(data.roles || [])
            }
        } catch (error) {
            console.error('加载角色失败:', error)
        }
    }, [])

    // 加载部门列表 (扁平)
    const loadDepartments = useCallback(async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/departments/tree`, {
                headers: getAuthHeader(),
            })
            if (response.ok) {
                const data = await response.json()
                setDepartments(data)  // 直接使用扁平列表
            }
        } catch (error) {
            console.error('加载部门失败:', error)
        }
    }, [])

    useEffect(() => {
        loadUsers()
        loadRoles()
        loadDepartments()
    }, [loadUsers, loadRoles, loadDepartments])

    // 创建用户 (原子性事务)
    const handleCreateUser = async () => {
        if (!newUser.username || !newUser.password) return
        setIsCreating(true)
        try {
            const response = await fetch(`${API_BASE_URL}/admin/users`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify({
                    username: newUser.username,
                    password: newUser.password,
                    email: newUser.email || null,
                    role_ids: newUser.roleIds.length > 0 ? newUser.roleIds : undefined,
                    department_id: newUser.departmentId
                }),
            })
            if (response.ok) {
                setShowCreateDialog(false)
                setNewUser({ username: '', password: '', email: '', roleIds: [], departmentId: null })
                loadUsers()
            } else {
                const err = await response.json().catch(() => ({}))
                alert(err.detail || '创建用户失败')
            }
        } catch (error) {
            console.error('创建用户失败:', error)
        }
        setIsCreating(false)
    }

    // 打开编辑对话框
    const handleEditClick = (user: User) => {
        setEditUser(user)
        setSelectedRoleIds(user.roles.map(r => r.id))
        setEditDeptId(user.department_id)
        setShowEditDialog(true)
    }

    // 保存用户配置 (原子性 API)
    const handleSaveConfig = async () => {
        if (!editUser) return
        setIsSaving(true)
        try {
            // 使用单一 API 同时更新角色和部门 (原子性)
            const response = await fetch(`${API_BASE_URL}/admin/users/${editUser.id}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify({
                    role_ids: selectedRoleIds,
                    department_id: editDeptId
                }),
            })

            if (!response.ok) {
                const err = await response.json().catch(() => ({ detail: '保存失败' }))
                throw new Error(err.detail || '保存失败')
            }

            setShowEditDialog(false)
            loadUsers()
        } catch (error) {
            console.error('保存配置失败:', error)
            alert(error instanceof Error ? error.message : '保存配置失败')
        }
        setIsSaving(false)
    }

    // 删除确认状态
    const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
    const [userToDeleteId, setUserToDeleteId] = useState<string | null>(null)
    const [isDeleting, setIsDeleting] = useState(false)

    // 点击删除按钮
    const handleDeleteUser = (userId: string) => {
        setUserToDeleteId(userId)
        setConfirmDeleteOpen(true)
    }

    // 确认删除
    const handleConfirmDelete = async () => {
        if (!userToDeleteId) return

        setIsDeleting(true)
        try {
            const response = await fetch(`${API_BASE_URL}/admin/users/${userToDeleteId}`, {
                method: 'DELETE',
                headers: getAuthHeader(),
            })
            if (response.ok) {
                loadUsers()
                setConfirmDeleteOpen(false)
                setUserToDeleteId(null)
            }
        } catch (error) {
            console.error('删除用户失败:', error)
        } finally {
            setIsDeleting(false)
        }
    }

    // 切换角色选择
    const toggleRole = (roleId: number) => {
        setSelectedRoleIds(prev =>
            prev.includes(roleId)
                ? prev.filter(id => id !== roleId)
                : [...prev, roleId]
        )
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
            {/* 标题栏 */}
            <div className="flex items-center justify-between mb-4">
                <div>
                    <h1 className="text-2xl font-semibold text-manus-text flex items-center gap-2">
                        <Users className="h-6 w-6 text-accent" />
                        用户管理
                    </h1>
                    <p className="text-manus-muted mt-1">管理系统用户、角色分配和部门归属</p>
                </div>
                <div className="flex gap-2">
                    <Button
                        variant="outline"
                        onClick={loadUsers}
                        className="bg-manus-tertiary border-manus-border hover:bg-manus-hover"
                    >
                        <RefreshCw className="h-4 w-4 mr-2" />
                        刷新
                    </Button>
                    <Button
                        onClick={() => {
                            // 预填充当前选中的部门
                            setNewUser({
                                username: '',
                                password: '',
                                email: '',
                                roleIds: [],
                                departmentId: selectedDeptId
                            })
                            setShowCreateDialog(true)
                        }}
                        className="bg-accent hover:bg-accent/90 text-white"
                    >
                        <Plus className="h-4 w-4 mr-2" />
                        新建用户
                    </Button>
                </div>
            </div>

            {/* 左树右表主体 */}
            <div className="flex-1 flex gap-4 min-h-0">
                {/* 左侧：部门列表导航 */}
                <Card className="w-64 shrink-0 bg-manus-secondary border-manus-border">
                    <CardHeader className="py-3">
                        <CardTitle className="text-sm text-manus-text flex items-center gap-2">
                            <Building2 className="h-4 w-4" />
                            部门筛选
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="p-2">
                        <ScrollArea className="h-[calc(100vh-280px)]">
                            {/* 全部用户选项 */}
                            <div
                                onClick={() => setSelectedDeptId(null)}
                                className={cn(
                                    "px-3 py-2 rounded cursor-pointer transition-colors mb-1",
                                    selectedDeptId === null
                                        ? "bg-accent/20 text-accent"
                                        : "hover:bg-manus-hover text-manus-text"
                                )}
                            >
                                全部用户
                            </div>

                            {/* 部门列表 */}
                            {departments.map(dept => (
                                <div
                                    key={dept.id}
                                    onClick={() => setSelectedDeptId(dept.id)}
                                    className={cn(
                                        "px-3 py-2 rounded cursor-pointer transition-colors flex items-center gap-2",
                                        selectedDeptId === dept.id
                                            ? "bg-accent/20 text-accent"
                                            : "hover:bg-manus-hover text-manus-text"
                                    )}
                                >
                                    <Building2 className="h-4 w-4 opacity-70" />
                                    <span className="text-sm truncate">{dept.name}</span>
                                </div>
                            ))}
                        </ScrollArea>
                    </CardContent>
                </Card>

                {/* 右侧：用户列表 */}
                <Card className="flex-1 bg-manus-secondary border-manus-border">
                    <CardHeader className="py-3">
                        <CardTitle className="text-manus-text">
                            用户列表 ({filteredUsers.length})
                            {selectedDeptId !== null && (
                                <span className="text-xs text-manus-muted ml-2 font-normal">
                                    - {departments.find(d => d.id === selectedDeptId)?.name || ''}
                                </span>
                            )}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <ScrollArea className="h-[calc(100vh-280px)]">
                            <div className="space-y-2">
                                {filteredUsers.map(user => (
                                    <div
                                        key={user.id}
                                        className="p-4 rounded-lg border bg-manus-tertiary border-manus-border hover:border-accent/30 transition-colors"
                                    >
                                        <div className="flex items-center justify-between">
                                            <div className="flex items-center gap-4">
                                                <div className="w-10 h-10 rounded-full bg-accent/20 flex items-center justify-center">
                                                    <Users className="h-5 w-5 text-accent" />
                                                </div>
                                                <div>
                                                    <div className="font-medium text-manus-text">{user.username}</div>
                                                    <div className="text-sm text-manus-muted">{user.email || '未设置邮箱'}</div>
                                                </div>
                                            </div>
                                            <div className="flex items-center gap-4">
                                                {/* 角色标签 */}
                                                <div className="flex flex-wrap gap-1">
                                                    {user.roles.map(role => (
                                                        <span key={role.id} className={cn(
                                                            "px-2 py-0.5 rounded text-xs font-medium",
                                                            role.name === 'Admin'
                                                                ? "bg-accent/20 text-accent"
                                                                : "bg-manus-tertiary text-manus-muted border border-manus-border"
                                                        )}>
                                                            {role.name}
                                                        </span>
                                                    ))}
                                                    {user.roles.length === 0 && (
                                                        <span className="text-xs text-manus-muted">未分配角色</span>
                                                    )}
                                                </div>
                                                {/* 部门标签 */}
                                                {user.department?.name && (
                                                    <span className="text-xs text-blue-400 bg-blue-500/10 px-2 py-0.5 rounded">
                                                        {user.department.name}
                                                    </span>
                                                )}
                                                {user.disabled && (
                                                    <span className="px-2 py-1 rounded text-xs bg-red-500/20 text-red-400">
                                                        已禁用
                                                    </span>
                                                )}
                                                <div className="flex gap-1">
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => handleEditClick(user)}
                                                        title="配置角色和部门"
                                                        className="hover:bg-blue-600 hover:text-white"
                                                    >
                                                        <Settings className="h-4 w-4" />
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => handleDeleteUser(user.id)}
                                                        disabled={user.id === currentUser?.id}
                                                        className="hover:bg-red-600 hover:text-white text-red-400"
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                                {filteredUsers.length === 0 && (
                                    <div className="text-center py-8 text-manus-muted">
                                        该部门暂无用户
                                    </div>
                                )}
                            </div>
                        </ScrollArea>
                    </CardContent>
                </Card>
            </div>

            {/* 新建用户对话框 */}
            <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
                <DialogContent className="bg-manus-secondary border-manus-border max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="text-manus-text">新建用户</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-4 py-4">
                        {/* 基础信息 */}
                        <Input
                            placeholder="用户名 *"
                            value={newUser.username}
                            onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
                            className="bg-manus-tertiary border-manus-border"
                        />
                        <Input
                            type="password"
                            placeholder="密码 *"
                            value={newUser.password}
                            onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                            className="bg-manus-tertiary border-manus-border"
                        />
                        <Input
                            placeholder="邮箱（可选）"
                            value={newUser.email}
                            onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
                            className="bg-manus-tertiary border-manus-border"
                        />

                        {/* 部门选择 */}
                        <div>
                            <label className="text-sm font-medium text-manus-text mb-2 block">所属部门</label>
                            <select
                                value={newUser.departmentId || ''}
                                onChange={(e) => setNewUser({ ...newUser, departmentId: e.target.value ? parseInt(e.target.value) : null })}
                                className="w-full p-2 rounded bg-manus-tertiary border border-manus-border text-manus-text"
                            >
                                <option value="">未分配部门</option>
                                {departments.map(dept => (
                                    <option key={dept.id} value={dept.id}>
                                        {dept.name}
                                    </option>
                                ))}
                            </select>
                        </div>

                        {/* 角色选择 - Tag Select */}
                        <div>
                            <label className="text-sm font-medium text-manus-text mb-2 block">分配角色</label>
                            <div className="flex flex-wrap gap-2 p-3 border border-manus-border rounded-lg min-h-[60px] bg-manus-tertiary">
                                {roles.length > 0 ? (
                                    roles.map(role => (
                                        <span
                                            key={role.id}
                                            onClick={() => {
                                                const newRoleIds = newUser.roleIds.includes(role.id)
                                                    ? newUser.roleIds.filter(id => id !== role.id)
                                                    : [...newUser.roleIds, role.id]
                                                setNewUser({ ...newUser, roleIds: newRoleIds })
                                            }}
                                            className={cn(
                                                "px-3 py-1 rounded-full text-sm cursor-pointer transition-all",
                                                newUser.roleIds.includes(role.id)
                                                    ? "bg-accent text-white"
                                                    : "bg-manus-secondary text-manus-muted hover:bg-accent/20"
                                            )}
                                        >
                                            {role.name}
                                        </span>
                                    ))
                                ) : (
                                    <span className="text-manus-muted text-sm">暂无可用角色</span>
                                )}
                            </div>
                            <p className="text-xs text-manus-subtle mt-1">
                                点击选择角色，用户将获得所选角色的权限并集
                            </p>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowCreateDialog(false)}>
                            取消
                        </Button>
                        <Button onClick={handleCreateUser} disabled={isCreating} className="bg-accent hover:bg-accent/90 text-white transition-all">
                            {isCreating ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                            创建
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* 编辑用户配置对话框 */}
            <Dialog open={showEditDialog} onOpenChange={setShowEditDialog}>
                <DialogContent className="bg-manus-secondary border-manus-border max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="text-manus-text">
                            配置用户 - {editUser?.username}
                        </DialogTitle>
                    </DialogHeader>
                    <div className="space-y-6 py-4">
                        {/* 部门选择 */}
                        <div>
                            <label className="text-sm font-medium text-manus-text mb-2 block">所属部门</label>
                            <select
                                value={editDeptId || ''}
                                onChange={(e) => setEditDeptId(e.target.value ? parseInt(e.target.value) : null)}
                                className="w-full p-2 rounded bg-manus-tertiary border border-manus-border text-manus-text"
                            >
                                <option value="">未分配部门</option>
                                {departments.map(dept => (
                                    <option key={dept.id} value={dept.id}>
                                        {dept.name}
                                    </option>
                                ))}
                            </select>
                        </div>

                        {/* 角色选择 */}
                        <div>
                            <label className="text-sm font-medium text-manus-text mb-2 block">分配角色</label>
                            <div className="grid grid-cols-2 gap-2">
                                {roles.length > 0 ? (
                                    roles.map(role => (
                                        <label
                                            key={role.id}
                                            className={cn(
                                                "flex items-center gap-2 p-3 rounded-lg border cursor-pointer transition-colors",
                                                selectedRoleIds.includes(role.id)
                                                    ? "bg-accent/10 border-accent"
                                                    : "bg-manus-tertiary border-manus-border hover:border-accent/30"
                                            )}
                                        >
                                            <input
                                                type="checkbox"
                                                checked={selectedRoleIds.includes(role.id)}
                                                onChange={() => toggleRole(role.id)}
                                                className="rounded"
                                            />
                                            <div>
                                                <div className="text-sm font-medium text-manus-text">{role.name}</div>
                                                {role.description && (
                                                    <div className="text-xs text-manus-muted">{role.description}</div>
                                                )}
                                            </div>
                                        </label>
                                    ))
                                ) : (
                                    <div className="col-span-2 text-center py-4 text-manus-muted border border-dashed border-manus-border rounded-lg">
                                        暂无可用角色，请先创建角色
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowEditDialog(false)}>
                            取消
                        </Button>
                        <Button onClick={handleSaveConfig} disabled={isSaving} className="bg-accent hover:bg-accent/90 text-white transition-all">
                            {isSaving && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                            保存
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
            {/* 确认删除对话框 */}
            <ConfirmDialog
                open={confirmDeleteOpen}
                onOpenChange={setConfirmDeleteOpen}
                title="删除用户"
                description="确定删除该用户吗？此操作无法撤销。该用户将无法再登录系统。"
                onConfirm={handleConfirmDelete}
                loading={isDeleting}
                variant="destructive"
                confirmText="删除"
            />
        </div>
    )
}
