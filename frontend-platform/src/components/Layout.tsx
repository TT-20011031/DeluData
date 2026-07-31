import { useState, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
    LayoutDashboard,
    Users,
    Building2,
    HardDrive,
    ShieldCheck,
    Settings,
    LogOut,
    Menu,
    X,
    ChevronRight
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface NavItem {
    label: string;
    path: string;
    icon: React.ReactNode;
}

const navItems: NavItem[] = [
    { label: '仪表盘', path: '/dashboard', icon: <LayoutDashboard size={20} /> },
    { label: '租户管理', path: '/tenants', icon: <Building2 size={20} /> },
    { label: '知识库治理', path: '/knowledge-governance', icon: <HardDrive size={20} /> },
    { label: 'SQL白名单', path: '/db-whitelist', icon: <ShieldCheck size={20} /> },
    { label: '平台管理员', path: '/admins', icon: <Users size={20} /> },
    { label: '系统设置', path: '/settings', icon: <Settings size={20} /> },
];


export default function Layout({ children }: { children: React.ReactNode }) {
    const [sidebarOpen, setSidebarOpen] = useState(true);
    const [adminUser, setAdminUser] = useState<{ username: string } | null>(null);
    const location = useLocation();
    const navigate = useNavigate();

    useEffect(() => {
        const stored = localStorage.getItem('admin_user');
        if (stored) {
            try {
                setAdminUser(JSON.parse(stored));
            } catch {
                setAdminUser(null);
            }
        }
    }, []);

    const handleLogout = () => {
        localStorage.removeItem('platform_token');
        localStorage.removeItem('admin_user');
        navigate('/login');
    };

    const isActive = (path: string) => location.pathname === path;

    return (
        <div className="flex min-h-screen bg-manus">
            {/* 侧边栏 */}
            <aside
                className={cn(
                    "fixed h-screen z-50 flex flex-col transition-all duration-200 sidebar-dark",
                    sidebarOpen ? "w-60" : "w-16"
                )}
            >
                {/* Logo 区域 */}
                <div className="h-16 flex items-center px-4 border-b border-sidebar justify-between"
                    style={{ borderColor: '#27272a' }}>
                    {sidebarOpen && (
                        <span className="font-bold text-lg text-white">平台管理</span>
                    )}
                    <button
                        onClick={() => setSidebarOpen(!sidebarOpen)}
                        className="p-2 rounded-lg hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors"
                    >
                        {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
                    </button>
                </div>

                {/* 导航菜单 */}
                <nav className="flex-1 p-2 space-y-1">
                    {navItems.map((item) => (
                        <Link
                            key={item.path}
                            to={item.path}
                            className={cn(
                                "flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-150",
                                isActive(item.path)
                                    ? "bg-blue-500 text-white"
                                    : "text-zinc-400 hover:bg-zinc-800 hover:text-white"
                            )}
                        >
                            {item.icon}
                            {sidebarOpen && <span>{item.label}</span>}
                            {sidebarOpen && isActive(item.path) && (
                                <ChevronRight size={16} className="ml-auto" />
                            )}
                        </Link>
                    ))}
                </nav>

                {/* 用户信息 & 登出 */}
                <div className="p-4 border-t" style={{ borderColor: '#27272a' }}>
                    {sidebarOpen && adminUser && (
                        <div className="mb-3 text-sm text-zinc-400">
                            已登录: <span className="text-white">{adminUser.username}</span>
                        </div>
                    )}
                    <button
                        onClick={handleLogout}
                        className={cn(
                            "flex items-center gap-3 w-full px-3 py-2.5 rounded-lg",
                            "bg-zinc-800 text-red-400 hover:bg-zinc-700 transition-colors"
                        )}
                    >
                        <LogOut size={20} />
                        {sidebarOpen && <span>退出登录</span>}
                    </button>
                </div>
            </aside>

            {/* 主内容区 */}
            <main
                className={cn(
                    "flex-1 p-6 transition-all duration-200",
                    sidebarOpen ? "ml-60" : "ml-16"
                )}
            >
                {children}
            </main>
        </div>
    );
}
