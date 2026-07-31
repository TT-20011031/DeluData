/**
 * 智能体配置页面（用户个人配置）
 * 
 * 模块化设计，遵循设计规范：
 * - 组件抽取：BehaviorSettings, StyleSettings
 * - 视觉透气：增加间距、轻量背景
 * - 异步优先：所有 API 调用使用 async/await
 * - 用户级配置：调用 /config/agent/me API
 */
import { useState, useEffect } from 'react'
import { Navigate } from 'react-router-dom'
import { Bot, Save, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/authStore'
import { useToast } from '@/components/ui/toast'
import { API_BASE_URL } from '@/config'

import { BehaviorSettings, StyleSettings } from '@/components/agent-config'
import type { TemplateListResponse } from '@/components/agent-config'

const MAX_CUSTOM_PROMPT_LENGTH = 500

export default function AgentConfigPage() {
    const { user: currentUser, token } = useAuthStore()
    const { toast } = useToast()

    // 加载状态
    const [isLoading, setIsLoading] = useState(true)
    const [isSaving, setIsSaving] = useState(false)

    // 行为配置
    const [maxRetries, setMaxRetries] = useState(2)
    const [alwaysConfirm, setAlwaysConfirm] = useState(false)

    // 风格配置
    const [synthesizerTemplate, setSynthesizerTemplate] = useState('default')
    const [synthesizerCustomPrompt, setSynthesizerCustomPrompt] = useState('')
    const [templates, setTemplates] = useState<TemplateListResponse>({
        system_templates: [],
        user_templates: []
    })

    // 权限检查
    if (!currentUser?.permissions?.some((code) => code === '*' || code === 'config:manage')) {
        return <Navigate to="/" replace />
    }

    // 加载配置
    useEffect(() => {
        const loadData = async () => {
            try {
                const [configRes, templatesRes] = await Promise.all([
                    fetch(`${API_BASE_URL}/config/agent/me`, {
                        headers: { 'Authorization': `Bearer ${token}` }
                    }),
                    fetch(`${API_BASE_URL}/config/agent/synthesizer-templates`, {
                        headers: { 'Authorization': `Bearer ${token}` }
                    })
                ])
                if (configRes.ok) {
                    const data = await configRes.json()
                    setMaxRetries(data.max_retries)
                    setAlwaysConfirm(data.always_confirm ?? false)
                    setSynthesizerTemplate(data.synthesizer_template || 'default')
                    setSynthesizerCustomPrompt(data.synthesizer_custom_prompt || '')
                }
                if (templatesRes.ok) {
                    setTemplates(await templatesRes.json())
                }
            } catch (error) {
                toast({ type: 'error', title: '加载失败', description: '无法加载配置' })
            } finally {
                setIsLoading(false)
            }
        }
        loadData()
    }, [token, toast])

    // 保存配置
    const handleSave = async () => {
        if (synthesizerCustomPrompt.length > MAX_CUSTOM_PROMPT_LENGTH) {
            toast({ type: 'error', title: '保存失败', description: '自定义 Prompt 超出字数限制' })
            return
        }
        setIsSaving(true)
        try {
            const response = await fetch(`${API_BASE_URL}/config/agent/me`, {
                method: 'PUT',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    max_retries: maxRetries,
                    always_confirm: alwaysConfirm,
                    synthesizer_template: synthesizerTemplate,
                    synthesizer_custom_prompt: synthesizerCustomPrompt
                })
            })
            if (response.ok) {
                toast({ type: 'success', title: '保存成功', description: '配置已更新' })
            } else {
                throw new Error()
            }
        } catch {
            toast({ type: 'error', title: '保存失败', description: '无法保存配置' })
        } finally {
            setIsSaving(false)
        }
    }

    // 恢复默认
    const handleResetToDefault = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/config/agent/me/reset`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` }
            })
            if (response.ok) {
                const data = await response.json()
                setMaxRetries(data.max_retries)
                setAlwaysConfirm(data.always_confirm)
                setSynthesizerTemplate(data.synthesizer_template)
                setSynthesizerCustomPrompt(data.synthesizer_custom_prompt)
                toast({ type: 'success', title: '已重置', description: '配置已恢复为默认值' })
            }
        } catch {
            toast({ type: 'error', title: '重置失败', description: '无法恢复默认' })
        }
    }

    // 创建模板
    const handleCreateTemplate = async (name: string, desc: string, prompt: string) => {
        const response = await fetch(`${API_BASE_URL}/config/agent/synthesizer-templates`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name, description: desc, prompt })
        })
        if (response.ok) {
            const newTemplate = await response.json()
            setTemplates(prev => ({
                ...prev,
                user_templates: [{ ...newTemplate, is_system: false }, ...prev.user_templates]
            }))
            setSynthesizerTemplate(newTemplate.template_id)
            toast({ type: 'success', title: '创建成功', description: `模板"${name}"已创建` })
        } else {
            toast({ type: 'error', title: '创建失败', description: '无法创建模板' })
        }
    }

    // 删除模板
    const handleDeleteTemplate = async (templateId: string) => {
        try {
            const response = await fetch(`${API_BASE_URL}/config/agent/synthesizer-templates/${templateId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` }
            })
            if (response.ok) {
                setTemplates(prev => ({
                    ...prev,
                    user_templates: prev.user_templates.filter(t => t.template_id !== templateId)
                }))
                if (synthesizerTemplate === templateId) {
                    setSynthesizerTemplate('default')
                }
                toast({ type: 'success', title: '删除成功', description: '模板已删除' })
            }
        } catch {
            toast({ type: 'error', title: '删除失败', description: '无法删除模板' })
        }
    }

    return (
        <div className="flex-1 flex flex-col h-full bg-manus overflow-y-auto">
            {/* 顶部导航栏 */}
            <div className="sticky top-0 z-10 bg-manus/80 backdrop-blur-sm border-b border-manus-border/50">
                <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2 rounded-lg bg-accent/10">
                            <Bot className="h-5 w-5 text-accent" />
                        </div>
                        <div>
                            <h1 className="text-lg font-semibold text-manus-text">智能体配置</h1>
                            <p className="text-xs text-manus-muted">管理个人的行为和回复风格设置</p>
                        </div>
                    </div>
                    <Button onClick={handleSave} disabled={isSaving || isLoading} size="sm" className="gap-2">
                        {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                        {isSaving ? '保存中...' : '保存'}
                    </Button>
                </div>
            </div>

            {/* 内容区域 - 增加上下间距 */}
            <div className="flex-1 px-6 py-8">
                <div className="max-w-4xl mx-auto space-y-8">
                    {/* 行为配置 - 2 列网格 */}
                    <section>
                        <h2 className="text-sm font-medium text-manus-muted uppercase tracking-wide mb-4">行为配置</h2>
                        <BehaviorSettings
                            maxRetries={maxRetries}
                            setMaxRetries={setMaxRetries}
                            alwaysConfirm={alwaysConfirm}
                            setAlwaysConfirm={setAlwaysConfirm}
                            isLoading={isLoading}
                        />
                    </section>

                    {/* 回复风格 */}
                    <section>
                        <h2 className="text-sm font-medium text-manus-muted uppercase tracking-wide mb-4">回复风格</h2>
                        <StyleSettings
                            templates={templates}
                            selectedTemplateId={synthesizerTemplate}
                            setSelectedTemplateId={setSynthesizerTemplate}
                            customPrompt={synthesizerCustomPrompt}
                            setCustomPrompt={setSynthesizerCustomPrompt}
                            maxPromptLength={MAX_CUSTOM_PROMPT_LENGTH}
                            isLoading={isLoading}
                            onResetToDefault={handleResetToDefault}
                            onCreateTemplate={handleCreateTemplate}
                            onDeleteTemplate={handleDeleteTemplate}
                        />
                    </section>
                </div>
            </div>
        </div>
    )
}
