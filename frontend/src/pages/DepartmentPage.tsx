/**
 * 部门管理页面
 * 
 * 视觉优化版: 增加 Dashboard 概览、搜索筛选、成员表格化展示
 */
import { useState, useEffect, useCallback, useMemo } from 'react'
import { Navigate } from 'react-router-dom'
import {
    Building2, Plus, Trash2, Edit2, Loader2, RefreshCw,
    Users, Search, LayoutDashboard, Mail, Shield, Circle,
    MoreHorizontal
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { getAuthHeader, useAuthStore } from '@/stores/authStore'
import { cn } from '@/lib/utils'
import { ConfirmDialog } from '@/components/ConfirmDialog'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

interface Department {
    id: number
    name: string
    code: string | null
    parent_id: number | null
    leader_id: string | null
    children?: Department[]
}

interface DeptMember {
    id: string
    username: string
    email: string | null
    primary_role: string | null
    disabled: boolean
}

export default function DepartmentPage() {
    const { user: currentUser } = useAuthStore()
    const [departments, setDepartments] = useState<Department[]>([])
    const [isLoading, setIsLoading] = useState(true)
    const [selectedDept, setSelectedDept] = useState<Department | null>(null)
    const [searchQuery, setSearchQuery] = useState('')

    // 右键菜单状态 (保留右键习惯，但也提供更多操作入口)
    const [contextMenu, setContextMenu] = useState<{ x: number, y: number, dept: Department } | null>(null)

    // 对话框状态
    const [showCreateDialog, setShowCreateDialog] = useState(false)
    const [showEditDialog, setShowEditDialog] = useState(false)
    const [newDept, setNewDept] = useState({ name: '', code: '' })
    const [editDept, setEditDept] = useState<Department | null>(null)
    const [isSubmitting, setIsSubmitting] = useState(false)

    // 成员列表状态
    const [members, setMembers] = useState<DeptMember[]>([])
    const [isLoadingMembers, setIsLoadingMembers] = useState(false)

    // 搜索过滤
    const filteredDepartments = useMemo(() => {
        if (!searchQuery) return departments
        const lowerQuery = searchQuery.toLowerCase()
        return departments.filter(d =>
            d.name.toLowerCase().includes(lowerQuery) ||
            d.code?.toLowerCase().includes(lowerQuery)
        )
    }, [departments, searchQuery])

    // 权限检查
    if (!currentUser?.permissions?.some((code) => code === '*' || code === 'org:manage')) {
        return <Navigate to="/" replace />
    }

    // 点击空白处取消菜单
    useEffect(() => {
        const handleClick = () => setContextMenu(null)
        document.addEventListener('click', handleClick)
        return () => document.removeEventListener('click', handleClick)
    }, [])

    // 加载部门列表
    const loadDepartments = useCallback(async () => {
        setIsLoading(true)
        try {
            const response = await fetch(`${API_BASE_URL}/departments/tree`, {
                headers: getAuthHeader(),
            })
            if (response.ok) {
                const data = await response.json()
                setDepartments(data)
            }
        } catch (error) {
            console.error('加载部门失败:', error)
        }
        setIsLoading(false)
    }, [])

    useEffect(() => {
        loadDepartments()
    }, [loadDepartments])

    // 加载成员
    const loadMembers = useCallback(async (deptId: number) => {
        setIsLoadingMembers(true)
        try {
            const response = await fetch(
                `${API_BASE_URL}/departments/${deptId}/members`,
                { headers: getAuthHeader() }
            )
            if (response.ok) {
                const data = await response.json()
                setMembers(data)
            }
        } catch (error) {
            console.error('加载成员失败:', error)
            setMembers([])
        }
        setIsLoadingMembers(false)
    }, [])

    useEffect(() => {
        if (selectedDept) {
            loadMembers(selectedDept.id)
        } else {
            setMembers([])
        }
    }, [selectedDept, loadMembers])

    // 右键处理
    const handleContextMenu = (e: React.MouseEvent, dept: Department) => {
        e.preventDefault()
        e.stopPropagation()
        setContextMenu({ x: e.clientX, y: e.clientY, dept })
        setSelectedDept(dept)
    }

    // 创建部门
    const handleCreateDept = async () => {
        if (!newDept.name) return
        setIsSubmitting(true)
        try {
            const response = await fetch(`${API_BASE_URL}/departments`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify({
                    name: newDept.name,
                    code: newDept.code || null,
                    parent_id: null,
                }),
            })
            if (response.ok) {
                setShowCreateDialog(false)
                setNewDept({ name: '', code: '' })
                loadDepartments()
            }
        } catch (error) {
            console.error('创建部门失败:', error)
        }
        setIsSubmitting(false)
    }

    // 更新部门
    const handleUpdateDept = async () => {
        if (!editDept) return
        setIsSubmitting(true)
        try {
            const response = await fetch(`${API_BASE_URL}/departments/${editDept.id}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    ...getAuthHeader(),
                },
                body: JSON.stringify({
                    name: editDept.name,
                    code: editDept.code,
                }),
            })
            if (response.ok) {
                setShowEditDialog(false)
                setEditDept(null)
                loadDepartments()
            }
        } catch (error) {
            console.error('更新部门失败:', error)
        }
        setIsSubmitting(false)
    }

    // 删除状态
    const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
    const [deptToDeleteId, setDeptToDeleteId] = useState<number | null>(null)
    const [isDeleting, setIsDeleting] = useState(false)

    const handleDeleteClick = (deptId: number) => {
        setDeptToDeleteId(deptId)
        setConfirmDeleteOpen(true)
    }

    const handleConfirmDelete = async () => {
        if (!deptToDeleteId) return
        setIsDeleting(true)
        try {
            const response = await fetch(`${API_BASE_URL}/departments/${deptToDeleteId}`, {
                method: 'DELETE',
                headers: getAuthHeader(),
            })
            if (response.ok) {
                loadDepartments()
                if (selectedDept?.id === deptToDeleteId) {
                    setSelectedDept(null)
                }
                setConfirmDeleteOpen(false)
                setDeptToDeleteId(null)
            }
        } catch (error) {
            console.error('删除部门失败:', error)
        } finally {
            setIsDeleting(false)
        }
    }

    if (isLoading) {
        return (
            <div className="flex-1 flex items-center justify-center bg-manus">
                <Loader2 className="h-8 w-8 animate-spin text-accent" />
            </div>
        )
    }

    return (
        <div className="flex-1 flex flex-col h-full bg-manus p-6" onClick={() => setSelectedDept(null)}>
            <div className="max-w-6xl mx-auto w-full h-full flex flex-col gap-6" onClick={e => e.stopPropagation()}>

                {/* 顶部 Header 区 */}
                <div className="flex items-center justify-between">
                    <div>
                        <h1 className="text-2xl font-semibold text-manus-text flex items-center gap-2">
                            <Building2 className="h-6 w-6 text-accent" />
                            部门管理
                        </h1>
                        <p className="text-manus-muted mt-1 text-sm">配置组织架构，管理各部门成员及权限范围</p>
                    </div>
                    <div className="flex gap-3">
                        <Button
                            variant="outline"
                            onClick={loadDepartments}
                            className="bg-manus-tertiary border-manus-border hover:bg-manus-hover transition-all"
                        >
                            <RefreshCw className="h-4 w-4 mr-2" />
                            刷新
                        </Button>
                        <Button
                            onClick={() => {
                                setNewDept({ name: '', code: '' })
                                setShowCreateDialog(true)
                            }}
                            className="bg-accent hover:bg-accent/90 text-white shadow-lg shadow-accent/20 transition-all"
                        >
                            <Plus className="h-4 w-4 mr-2" />
                            新建部门
                        </Button>
                    </div>
                </div>

                <div className="flex gap-6 flex-1 min-h-0">
                    {/* 左侧：部门列表 (增强版) */}
                    <Card className="w-80 shrink-0 bg-manus-secondary border-manus-border flex flex-col shadow-sm">
                        <div className="p-4 border-b border-manus-border/50 space-y-3">
                            <div className="relative">
                                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-manus-muted" />
                                <Input
                                    placeholder="搜索部门..."
                                    value={searchQuery}
                                    onChange={e => setSearchQuery(e.target.value)}
                                    className="pl-9 bg-manus-tertiary border-transparent focus:bg-manus focus:border-accent/50 transition-all"
                                />
                            </div>
                        </div>
                        <ScrollArea className="flex-1">
                            <div className="p-2 space-y-1">
                                {filteredDepartments.length === 0 ? (
                                    <div className="text-center py-12 text-manus-muted text-sm">
                                        无匹配部门
                                    </div>
                                ) : (
                                    filteredDepartments.map(dept => (
                                        <div
                                            key={dept.id}
                                            className={cn(
                                                "group flex items-center justify-between px-3 py-2.5 rounded-lg cursor-pointer transition-all duration-200 border border-transparent",
                                                selectedDept?.id === dept.id
                                                    ? "bg-accent/10 border-accent/20 shadow-sm"
                                                    : "hover:bg-manus-tertiary hover:border-manus-border/50"
                                            )}
                                            onClick={() => setSelectedDept(dept)}
                                            onContextMenu={(e) => handleContextMenu(e, dept)}
                                        >
                                            <div className="flex items-center gap-3 overflow-hidden">
                                                <div className={cn(
                                                    "h-8 w-8 rounded-md flex items-center justify-center shrink-0 transition-colors",
                                                    selectedDept?.id === dept.id ? "bg-accent/20 text-accent" : "bg-manus-tertiary text-manus-muted group-hover:bg-manus-hover group-hover:text-manus-text"
                                                )}>
                                                    <Building2 className="h-4 w-4" />
                                                </div>
                                                <div className="flex flex-col min-w-0">
                                                    <span className={cn(
                                                        "text-sm font-medium truncate",
                                                        selectedDept?.id === dept.id ? "text-accent" : "text-manus-text"
                                                    )}>
                                                        {dept.name}
                                                    </span>
                                                    {dept.code && (
                                                        <span className="text-[10px] text-manus-muted truncate font-mono">
                                                            {dept.code}
                                                        </span>
                                                    )}
                                                </div>
                                            </div>

                                            {/* 操作菜单 (Hover显示或选中显示) */}
                                            <DropdownMenu>
                                                <DropdownMenuTrigger asChild>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className={cn(
                                                            "h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity",
                                                            selectedDept?.id === dept.id && "opacity-100"
                                                        )}
                                                        onClick={e => e.stopPropagation()}
                                                    >
                                                        <MoreHorizontal className="h-4 w-4 text-manus-muted" />
                                                    </Button>
                                                </DropdownMenuTrigger>
                                                <DropdownMenuContent align="end" className="w-32">
                                                    <DropdownMenuItem onClick={(e) => {
                                                        e.stopPropagation()
                                                        setEditDept(dept)
                                                        setShowEditDialog(true)
                                                    }}>
                                                        <Edit2 className="mr-2 h-4 w-4" /> 编辑
                                                    </DropdownMenuItem>
                                                    <DropdownMenuItem
                                                        className="text-red-500 focus:text-red-500"
                                                        onClick={(e) => {
                                                            e.stopPropagation()
                                                            handleDeleteClick(dept.id)
                                                        }}
                                                    >
                                                        <Trash2 className="mr-2 h-4 w-4" /> 删除
                                                    </DropdownMenuItem>
                                                </DropdownMenuContent>
                                            </DropdownMenu>
                                        </div>
                                    ))
                                )}
                            </div>
                        </ScrollArea>
                        <div className="p-3 border-t border-manus-border/50 text-xs text-center text-manus-muted bg-manus-tertiary/30">
                            共 {filteredDepartments.length} 个部门
                        </div>
                    </Card>

                    {/* 右侧：主内容区 */}
                    <div className="flex-1 flex flex-col min-w-0">
                        {!selectedDept ? (
                            // 空状态：仪表盘概览
                            <div className="h-full flex flex-col gap-6 animate-in fade-in duration-500">
                                <Card className="bg-manus-secondary border-manus-border shadow-sm">
                                    <CardHeader>
                                        <CardTitle className="text-lg flex items-center gap-2">
                                            <LayoutDashboard className="h-5 w-5 text-accent" />
                                            组织概览
                                        </CardTitle>
                                    </CardHeader>
                                    <CardContent className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <div className="bg-manus-tertiary/50 p-4 rounded-lg flex items-center gap-4 border border-manus-border/50">
                                            <div className="h-10 w-10 rounded-full bg-blue-500/10 flex items-center justify-center text-blue-500">
                                                <Building2 className="h-5 w-5" />
                                            </div>
                                            <div>
                                                <div className="text-sm text-manus-muted">部门总数</div>
                                                <div className="text-2xl font-bold text-manus-text">{departments.length}</div>
                                            </div>
                                        </div>
                                        {/* 可以在这里添加更多统计，如总成员数等，目前暂无API支持 */}
                                    </CardContent>
                                </Card>

                                <div className="flex-1 rounded-xl border-2 border-dashed border-manus-border/50 flex flex-col items-center justify-center text-manus-muted bg-manus-tertiary/10">
                                    <div className="h-16 w-16 rounded-full bg-manus-tertiary flex items-center justify-center mb-4">
                                        <Building2 className="h-8 w-8 opacity-50" />
                                    </div>
                                    <h3 className="text-lg font-medium text-manus-text">选择一个部门</h3>
                                    <p className="text-sm mt-1">查看部门详情、成员列表及权限设置</p>
                                </div>
                            </div>
                        ) : (
                            // 选中状态：部门详情
                            <Card className="flex-1 bg-manus-secondary border-manus-border shadow-sm flex flex-col h-full animate-in slide-in-from-bottom-2 duration-300">
                                <CardHeader className="py-4 border-b border-manus-border/50 bg-manus-tertiary/10">
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-4">
                                            <div className="h-12 w-12 rounded-lg bg-accent/10 flex items-center justify-center border border-accent/20">
                                                <Building2 className="h-6 w-6 text-accent" />
                                            </div>
                                            <div>
                                                <CardTitle className="text-xl text-manus-text">
                                                    {selectedDept.name}
                                                </CardTitle>
                                                <CardDescription className="flex items-center gap-4 mt-1">
                                                    <span>部门编码: {selectedDept.code || '未设置'}</span>
                                                    <span>•</span>
                                                    <span className="flex items-center gap-1">
                                                        <Users className="h-3 w-3" />
                                                        {members.length} 名成员
                                                    </span>
                                                </CardDescription>
                                            </div>
                                        </div>
                                        <div className="flex gap-2">
                                            <Button
                                                variant="outline" size="sm"
                                                onClick={() => {
                                                    setEditDept(selectedDept)
                                                    setShowEditDialog(true)
                                                }}
                                                className="h-8"
                                            >
                                                <Edit2 className="h-3 w-3 mr-1.5" />
                                                编辑
                                            </Button>
                                            <Button
                                                variant="destructive" size="sm"
                                                onClick={() => handleDeleteClick(selectedDept.id)}
                                                className="h-8 bg-red-500/10 text-red-500 hover:bg-red-500/20 shadow-none border border-red-500/20"
                                            >
                                                <Trash2 className="h-3 w-3 mr-1.5" />
                                                删除
                                            </Button>
                                        </div>
                                    </div>
                                </CardHeader>

                                <CardContent className="p-0 flex-1 flex flex-col min-h-0">
                                    {isLoadingMembers ? (
                                        <div className="flex-1 flex items-center justify-center">
                                            <div className="flex flex-col items-center gap-2">
                                                <Loader2 className="h-8 w-8 animate-spin text-accent" />
                                                <p className="text-sm text-manus-muted">加载成员中...</p>
                                            </div>
                                        </div>
                                    ) : members.length === 0 ? (
                                        <div className="flex-1 flex flex-col items-center justify-center text-manus-muted">
                                            <div className="h-16 w-16 rounded-full bg-manus-tertiary flex items-center justify-center mb-4">
                                                <Users className="h-8 w-8 opacity-50" />
                                            </div>
                                            <p>该部门暂无成员</p>
                                            <Button variant="link" className="text-accent mt-2">
                                                去用户管理添加成员
                                            </Button>
                                        </div>
                                    ) : (
                                        <div className="flex-1 overflow-auto">
                                            <table className="w-full text-sm text-left">
                                                <thead className="text-xs text-manus-muted uppercase bg-manus-tertiary/50 sticky top-0 backdrop-blur-sm z-10">
                                                    <tr>
                                                        <th className="px-6 py-3 font-medium">成员信息</th>
                                                        <th className="px-6 py-3 font-medium">邮箱</th>
                                                        <th className="px-6 py-3 font-medium">角色</th>
                                                        <th className="px-6 py-3 font-medium text-right">状态</th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-manus-border/50">
                                                    {members.map(member => (
                                                        <tr key={member.id} className="hover:bg-manus-tertiary/30 transition-colors group">
                                                            <td className="px-6 py-3">
                                                                <div className="flex items-center gap-3">
                                                                    <Avatar className="h-8 w-8 border border-manus-border">
                                                                        <AvatarImage src="" /> {/* 预留头像URL */}
                                                                        <AvatarFallback className="bg-accent/10 text-accent text-xs">
                                                                            {member.username.slice(0, 2).toUpperCase()}
                                                                        </AvatarFallback>
                                                                    </Avatar>
                                                                    <div className="font-medium text-manus-text">
                                                                        {member.username}
                                                                    </div>
                                                                </div>
                                                            </td>
                                                            <td className="px-6 py-3 text-manus-muted">
                                                                <div className="flex items-center gap-2">
                                                                    <Mail className="h-3 w-3 opacity-50" />
                                                                    {member.email || '-'}
                                                                </div>
                                                            </td>
                                                            <td className="px-6 py-3">
                                                                {member.primary_role ? (
                                                                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20">
                                                                        <Shield className="h-3 w-3 mr-1" />
                                                                        {member.primary_role}
                                                                    </span>
                                                                ) : (
                                                                    <span className="text-manus-muted">-</span>
                                                                )}
                                                            </td>
                                                            <td className="px-6 py-3 text-right">
                                                                <div className="flex items-center justify-end gap-2">
                                                                    {member.disabled ? (
                                                                        <span className="flex items-center gap-1.5 text-xs text-red-400 bg-red-500/10 px-2 py-0.5 rounded-full">
                                                                            <Circle className="h-2 w-2 fill-current" /> 已禁用
                                                                        </span>
                                                                    ) : (
                                                                        <span className="flex items-center gap-1.5 text-xs text-green-400 bg-green-500/10 px-2 py-0.5 rounded-full">
                                                                            <Circle className="h-2 w-2 fill-current" /> 正常
                                                                        </span>
                                                                    )}
                                                                </div>
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}
                                </CardContent>
                            </Card>
                        )}
                    </div>
                </div>

                {/* 右键菜单 (仅在 Card 内部有效，这里主要作为全局覆盖) */}
                {contextMenu && (
                    <div
                        className="fixed z-50 bg-manus-secondary border border-manus-border rounded-lg shadow-xl py-1 min-w-[150px] animate-in fade-in zoom-in-95 duration-100"
                        style={{ left: contextMenu.x, top: contextMenu.y }}
                    >
                        <button
                            className="w-full text-left px-4 py-2 hover:bg-manus-tertiary text-sm text-manus-text flex items-center gap-2"
                            onClick={() => {
                                setEditDept(contextMenu.dept)
                                setShowEditDialog(true)
                                setContextMenu(null)
                            }}
                        >
                            <Edit2 className="h-4 w-4" />
                            编辑部门
                        </button>
                        <button
                            className="w-full text-left px-4 py-2 hover:bg-manus-tertiary text-sm text-red-500 hover:text-red-400 flex items-center gap-2"
                            onClick={() => {
                                handleDeleteClick(contextMenu.dept.id)
                                setContextMenu(null)
                            }}
                        >
                            <Trash2 className="h-4 w-4" />
                            删除部门
                        </button>
                    </div>
                )}

                {/* 新建部门对话框 */}
                <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
                    <DialogContent className="bg-manus-secondary border-manus-border sm:max-w-[425px]">
                        <DialogHeader>
                            <DialogTitle className="text-manus-text flex items-center gap-2">
                                <Plus className="h-5 w-5 text-accent" />
                                新建部门
                            </DialogTitle>
                        </DialogHeader>
                        <div className="space-y-4 py-4">
                            <div className="space-y-2">
                                <label className="text-sm font-medium text-manus-text">部门名称</label>
                                <Input
                                    placeholder="例如：研发部"
                                    value={newDept.name}
                                    onChange={(e) => setNewDept({ ...newDept, name: e.target.value })}
                                    className="bg-manus-tertiary border-manus-border focus:border-accent/50"
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-medium text-manus-text">部门编码 (可选)</label>
                                <Input
                                    placeholder="例如：RD01"
                                    value={newDept.code}
                                    onChange={(e) => setNewDept({ ...newDept, code: e.target.value })}
                                    className="bg-manus-tertiary border-manus-border focus:border-accent/50"
                                />
                            </div>
                        </div>
                        <DialogFooter>
                            <Button variant="outline" onClick={() => setShowCreateDialog(false)}>取消</Button>
                            <Button onClick={handleCreateDept} disabled={isSubmitting} className="bg-accent hover:bg-accent/90 text-white">
                                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                                创建
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>

                {/* 编辑部门对话框 */}
                <Dialog open={showEditDialog} onOpenChange={setShowEditDialog}>
                    <DialogContent className="bg-manus-secondary border-manus-border sm:max-w-[425px]">
                        <DialogHeader>
                            <DialogTitle className="text-manus-text flex items-center gap-2">
                                <Edit2 className="h-5 w-5 text-accent" />
                                编辑部门
                            </DialogTitle>
                        </DialogHeader>
                        {editDept && (
                            <div className="space-y-4 py-4">
                                <div className="space-y-2">
                                    <label className="text-sm font-medium text-manus-text">部门名称</label>
                                    <Input
                                        value={editDept.name}
                                        onChange={(e) => setEditDept({ ...editDept, name: e.target.value })}
                                        className="bg-manus-tertiary border-manus-border focus:border-accent/50"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium text-manus-text">部门编码</label>
                                    <Input
                                        value={editDept.code || ''}
                                        onChange={(e) => setEditDept({ ...editDept, code: e.target.value })}
                                        className="bg-manus-tertiary border-manus-border focus:border-accent/50"
                                    />
                                </div>
                            </div>
                        )}
                        <DialogFooter>
                            <Button variant="outline" onClick={() => setShowEditDialog(false)}>取消</Button>
                            <Button onClick={handleUpdateDept} disabled={isSubmitting} className="bg-accent hover:bg-accent/90 text-white">
                                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                                保存修改
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>

                {/* 确认删除对话框 */}
                <ConfirmDialog
                    open={confirmDeleteOpen}
                    onOpenChange={setConfirmDeleteOpen}
                    title="删除部门"
                    description={
                        <div className="space-y-2">
                            <p>确定删除该部门吗？此操作无法撤销。</p>
                            <p className="text-red-500 text-xs bg-red-500/10 p-2 rounded">
                                注意：请确保该部门下没有成员，否则可能删除失败。
                            </p>
                        </div>
                    }
                    onConfirm={handleConfirmDelete}
                    loading={isDeleting}
                    variant="destructive"
                    confirmText="确认删除"
                />
            </div>
        </div>
    )
}
