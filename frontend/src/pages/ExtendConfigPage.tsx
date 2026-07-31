import { useState, useMemo, useEffect } from 'react'
import { Navigate, useSearchParams } from 'react-router-dom'
import { Settings2, Code, FileText, BookOpen, Palette } from 'lucide-react'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { StyledTabsNav, type StyledTabItem } from '@/components/ui/styled-tabs'
import { useAuthStore } from '@/stores/authStore'
import { SqlExampleManager, TemplateManager, SkillManager, DocStyleManager } from '@/components/extendConfig'

export default function ExtendConfigPage() {
    const { user: currentUser } = useAuthStore()
    const [searchParams, setSearchParams] = useSearchParams()

    // 从 URL 参数获取 tab，默认为 'sql'
    const defaultTab = searchParams.get('tab') || 'sql'
    const [activeTab, setActiveTab] = useState(defaultTab)

    // 当 activeTab 改变时更新 URL
    useEffect(() => {
        setSearchParams(prev => {
            prev.set('tab', activeTab)
            return prev
        }, { replace: true })
    }, [activeTab, setSearchParams])

    // 权限检查
    if (!currentUser?.permissions?.some((code) => code === '*' || code === 'config:manage')) {
        return <Navigate to="/" replace />
    }

    const tabItems: StyledTabItem[] = useMemo(() => [
        {
            value: 'sql',
            label: 'SQL 示例',
            icon: <Code className="h-4 w-4" />,
        },
        {
            value: 'templates',
            label: '模板管理',
            icon: <FileText className="h-4 w-4" />,
        },
        {
            value: 'skills',
            label: 'DeluSkills',
            icon: <BookOpen className="h-4 w-4" />,
        },
        {
            value: 'doc-styles',
            label: '文档样式',
            icon: <Palette className="h-4 w-4" />,
        },
    ], [])

    return (
        <div className="flex-1 h-full min-h-0 bg-manus overflow-y-auto p-6">
            <div className="max-w-5xl mx-auto w-full space-y-6 pb-10">
                {/* 标题 */}
                <div className="flex items-center justify-between">
                    <div>
                        <h1 className="text-2xl font-semibold text-manus-text flex items-center gap-2">
                            <Settings2 className="h-6 w-6 text-accent" />
                            扩展配置
                        </h1>
                        <p className="text-manus-muted mt-1">
                            管理 SQL 示例、文档模板、DeluSkills 和文档样式
                        </p>
                    </div>
                </div>

                {/* Tabs */}
                <Tabs value={activeTab} onValueChange={setActiveTab}>
                    <StyledTabsNav
                        items={tabItems}
                        onValueChange={setActiveTab}
                    />

                    <TabsContent value="sql" className="mt-6">
                        <SqlExampleManager />
                    </TabsContent>

                    <TabsContent value="templates" className="mt-6">
                        <TemplateManager />
                    </TabsContent>

                    <TabsContent value="skills" className="mt-6">
                        <SkillManager />
                    </TabsContent>

                    <TabsContent value="doc-styles" className="mt-6">
                        <DocStyleManager />
                    </TabsContent>
                </Tabs>
            </div>
        </div>
    )
}

