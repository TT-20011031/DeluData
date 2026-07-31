/**
 * 路由配置
 * 
 * 包含认证守卫和所有页面路由
 */
import { createBrowserRouter, Navigate, type RouteObject } from 'react-router-dom'
import MainLayout from '@/layouts/MainLayout'
import ChatPage from '@/pages/ChatPage'
import KnowledgeBasePage from '@/pages/KnowledgeBasePage'
import LoginPage from '@/pages/LoginPage'
import MaintenanceRecordsPage from '@/pages/MaintenanceRecordsPage'
import WorkspaceDisabledPage from '@/pages/WorkspaceDisabledPage'
import UnauthorizedPage from '@/pages/UnauthorizedPage'
import ExtendConfigPage from '@/pages/ExtendConfigPage'
import TemplateEditorPage from '@/pages/TemplateEditorPage'
import { useAuthStore } from '@/stores/authStore'

// ========== 路由守卫 ==========

/**
 * 认证守卫
 * 
 * 检查用户是否已登录，未登录则重定向到登录页
 */
function AuthGuard({ children }: { children: React.ReactNode }) {
    const { isAuthenticated } = useAuthStore()

    if (!isAuthenticated) {
        return <Navigate to="/login" replace />
    }

    return <>{children}</>
}

/**
 * 访客守卫
 * 
 * 已登录用户访问登录页时重定向到首页
 */
function GuestGuard({ children }: { children: React.ReactNode }) {
    const { isAuthenticated } = useAuthStore()

    if (isAuthenticated) {
        return <Navigate to="/" replace />
    }

    return <>{children}</>
}

// 导出供其他模块使用
export { AuthGuard, GuestGuard }

function WorkspaceFeatureGuard({
    feature,
    children,
}: {
    feature: 'museum' | 'kiosk'
    children: React.ReactNode
}) {
    const { user } = useAuthStore()
    const features = user?.workspace_features || {}
    const enabled = feature === 'museum' ? Boolean(features.museum_enabled) : Boolean(features.kiosk_enabled)

    if (!enabled) {
        return <Navigate to="/unauthorized" replace />
    }
    return <>{children}</>
}

// ========== 路由定义 ==========

const routes: RouteObject[] = [
    // 登录页
    {
        path: '/login',
        element: (
            <GuestGuard>
                <LoginPage />
            </GuestGuard>
        ),
    },
    {
        path: '/unauthorized',
        element: <UnauthorizedPage />,
    },
    {
        path: '/disabled',
        element: <WorkspaceDisabledPage />,
    },
    {
        path: '/maintenance-capture',
        element: <MaintenanceRecordsPage />,
    },

    // 主应用
    {
        path: '/',
        element: (
            <AuthGuard>
                <MainLayout />
            </AuthGuard>
        ),
        children: [
            {
                index: true,
                element: <ChatPage />,
            },
            {
                path: 'chat',
                element: <ChatPage />,
            },
            {
                path: 'chat/:sessionId',
                element: <ChatPage />,
            },
            {
                path: 'knowledge',
                element: <KnowledgeBasePage />,
            },
            {
                path: 'database',
                lazy: async () => {
                    const { default: DatabaseConfigPage } = await import('@/pages/DatabaseConfigPage')
                    return { Component: DatabaseConfigPage }
                },
            },
            {
                path: 'permissions',
                element: <Navigate to="/database/semantic-governance?workspace=access-policies" replace />,
            },
            {
                path: 'extend-config',
                element: <ExtendConfigPage />,
            },
            {
                path: 'database/semantic-governance',
                lazy: async () => {
                    const { default: SemanticGovernancePage } = await import('@/pages/SemanticGovernancePage')
                    return { Component: SemanticGovernancePage }
                },
            },
            // 模板编辑独立页面（new 为新建，数字 ID 为编辑）
            {
                path: 'extend-config/templates/:templateId',
                element: <TemplateEditorPage />,
            },
            {
                path: 'admin/users',
                element: <Navigate to="/admin/organization-accounts" replace />,
            },
            {
                path: 'admin/roles',
                lazy: async () => {
                    const { default: AuthorizationRolesPage } = await import('@/pages/AuthorizationRolesPage')
                    return { Component: AuthorizationRolesPage }
                },
            },
            {
                path: 'admin/departments',
                element: <Navigate to="/admin/organization-accounts" replace />,
            },
            {
                path: 'admin/authorization',
                element: <Navigate to="/admin/organization-accounts" replace />,
            },
            {
                path: 'admin/organization-accounts',
                lazy: async () => {
                    const { default: OrganizationAccountsPage } = await import('@/pages/OrganizationAccountsPage')
                    return { Component: OrganizationAccountsPage }
                },
            },
            {
                path: 'admin/science',
                lazy: async () => {
                    const { default: ScienceAdminPage } = await import('@/pages/science-admin/ScienceAdminPage')
                    return {
                        Component: () => (
                            <WorkspaceFeatureGuard feature="kiosk">
                                <ScienceAdminPage />
                            </WorkspaceFeatureGuard>
                        ),
                    }
                },
            },
            {
                path: 'agent-config',
                lazy: async () => {
                    const { default: AgentConfigPage } = await import('@/pages/AgentConfigPage')
                    return { Component: AgentConfigPage }
                },
            },
            {
                path: 'system-settings',
                lazy: async () => {
                    const { default: SystemSettingsPage } = await import('@/pages/SystemSettingsPage')
                    return { Component: SystemSettingsPage }
                },
            },
            // ========== 扩展场景入口 ==========
            {
                path: 'extend-scenes',
                lazy: async () => {
                    const { default: ExtendScenesPage } = await import('@/pages/ExtendScenesPage')
                    return {
                        Component: () => (
                            <WorkspaceFeatureGuard feature="museum">
                                <ExtendScenesPage />
                            </WorkspaceFeatureGuard>
                        ),
                    }
                },
            },
            // ========== 博物馆智能导览模块（从扩展场景进入） ==========
            {
                path: 'museum/guide',
                lazy: async () => {
                    const { default: GuidePage } = await import('@/museum/pages/GuidePage')
                    return {
                        Component: () => (
                            <WorkspaceFeatureGuard feature="museum">
                                <GuidePage />
                            </WorkspaceFeatureGuard>
                        ),
                    }
                },
            },
            {
                path: 'museum/shop',
                lazy: async () => {
                    const { default: ShopPage } = await import('@/museum/pages/ShopPage')
                    return {
                        Component: () => (
                            <WorkspaceFeatureGuard feature="museum">
                                <ShopPage />
                            </WorkspaceFeatureGuard>
                        ),
                    }
                },
            },
        ],
    },

    // 404
    {
        path: '*',
        element: <Navigate to="/" replace />,
    },
]

export const router = createBrowserRouter(routes)
