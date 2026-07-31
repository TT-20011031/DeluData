/**
 * 侧边栏组件
 *
 * 导航菜单和会话列表
 */
import { useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Plus, MessageSquare, Trash2, FolderOpen, Database, Shield, ShieldCheck, Settings2, LogOut, User, Moon, Sun, Bot, Sparkles, Settings, Building2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useChatStore } from '@/stores/chatStore'
import { useAuthStore } from '@/stores/authStore'
import { useLayoutStore } from '@/stores/layoutStore'
import { APP_CONFIG } from '@/config'
import { cn } from '@/lib/utils'

// 导航项 - 添加权限控制
const navItems = [
    { path: '/', icon: MessageSquare, label: '对话', requiredPermissions: ['chat:use'] },
    { path: '/knowledge', icon: FolderOpen, label: '知识库', requiredPermissions: ['knowledge:view', 'knowledge:manage'] },
    { path: '/database', icon: Database, label: '数据库', requiredPermissions: ['database:view', 'database:query'] },
    { path: '/extend-config', icon: Settings2, label: '扩展配置', requiredPermissions: ['config:view', 'config:manage'] },
    { path: '/agent-config', icon: Bot, label: '智能体配置', requiredPermissions: ['config:view', 'config:manage'] },
    { path: '/extend-scenes', icon: Sparkles, label: '扩展场景', requiredPermissions: ['museum:guide', 'museum:shop'] },
]

// 管理菜单 - 添加权限控制
const adminItems = [
    { path: '/admin/organization-accounts', icon: Building2, label: '组织与账号管理', requiredPermissions: ['org:view', 'org:manage'] },
    { path: '/admin/roles', icon: Shield, label: '角色管理', requiredPermissions: ['role:view', 'role:manage', 'authorization:view', 'authorization:manage'] },
    { path: '/admin/science', icon: Sparkles, label: '科技馆管理', requiredPermissions: ['config:manage'] },
    { path: '/database/semantic-governance?workspace=access-policies', icon: ShieldCheck, label: '问数数据权限', requiredPermissions: ['semantic_access:view', 'semantic_access:manage'] },
    { path: '/system-settings', icon: Settings, label: '智能体全局设置', requiredPermissions: ['config:manage'] },
]

export default function Sidebar({ collapsed = false }: { collapsed?: boolean }) {
    const navigate = useNavigate()
    const location = useLocation()
    const { user, logout } = useAuthStore()
    const { theme, toggleThemeWithAnimation } = useLayoutStore()
    const workspaceFeatures = user?.workspace_features || {}

    const {
        sessions,
        currentSessionId,
        setCurrentSession,
        createSession,
        deleteSession,
        fetchSessions,
    } = useChatStore()

    // Fetch sessions on mount
    useEffect(() => {
        fetchSessions()
    }, [fetchSessions])

    const sessionList = Object.values(sessions).sort(
        (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
    )

    const handleNewChat = () => {
        createSession()
        navigate('/')
    }

    const handleSelectSession = (sessionId: string) => {
        setCurrentSession(sessionId)
        navigate('/')
    }

    const handleDeleteSession = (e: React.MouseEvent, sessionId: string) => {
        e.stopPropagation()
        deleteSession(sessionId)
    }

    const handleLogout = () => {
        logout()
        navigate('/login')
    }

    const isActive = (path: string) => {
        if (path === '/') {
            return location.pathname === '/' || location.pathname.startsWith('/chat')
        }
        const [pathname, search] = path.split('?')
        if (location.pathname !== pathname) return false
        return !search || location.search.slice(1) === search
    }

    const isFeatureVisible = (path: string) => {
        if (path === '/extend-scenes' || path.startsWith('/museum')) {
            return Boolean(workspaceFeatures.museum_enabled)
        }
        if (path === '/admin/science') {
            return Boolean(workspaceFeatures.kiosk_enabled)
        }
        return true
    }

    // 检查用户是否拥有某菜单的任意一个权限
    const hasAnyPermission = (requiredPermissions: string[]) => {
        if (requiredPermissions.length === 0) return true
        // 检查用户权限列表是否包含任意一个所需权限
        const userPerms = user?.permissions || []
        return requiredPermissions.some(perm => userPerms.includes(perm))
    }

    // 过滤导航菜单
    const filteredNavItems = navItems.filter(item => hasAnyPermission(item.requiredPermissions) && isFeatureVisible(item.path))

    // 过滤管理菜单
    const filteredAdminItems = adminItems.filter(item => hasAnyPermission(item.requiredPermissions) && isFeatureVisible(item.path))

    return (
        <TooltipProvider>
            <aside className={cn(
                'h-full bg-manus-secondary border-r border-manus-border flex flex-col transition-all duration-300',
                collapsed ? 'w-16' : 'w-64'
            )}>
                {/* Logo */}
                <div className="h-14 flex items-center justify-start border-b border-manus-border px-4">
                    <div className="flex items-center gap-2 overflow-hidden">
                        <div className="w-8 h-8 rounded-lg bg-accent/20 flex items-center justify-center shrink-0">
                            <span className="text-accent font-semibold text-sm">D</span>
                        </div>
                        {!collapsed && <span className="text-manus-text font-medium truncate">{APP_CONFIG.name}</span>}
                    </div>
                </div>

                {/* 导航菜单 */}
                <div className="p-3 space-y-1">
                    {filteredNavItems.map((item) => {
                        const ButtonComp = (
                            <Button
                                variant="ghost"
                                onClick={() => navigate(item.path)}
                                className={cn(
                                    'w-full gap-2 text-manus-muted hover:text-manus-text hover:bg-manus-hover',
                                    collapsed ? 'justify-center px-0 h-10 w-10 mx-auto' : 'justify-start',
                                    isActive(item.path) && 'bg-manus-hover text-manus-text'
                                )}
                            >
                                <item.icon className="h-4 w-4 shrink-0" />
                                {!collapsed && <span>{item.label}</span>}
                            </Button>
                        )

                        if (collapsed) {
                            return (
                                <Tooltip key={item.path}>
                                    <TooltipTrigger asChild>
                                        {ButtonComp}
                                    </TooltipTrigger>
                                    <TooltipContent side="right">{item.label}</TooltipContent>
                                </Tooltip>
                            )
                        }

                        return (
                            <div key={item.path}>
                                {ButtonComp}
                            </div>
                        )
                    })}
                </div>

                {/* 分隔线 */}
                <div className="px-4">
                    <div className="h-px bg-manus-border" />
                </div>

                {/* 新建对话按钮 */}
                <div className="p-3">
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                onClick={handleNewChat}
                                variant="outline"
                                className={cn(
                                    'w-full gap-2 bg-manus-tertiary border-manus-border hover:bg-manus-hover hover:border-manus-border-strong text-manus-text',
                                    collapsed ? 'justify-center px-0 h-10 w-10 mx-auto' : 'justify-start'
                                )}
                            >
                                <Plus className="h-4 w-4 shrink-0" />
                                {!collapsed && '新建对话'}
                            </Button>
                        </TooltipTrigger>
                        {collapsed && <TooltipContent side="right">新建对话</TooltipContent>}
                    </Tooltip>
                </div>

                {/* 会话列表 - 仅在非折叠时显示 */}
                {!collapsed && (
                    <ScrollArea className="flex-1 px-2">
                        <div className="space-y-1 pb-4">
                            {sessionList.length === 0 ? (
                                <div className="text-center text-manus-subtle py-8 text-sm">
                                    暂无对话
                                </div>
                            ) : (
                                sessionList.map((session) => (
                                    <div
                                        key={session.id}
                                        onClick={() => handleSelectSession(session.id)}
                                        className={cn(
                                            'group flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition-all duration-150',
                                            currentSessionId === session.id
                                                ? 'bg-manus-hover border border-manus-border-strong'
                                                : 'hover:bg-manus-hover/50 border border-transparent'
                                        )}
                                    >
                                        <MessageSquare className="h-4 w-4 text-manus-muted shrink-0" />
                                        <div className="flex-1 min-w-0 max-w-[150px] text-sm text-manus-text truncate" title={session.title}>
                                            {session.title}
                                        </div>
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-6 w-6 shrink-0 opacity-0 group-hover:opacity-100 hover:bg-manus-tertiary hover:text-error"
                                                    onClick={(e) => handleDeleteSession(e, session.id)}
                                                >
                                                    <Trash2 className="h-3.5 w-3.5" />
                                                </Button>
                                            </TooltipTrigger>
                                            <TooltipContent side="right">
                                                <p>删除对话</p>
                                            </TooltipContent>
                                        </Tooltip>
                                    </div>
                                ))
                            )}
                        </div>
                    </ScrollArea>
                )}

                {/* 占位符 */}
                {collapsed && <div className="flex-1" />}

                {/* 用户信息和登出 */}
                <div className="p-3 border-t border-manus-border space-y-2">
                    {user && !collapsed && (
                        <div className="flex items-center gap-2 px-2 py-1.5 text-sm">
                            <User className="h-4 w-4 text-manus-muted" />
                            <span className="text-manus-text flex-1 truncate">{user.username}</span>
                            <span className="text-xs px-1.5 py-0.5 bg-manus-tertiary text-manus-muted rounded">
                                {user.permissions?.includes('authorization:manage') || user.permissions?.includes('*') ? '权限管理员' : '用户'}
                            </span>
                        </div>
                    )}
                    {/* 设置按钮下拉菜单 (有管理权限时显示) */}
                    {filteredAdminItems.length > 0 && (
                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <Button
                                    variant="ghost"
                                    className={cn(
                                        'w-full gap-2 text-manus-muted hover:text-manus-text hover:bg-manus-hover',
                                        collapsed ? 'justify-center px-0 h-10 w-10 mx-auto' : 'justify-start'
                                    )}
                                >
                                    <Settings2 className="h-4 w-4 shrink-0" />
                                    {!collapsed && <span>系统管理</span>}
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" side="top" className="w-48 bg-manus-secondary border-manus-border">
                                <DropdownMenuLabel className="text-manus-muted">系统管理</DropdownMenuLabel>
                                <DropdownMenuSeparator className="bg-manus-border" />
                                {filteredAdminItems.map((item) => (
                                    <DropdownMenuItem
                                        key={item.path}
                                        onClick={() => navigate(item.path)}
                                        className="gap-2 cursor-pointer hover:bg-manus-hover"
                                    >
                                        <item.icon className="h-4 w-4" />
                                        {item.label}
                                    </DropdownMenuItem>
                                ))}
                            </DropdownMenuContent>
                        </DropdownMenu>
                    )}
                    {/* 主题切换按钮 */}
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="ghost"
                                onClick={(e) => toggleThemeWithAnimation(e)}
                                className={cn(
                                    'w-full gap-2 text-manus-muted hover:text-manus-text hover:bg-manus-hover',
                                    collapsed ? 'justify-center px-0 h-10 w-10 mx-auto' : 'justify-start'
                                )}
                            >
                                {theme === 'dark' ? (
                                    <Sun className="h-4 w-4 shrink-0" />
                                ) : (
                                    <Moon className="h-4 w-4 shrink-0" />
                                )}
                                {!collapsed && (theme === 'dark' ? '浅色模式' : '深色模式')}
                            </Button>
                        </TooltipTrigger>
                        {collapsed && <TooltipContent side="right">{theme === 'dark' ? '浅色模式' : '深色模式'}</TooltipContent>}
                    </Tooltip>
                    {/* 退出登录按钮 */}
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="ghost"
                                onClick={handleLogout}
                                className={cn(
                                    'w-full gap-2 text-manus-muted hover:text-error hover:bg-error/10',
                                    collapsed ? 'justify-center px-0 h-10 w-10 mx-auto' : 'justify-start'
                                )}
                            >
                                <LogOut className="h-4 w-4 shrink-0" />
                                {!collapsed && '退出登录'}
                            </Button>
                        </TooltipTrigger>
                        {collapsed && <TooltipContent side="right">退出登录</TooltipContent>}
                    </Tooltip>
                    {!collapsed && (
                        <div className="text-xs text-manus-subtle text-center">
                            v{APP_CONFIG.version}
                        </div>
                    )}
                </div>
            </aside>
        </TooltipProvider>
    )
}
