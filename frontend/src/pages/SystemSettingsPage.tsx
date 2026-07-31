/**
 * 智能体全局设置页面 (原系统设置)
 * 
 * 超级管理员功能：
 * 1. 管理系统回复风格模板
 * 2. 为指定用户配置智能体参数
 */
import { useState, useEffect } from 'react'
import { Navigate } from 'react-router-dom'
import { Settings, Users, Palette, Plus, Trash2, Edit2, X, Loader2, RotateCcw, Bot, Search, Building2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useAuthStore } from '@/stores/authStore'
import { useToast } from '@/components/ui/toast'
import { API_BASE_URL } from '@/config'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '@/components/ui/dialog'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { useDebounce } from '@/hooks/useDebounce'

interface SystemTemplate {
    template_id: string
    name: string
    description: string
    prompt: string
    is_default: boolean
    sort_order: number
}

interface UserConfig {
    user_id: string
    username: string
    department?: string
    max_retries: number
    always_confirm: boolean
    execution_mode: string
    synthesizer_template: string
    has_custom_config: boolean
}

export default function SystemSettingsPage() {
    const { user: currentUser, token } = useAuthStore()
    const { toast } = useToast()

    // Tab 状态
    const [activeTab, setActiveTab] = useState<'templates' | 'users'>('templates')

    // 模板管理状态
    const [templates, setTemplates] = useState<SystemTemplate[]>([])
    const [isLoadingTemplates, setIsLoadingTemplates] = useState(true)
    const [editingTemplate, setEditingTemplate] = useState<SystemTemplate | null>(null)
    const [isCreating, setIsCreating] = useState(false)
    const [newTemplate, setNewTemplate] = useState({
        template_id: '',
        name: '',
        description: '',
        prompt: ''
    })

    // 用户管理状态
    const [users, setUsers] = useState<UserConfig[]>([])
    const [isLoadingUsers, setIsLoadingUsers] = useState(false)
    const [searchQuery, setSearchQuery] = useState('')
    const debouncedSearch = useDebounce(searchQuery, 500)
    const [selectedUser, setSelectedUser] = useState<UserConfig | null>(null)
    const [userConfigDialog, setUserConfigDialog] = useState(false)
    const [selectedDept, setSelectedDept] = useState<string>("all")
    const [departments, setDepartments] = useState<{ id: number, name: string }[]>([])
    const [userConfigForm, setUserConfigForm] = useState({
        max_retries: 2,
        always_confirm: false,
        execution_mode: 'auto',
        synthesizer_template: 'default'
    })

    // 权限检查
    if (!currentUser?.permissions?.some((code) => code === '*' || code === 'config:manage')) {
        return <Navigate to="/" replace />
    }

    // 加载系统模板
    useEffect(() => {
        const loadTemplates = async () => {
            try {
                const response = await fetch(`${API_BASE_URL}/config/system/system-templates`, {
                    headers: { 'Authorization': `Bearer ${token}` }
                })
                if (response.ok) {
                    setTemplates(await response.json())
                }
            } catch (error) {
                toast({ type: 'error', title: '加载失败', description: '无法加载系统模板' })
            } finally {
                setIsLoadingTemplates(false)
            }
        }
        loadTemplates()
    }, [token])

    // 加载部门列表
    const fetchDepartments = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/config/system/departments`, {
                headers: { 'Authorization': `Bearer ${token}` }
            })
            if (response.ok) {
                const data = await response.json()
                setDepartments(data)
            }
        } catch (error) {
            console.error("加载部门失败", error)
        }
    }

    // 加载用户列表
    const fetchUsers = async () => {
        setIsLoadingUsers(true)
        try {
            const url = new URL(`${API_BASE_URL}/config/system/users`, window.location.origin)
            url.searchParams.append('page', '1')
            url.searchParams.append('page_size', '20')

            if (debouncedSearch) {
                url.searchParams.append('search', debouncedSearch)
            }
            if (selectedDept && selectedDept !== "all") {
                url.searchParams.append('department_id', selectedDept)
            }

            const response = await fetch(url.toString(), {
                headers: { 'Authorization': `Bearer ${token}` }
            })
            if (response.ok) {
                const data = await response.json()
                setUsers(data.users)
                // setTotal(data.total) // 如果后端返回了 total
            } else {
                const errorData = await response.json().catch(() => ({}))
                console.error("加载用户列表失败:", errorData)
                toast({
                    type: 'error',
                    title: '加载失败',
                    description: errorData.detail || '无法加载用户列表，请检查网络或后台日志'
                })
            }
        } catch (error: any) {
            console.error("请求异常:", error)
            toast({
                type: 'error',
                title: '网络错误',
                description: error.message || '请求失败，请稍后重试'
            })
        } finally {
            setIsLoadingUsers(false)
        }
    }

    useEffect(() => {
        if (activeTab === 'users') {
            fetchUsers()
            fetchDepartments()
        }
    }, [activeTab, token, debouncedSearch, selectedDept])

    // 创建模板
    const handleCreateTemplate = async () => {
        if (!newTemplate.name || !newTemplate.prompt) {
            toast({ type: 'error', title: '验证失败', description: '请填写必填字段' })
            return
        }

        // 自动生成 ID (tpl_时间戳_随机数)
        const autoId = `tpl_${Date.now()}_${Math.floor(Math.random() * 1000)}`
        const templateToCreate = {
            ...newTemplate,
            template_id: newTemplate.template_id || autoId
        }

        try {
            const response = await fetch(`${API_BASE_URL}/config/system/system-templates`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(templateToCreate)
            })

            if (response.ok) {
                const created = await response.json()
                setTemplates([...templates, created])
                setIsCreating(false)
                setNewTemplate({ template_id: '', name: '', description: '', prompt: '' })
                toast({ type: 'success', title: '创建成功', description: `模板 "${created.name}" 已创建` })
            } else {
                const error = await response.json()
                toast({ type: 'error', title: '创建失败', description: error.detail })
            }
        } catch {
            toast({ type: 'error', title: '创建失败', description: '网络错误' })
        }
    }

    // 更新模板
    const handleUpdateTemplate = async () => {
        if (!editingTemplate) return
        if (editingTemplate.is_default || editingTemplate.template_id === 'default') {
            toast({ type: 'error', title: '不允许编辑', description: 'default 模板由系统托管，不可修改' })
            setEditingTemplate(null)
            return
        }

        try {
            const response = await fetch(
                `${API_BASE_URL}/config/system/system-templates/${editingTemplate.template_id}`,
                {
                    method: 'PUT',
                    headers: {
                        'Authorization': `Bearer ${token}`,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        name: editingTemplate.name,
                        description: editingTemplate.description,
                        prompt: editingTemplate.prompt
                    })
                }
            )

            if (response.ok) {
                const updated = await response.json()
                setTemplates(templates.map(t => t.template_id === updated.template_id ? updated : t))
                setEditingTemplate(null)
                toast({ type: 'success', title: '保存成功' })
            } else {
                const error = await response.json().catch(() => ({}))
                toast({ type: 'error', title: '保存失败', description: error.detail || '请求失败' })
            }
        } catch {
            toast({ type: 'error', title: '保存失败' })
        }
    }

    // 删除模板
    const handleDeleteTemplate = async (templateId: string) => {
        if (!confirm('确定要删除此模板吗？')) return

        try {
            const response = await fetch(
                `${API_BASE_URL}/config/system/system-templates/${templateId}`,
                {
                    method: 'DELETE',
                    headers: { 'Authorization': `Bearer ${token}` }
                }
            )

            if (response.ok) {
                setTemplates(templates.filter(t => t.template_id !== templateId))
                toast({ type: 'success', title: '删除成功' })
            } else {
                const error = await response.json()
                toast({ type: 'error', title: '删除失败', description: error.detail })
            }
        } catch {
            toast({ type: 'error', title: '删除失败' })
        }
    }

    // 打开用户配置对话框
    const openUserConfigDialog = (user: UserConfig) => {
        setSelectedUser(user)
        setUserConfigForm({
            max_retries: user.max_retries,
            always_confirm: user.always_confirm,
            execution_mode: user.execution_mode,
            synthesizer_template: user.synthesizer_template
        })
        setUserConfigDialog(true)
    }

    // 保存用户配置
    const handleSaveUserConfig = async () => {
        if (!selectedUser) return

        try {
            const response = await fetch(
                `${API_BASE_URL}/config/system/users/${selectedUser.user_id}/config`,
                {
                    method: 'PUT',
                    headers: {
                        'Authorization': `Bearer ${token}`,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(userConfigForm)
                }
            )

            if (response.ok) {
                toast({ type: 'success', title: '保存成功', description: `已更新 ${selectedUser.username} 的配置` })
                setUserConfigDialog(false)
                fetchUsers()
            }
        } catch {
            toast({ type: 'error', title: '保存失败' })
        }
    }

    // 重置用户配置
    const handleResetUserConfig = async (userId: string, username: string) => {
        if (!confirm(`确定要重置 ${username} 的配置吗？`)) return

        try {
            const response = await fetch(
                `${API_BASE_URL}/config/system/users/${userId}/config/reset`,
                {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${token}` }
                }
            )

            if (response.ok) {
                toast({ type: 'success', title: '重置成功' })
                fetchUsers()
            }
        } catch {
            toast({ type: 'error', title: '重置失败' })
        }
    }

    return (
        <div className="flex-1 flex flex-col h-full bg-manus p-6 overflow-hidden">
            {/* 标题栏 */}
            <div className="flex items-center justify-between mb-4 flex-shrink-0">
                <div>
                    <h1 className="text-2xl font-semibold text-manus-text flex items-center gap-2">
                        <Settings className="h-6 w-6 text-accent" />
                        智能体全局设置
                    </h1>
                    <p className="text-manus-muted mt-1">管理系统默认回复风格和全局用户智能体参数</p>
                </div>
                {activeTab === 'templates' && (
                    <Button
                        onClick={() => setIsCreating(true)}
                        className="bg-accent hover:bg-accent/90 text-white"
                    >
                        <Plus className="h-4 w-4 mr-2" />
                        新建全局模板
                    </Button>
                )}
            </div>

            {/* 内容区域容器 */}
            <Card className="flex-1 min-h-0 bg-manus-secondary border-manus-border flex flex-col">
                {/* Tabs 头部 */}
                <CardHeader className="py-0 px-0 border-b border-manus-border flex-shrink-0">
                    <div className="flex">
                        <button
                            onClick={() => setActiveTab('templates')}
                            className={cn(
                                "px-6 py-4 text-sm font-medium border-b-2 transition-colors flex items-center gap-2",
                                activeTab === 'templates'
                                    ? "border-accent text-accent bg-accent/5"
                                    : "border-transparent text-manus-muted hover:text-manus-text hover:bg-manus-hover"
                            )}
                        >
                            <Palette className="h-4 w-4" />
                            回复风格模板
                        </button>
                        <button
                            onClick={() => setActiveTab('users')}
                            className={cn(
                                "px-6 py-4 text-sm font-medium border-b-2 transition-colors flex items-center gap-2",
                                activeTab === 'users'
                                    ? "border-accent text-accent bg-accent/5"
                                    : "border-transparent text-manus-muted hover:text-manus-text hover:bg-manus-hover"
                            )}
                        >
                            <Bot className="h-4 w-4" />
                            用户智能体配置
                        </button>
                    </div>
                </CardHeader>

                <CardContent className="flex-1 min-h-0 p-0 overflow-hidden">
                    <ScrollArea className="h-full">
                        <div className="p-6">
                            {/* ================= 模板列表 ================= */}
                            {activeTab === 'templates' && (
                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 max-w-full"> {/* 使用两列网格布局 */}
                                    {/* 创建表单 (占据满宽) */}
                                    {isCreating && (
                                        <div className="lg:col-span-2 bg-manus-tertiary border border-accent/50 rounded-lg p-4 space-y-4 shadow-sm animate-in fade-in slide-in-from-top-4">
                                            <div className="flex items-center justify-between pb-2 border-b border-manus-border">
                                                <h3 className="font-medium text-manus-text">新建模板</h3>
                                                <Button variant="ghost" size="sm" onClick={() => setIsCreating(false)}><X className="h-4 w-4" /></Button>
                                            </div>
                                            <div className="grid grid-cols-1 gap-4">
                                                <div>
                                                    <label className="text-sm font-medium text-manus-muted mb-1 block">模板名称</label>
                                                    <Input
                                                        value={newTemplate.name}
                                                        onChange={e => setNewTemplate({ ...newTemplate, name: e.target.value })}
                                                        placeholder="例如: 专业报告风格"
                                                        className="bg-manus-secondary border-manus-border"
                                                    />
                                                </div>
                                            </div>
                                            <div>
                                                <label className="text-sm font-medium text-manus-muted mb-1 block">描述</label>
                                                <Input
                                                    value={newTemplate.description}
                                                    onChange={e => setNewTemplate({ ...newTemplate, description: e.target.value })}
                                                    placeholder="简要描述模板用途"
                                                    className="bg-manus-secondary border-manus-border"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-sm font-medium text-manus-muted mb-1 block">Prompt 内容 (支持 Jinja2)</label>
                                                <Textarea
                                                    value={newTemplate.prompt}
                                                    onChange={e => setNewTemplate({ ...newTemplate, prompt: e.target.value })}
                                                    placeholder="输入 Prompt..."
                                                    rows={6}
                                                    className="bg-manus-secondary border-manus-border text-sm leading-relaxed"
                                                />
                                            </div>
                                            <div className="flex justify-end gap-2 pt-2">
                                                <Button variant="ghost" onClick={() => setIsCreating(false)}>取消</Button>
                                                <Button onClick={handleCreateTemplate} className="bg-accent text-white">确认创建</Button>
                                            </div>
                                        </div>
                                    )}

                                    {/* 列表渲染 */}
                                    {isLoadingTemplates ? (
                                        <div className="col-span-full flex flex-col items-center justify-center py-20 text-manus-muted">
                                            <Loader2 className="h-8 w-8 animate-spin mb-2" />
                                            <span className="text-sm">加载模板中...</span>
                                        </div>
                                    ) : (
                                        templates.map(template => (
                                            <div
                                                key={template.template_id}
                                                className={cn(
                                                    "group bg-manus-tertiary border border-manus-border rounded-lg p-5 transition-all text-manus-text flex flex-col h-full",
                                                    editingTemplate?.template_id === template.template_id ? "ring-2 ring-accent border-transparent lg:col-span-2" : "hover:border-accent/30"
                                                )}
                                            >
                                                {editingTemplate?.template_id === template.template_id ? (
                                                    <div className="space-y-4">
                                                        <div className="grid grid-cols-2 gap-4">
                                                            <Input
                                                                value={editingTemplate.name}
                                                                onChange={e => setEditingTemplate({ ...editingTemplate, name: e.target.value })}
                                                                className="bg-manus-secondary border-manus-border"
                                                            />
                                                            <Input
                                                                value={editingTemplate.description}
                                                                onChange={e => setEditingTemplate({ ...editingTemplate, description: e.target.value })}
                                                                className="bg-manus-secondary border-manus-border"
                                                            />
                                                        </div>
                                                        <Textarea
                                                            value={editingTemplate.prompt}
                                                            onChange={e => setEditingTemplate({ ...editingTemplate, prompt: e.target.value })}
                                                            rows={8}
                                                            className="bg-manus-secondary border-manus-border text-sm"
                                                        />
                                                        <div className="flex gap-2 justify-end">
                                                            <Button variant="ghost" size="sm" onClick={() => setEditingTemplate(null)}>取消</Button>
                                                            <Button size="sm" onClick={handleUpdateTemplate} className="bg-accent text-white">保存修改</Button>
                                                        </div>
                                                    </div>
                                                ) : (
                                                    <div className="flex flex-col h-full">
                                                        <div className="flex items-start justify-between gap-4 mb-3">
                                                            <div className="flex items-center gap-2 flex-wrap">
                                                                <div className="p-1.5 rounded bg-accent/10 text-accent flex-shrink-0">
                                                                    <Palette className="h-4 w-4" />
                                                                </div>
                                                                <span className="text-base font-semibold truncate">{template.name}</span>
                                                                <code className="px-1.5 py-0.5 rounded bg-manus/50 text-manus-muted text-xs border border-manus-border">
                                                                    {template.template_id}
                                                                </code>
                                                                {template.is_default && (
                                                                    <span className="px-1.5 py-0.5 rounded bg-green-500/10 text-green-500 text-xs border border-green-500/20">默认</span>
                                                                )}
                                                            </div>

                                                            <div className="flex items-center gap-1 opacity-100 lg:opacity-0 group-hover:opacity-100 transition-opacity">
                                                                {!template.is_default && (
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        onClick={() => setEditingTemplate(template)}
                                                                        className="h-8 w-8 hover:bg-accent/10 hover:text-accent"
                                                                    >
                                                                        <Edit2 className="h-4 w-4" />
                                                                    </Button>
                                                                )}
                                                                {!template.is_default && (
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        onClick={() => handleDeleteTemplate(template.template_id)}
                                                                        className="h-8 w-8 hover:bg-red-500/10 hover:text-red-500"
                                                                    >
                                                                        <Trash2 className="h-4 w-4" />
                                                                    </Button>
                                                                )}
                                                            </div>
                                                        </div>

                                                        <p className="text-sm text-manus-muted mb-4 line-clamp-1">{template.description}</p>

                                                        <div className="bg-manus-secondary/50 rounded-md p-3 border border-manus-border flex-1">
                                                            <p className="text-sm text-manus-subtle leading-relaxed whitespace-pre-wrap line-clamp-4">
                                                                {template.prompt}
                                                            </p>
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        ))
                                    )}
                                </div>
                            )}

                            {/* ================= 用户配置列表 ================= */}
                            {activeTab === 'users' && (
                                <div className="space-y-4">
                                    {/* 搜索栏 */}
                                    <div className="flex items-center gap-2 max-w-md">
                                        <div className="relative flex-1">
                                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-manus-muted" />
                                            <Input
                                                placeholder="搜索用户或部门..."
                                                value={searchQuery}
                                                onChange={(e) => setSearchQuery(e.target.value)}
                                                className="bg-manus-tertiary border-manus-border pl-9 h-9"
                                            />
                                        </div>
                                        {/* 部门筛选下拉框 */}
                                        <div className="w-[180px]">
                                            <Select value={selectedDept} onValueChange={setSelectedDept}>
                                                <SelectTrigger className="bg-manus-tertiary border-manus-border text-sm h-9">
                                                    <SelectValue placeholder="筛选部门" />
                                                </SelectTrigger>
                                                <SelectContent className="bg-manus-secondary border-manus-border">
                                                    <SelectItem value="all" className="focus:bg-manus-hover cursor-pointer">所有部门</SelectItem>
                                                    {departments.map(dept => (
                                                        <SelectItem key={dept.id} value={String(dept.id)} className="focus:bg-manus-hover cursor-pointer">
                                                            {dept.name}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>

                                    {isLoadingUsers ? (
                                        <div className="flex flex-col items-center justify-center py-20 text-manus-muted">
                                            <Loader2 className="h-8 w-8 animate-spin mb-2" />
                                            <span className="text-sm">加载用户列表中...</span>
                                        </div>
                                    ) : (
                                        <div className="border border-manus-border rounded-lg overflow-hidden bg-manus-tertiary">
                                            <table className="w-full">
                                                <thead className="bg-manus-secondary border-b border-manus-border">
                                                    <tr>
                                                        <th className="px-6 py-3 text-left text-xs font-semibold text-manus-muted uppercase tracking-wider">用户</th>
                                                        <th className="px-6 py-3 text-left text-xs font-semibold text-manus-muted uppercase tracking-wider">部门</th>
                                                        <th className="px-6 py-3 text-left text-xs font-semibold text-manus-muted uppercase tracking-wider">重试次数</th>
                                                        <th className="px-6 py-3 text-left text-xs font-semibold text-manus-muted uppercase tracking-wider">执行模式</th>
                                                        <th className="px-6 py-3 text-left text-xs font-semibold text-manus-muted uppercase tracking-wider">回复风格</th>
                                                        <th className="px-6 py-3 text-left text-xs font-semibold text-manus-muted uppercase tracking-wider">配置状态</th>
                                                        <th className="px-6 py-3 text-right text-xs font-semibold text-manus-muted uppercase tracking-wider">操作</th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-manus-border/50">
                                                    {users.length === 0 ? (
                                                        <tr>
                                                            <td colSpan={7} className="px-6 py-12 text-center text-manus-muted text-sm">
                                                                暂无匹配数据
                                                            </td>
                                                        </tr>
                                                    ) : users.map(user => (
                                                        <tr key={user.user_id} className="hover:bg-manus-hover/50 transition-colors">
                                                            <td className="px-6 py-4 whitespace-nowrap">
                                                                <div className="flex items-center gap-3">
                                                                    <div className="h-8 w-8 rounded-full bg-accent/10 flex items-center justify-center text-accent">
                                                                        <Users className="h-4 w-4" />
                                                                    </div>
                                                                    <div className="flex flex-col">
                                                                        <span className="text-sm font-medium text-manus-text">{user.username}</span>
                                                                    </div>
                                                                </div>
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap">
                                                                <div className="flex items-center gap-2 text-manus-muted text-sm">
                                                                    <Building2 className="h-3.5 w-3.5" />
                                                                    <span>{user.department || '未分配'}</span>
                                                                </div>
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-manus-muted font-mono">{user.max_retries}</td>
                                                            <td className="px-6 py-4 whitespace-nowrap">
                                                                <span className={cn(
                                                                    "px-2 py-0.5 rounded text-xs border",
                                                                    user.execution_mode === 'auto' ? "bg-blue-500/10 text-blue-500 border-blue-500/20" : "bg-manus/50 text-manus-muted border-manus-border"
                                                                )}>
                                                                    {user.execution_mode}
                                                                </span>
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-manus-muted">{user.synthesizer_template}</td>
                                                            <td className="px-6 py-4 whitespace-nowrap">
                                                                {user.has_custom_config ? (
                                                                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-accent/10 text-accent ring-1 ring-inset ring-accent/20">
                                                                        已自定义
                                                                    </span>
                                                                ) : (
                                                                    <span className="text-xs text-manus-subtle">默认</span>
                                                                )}
                                                            </td>
                                                            <td className="px-6 py-4 whitespace-nowrap text-right text-sm">
                                                                <div className="flex items-center justify-end gap-2">
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        onClick={() => openUserConfigDialog(user)}
                                                                        className="h-8 w-8 hover:bg-manus-hover hover:text-manus-text"
                                                                        title="编辑配置"
                                                                    >
                                                                        <Edit2 className="h-4 w-4" />
                                                                    </Button>
                                                                    {user.has_custom_config && (
                                                                        <Button
                                                                            variant="ghost"
                                                                            size="icon"
                                                                            onClick={() => handleResetUserConfig(user.user_id, user.username)}
                                                                            className="h-8 w-8 hover:bg-manus-hover hover:text-yellow-500"
                                                                            title="重置为系统默认"
                                                                        >
                                                                            <RotateCcw className="h-4 w-4" />
                                                                        </Button>
                                                                    )}
                                                                </div>
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    </ScrollArea>
                </CardContent>
            </Card>

            {/* 用户配置对话框 */}
            <Dialog open={userConfigDialog} onOpenChange={setUserConfigDialog}>
                <DialogContent className="bg-manus-secondary border-manus-border text-manus-text max-w-2xl">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2 text-xl">
                            <Bot className="h-5 w-5 text-accent" />
                            配置智能体参数
                        </DialogTitle>
                        <div className="text-sm text-manus-muted">
                            正在配置用户: <span className="font-semibold text-manus-text">{selectedUser?.username}</span>
                        </div>
                    </DialogHeader>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6 py-6">
                        {/* 左列 */}
                        <div className="space-y-5">
                            <div className="space-y-2">
                                <label className="text-sm font-medium text-manus-text">最大重试次数</label>
                                <Select
                                    value={String(userConfigForm.max_retries)}
                                    onValueChange={v => setUserConfigForm({ ...userConfigForm, max_retries: Number(v) })}
                                >
                                    <SelectTrigger className="bg-manus-tertiary border-manus-border">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent className="bg-manus-secondary border-manus-border">
                                        {[0, 1, 2, 3, 4, 5].map(n => (
                                            <SelectItem key={n} value={String(n)} className="focus:bg-manus-hover cursor-pointer">
                                                {n} 次
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <p className="text-xs text-manus-subtle">
                                    智能体遇到错误时的最大自动重试次数。
                                </p>
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium text-manus-text">执行模式</label>
                                <Select
                                    value={userConfigForm.execution_mode}
                                    onValueChange={v => setUserConfigForm({ ...userConfigForm, execution_mode: v })}
                                >
                                    <SelectTrigger className="bg-manus-tertiary border-manus-border">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent className="bg-manus-secondary border-manus-border">
                                        <SelectItem value="auto" className="focus:bg-manus-hover cursor-pointer">自动 (Auto)</SelectItem>
                                        <SelectItem value="rag_only" className="focus:bg-manus-hover cursor-pointer">仅知识库 (RAG Only)</SelectItem>
                                        <SelectItem value="sql_only" className="focus:bg-manus-hover cursor-pointer">仅数据库 (SQL Only)</SelectItem>
                                        <SelectItem value="chart_only" className="focus:bg-manus-hover cursor-pointer">仅图表 (Chart Only)</SelectItem>
                                        <SelectItem value="office_only" className="focus:bg-manus-hover cursor-pointer">仅文档 (Office Only)</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>

                        {/* 右列 */}
                        <div className="space-y-5">
                            <div className="space-y-2">
                                <label className="text-sm font-medium text-manus-text">每次确认执行</label>
                                <div className="flex items-center justify-between p-3 h-10 bg-manus-tertiary border border-manus-border rounded-md">
                                    <div className="space-y-0.5">
                                        <p className="text-sm text-manus-text">执行前询问</p>
                                    </div>
                                    <label className="relative inline-flex items-center cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={userConfigForm.always_confirm}
                                            onChange={e => setUserConfigForm({ ...userConfigForm, always_confirm: e.target.checked })}
                                            className="sr-only peer"
                                        />
                                        <div className="w-9 h-5 bg-manus-border peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-accent"></div>
                                    </label>
                                </div>
                                <p className="text-xs text-manus-subtle">
                                    每次执行操作前都需要用户确认
                                </p>
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium text-manus-text">回复风格模板</label>
                                <Select
                                    value={userConfigForm.synthesizer_template}
                                    onValueChange={v => setUserConfigForm({ ...userConfigForm, synthesizer_template: v })}
                                >
                                    <SelectTrigger className="bg-manus-tertiary border-manus-border">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent className="bg-manus-secondary border-manus-border">
                                        <SelectItem value="default" className="focus:bg-manus-hover cursor-pointer text-manus-muted">系统默认</SelectItem>
                                        {templates.map(t => (
                                            <SelectItem key={t.template_id} value={t.template_id} className="focus:bg-manus-hover cursor-pointer">
                                                {t.name}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <p className="text-xs text-manus-subtle">
                                    控制智能体回复的语气和格式。
                                </p>
                            </div>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setUserConfigDialog(false)} className="hover:bg-manus-hover hover:text-manus-text">取消</Button>
                        <Button onClick={handleSaveUserConfig} className="bg-accent hover:bg-accent/90 text-white">保存配置</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
