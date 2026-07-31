/**
 * 模板数据管理 Hook
 * 
 * 职责：
 * - 管理模板和模板分组数据
 * - 处理 CRUD 操作
 * - 管理筛选状态
 */
import { useState, useCallback, useEffect, useMemo } from 'react'
import { useToast } from '@/components/ui/toast'
import { extendConfigService } from '@/services/extendConfigService'
import type {
    Template,
    TemplateGroup,
    TemplateForm,
    TemplateGroupForm,
    VariableItem,
} from '@/types/extendConfig'

export interface UseTemplatesReturn {
    // 数据
    templates: Template[]
    groups: TemplateGroup[]
    filteredTemplates: Template[]

    // 加载状态
    isLoading: boolean
    isSaving: boolean
    isTestingRender: boolean

    // 筛选
    filterGroupId: number | 'all'
    setFilterGroupId: (id: number | 'all') => void

    // CRUD 操作
    refresh: () => void
    createTemplate: (data: TemplateForm, file: File, variables: VariableItem[]) => Promise<number | null>
    updateTemplate: (id: number, data: TemplateForm, variables: VariableItem[]) => Promise<boolean>
    deleteTemplate: (id: number) => Promise<boolean>
    toggleActive: (template: Template) => Promise<void>
    testRender: (id: number, fileType: string) => Promise<void>

    // 分组操作
    saveGroup: (id: number | null, data: TemplateGroupForm) => Promise<boolean>
}

/**
 * 将变量列表转换为 schema 和 context
 */
function convertVariablesToPayload(variables: VariableItem[]): {
    variables_schema: Record<string, { type: string; desc: string; location?: VariableItem['location'] }>
    example_context: Record<string, string>
} {
    const variables_schema: Record<string, { type: string; desc: string; location?: VariableItem['location'] }> = {}
    const example_context: Record<string, string> = {}

    for (const v of variables) {
        if (v.name.trim()) {
            variables_schema[v.name] = { type: v.type, desc: v.desc, location: v.location }
            example_context[v.name] = v.exampleValue
        }
    }

    return { variables_schema, example_context }
}

export function useTemplates(): UseTemplatesReturn {
    const { toast } = useToast()

    // 数据状态
    const [templates, setTemplates] = useState<Template[]>([])
    const [groups, setGroups] = useState<TemplateGroup[]>([])

    // 加载状态
    const [isLoading, setIsLoading] = useState(true)
    const [isSaving, setIsSaving] = useState(false)
    const [isTestingRender, setIsTestingRender] = useState(false)

    // 筛选状态
    const [filterGroupId, setFilterGroupId] = useState<number | 'all'>('all')

    // 计算过滤后的模板
    const filteredTemplates = useMemo(() => {
        if (filterGroupId === 'all') return templates
        return templates.filter(t => t.group_id === filterGroupId)
    }, [templates, filterGroupId])

    // 加载数据
    const loadData = useCallback(async () => {
        setIsLoading(true)
        try {
            const [templatesData, groupsData] = await Promise.all([
                extendConfigService.getTemplates(),
                extendConfigService.getTemplateGroups(),
            ])
            setTemplates(templatesData)
            setGroups(groupsData)
        } catch (error) {
            console.error('加载模板失败:', error)
            toast({
                type: 'error',
                title: '加载失败',
                description: error instanceof Error ? error.message : '加载模板失败',
            })
        } finally {
            setIsLoading(false)
        }
    }, [toast])

    // 初始化加载
    useEffect(() => {
        loadData()
    }, [loadData])

    // 创建模板（上传文件）- 返回新创建的模板 ID
    const createTemplate = useCallback(async (
        data: TemplateForm,
        file: File,
        variables: VariableItem[]
    ): Promise<number | null> => {
        setIsSaving(true)
        try {
            const { variables_schema, example_context } = convertVariablesToPayload(variables)

            const formData = new FormData()
            formData.append('name', data.name)
            formData.append('description', data.description)
            formData.append('keywords', data.keywords)
            formData.append('variables_schema', JSON.stringify(variables_schema))
            formData.append('example_context', JSON.stringify(example_context))
            if (data.group_id !== null) {
                formData.append('group_id', String(data.group_id))
            }
            formData.append('file', file)

            const newTemplateId = await extendConfigService.createTemplate(formData)
            await loadData()
            toast({
                type: 'success',
                title: '模板已上传',
            })
            return newTemplateId
        } catch (error) {
            toast({
                type: 'error',
                title: '上传失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return null
        } finally {
            setIsSaving(false)
        }
    }, [loadData, toast])

    // 更新模板
    const updateTemplate = useCallback(async (
        id: number,
        data: TemplateForm,
        variables: VariableItem[]
    ): Promise<boolean> => {
        setIsSaving(true)
        try {
            const { variables_schema, example_context } = convertVariablesToPayload(variables)

            await extendConfigService.updateTemplate(id, {
                name: data.name,
                description: data.description,
                keywords: data.keywords,
                variables_schema,
                example_context,
            })
            await loadData()
            toast({
                type: 'success',
                title: '模板已更新',
            })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '更新失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [loadData, toast])

    // 删除模板
    const deleteTemplate = useCallback(async (id: number): Promise<boolean> => {
        try {
            await extendConfigService.deleteTemplate(id)
            // 乐观更新
            setTemplates(prev => prev.filter(t => t.id !== id))
            toast({
                type: 'success',
                title: '模板已删除',
            })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '删除失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        }
    }, [toast])

    // 切换启用状态
    const toggleActive = useCallback(async (template: Template): Promise<void> => {
        try {
            await extendConfigService.toggleTemplateActive(template.id, !template.is_active)
            // 乐观更新
            setTemplates(prev => prev.map(t =>
                t.id === template.id ? { ...t, is_active: !t.is_active } : t
            ))
        } catch (error) {
            toast({
                type: 'error',
                title: '更新状态失败',
            })
        }
    }, [toast])

    // 测试渲染
    const testRender = useCallback(async (id: number, fileType: string): Promise<void> => {
        setIsTestingRender(true)
        try {
            const blob = await extendConfigService.testRenderTemplate(id)
            // 触发下载
            const url = window.URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = `test_render.${fileType || 'docx'}`
            a.click()
            window.URL.revokeObjectURL(url)
            toast({
                type: 'success',
                title: '测试渲染成功',
            })
        } catch (error) {
            toast({
                type: 'error',
                title: '测试渲染失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
        } finally {
            setIsTestingRender(false)
        }
    }, [toast])

    // 保存分组
    const saveGroup = useCallback(async (id: number | null, data: TemplateGroupForm): Promise<boolean> => {
        setIsSaving(true)
        try {
            await extendConfigService.saveTemplateGroup(id, data)
            const groupsData = await extendConfigService.getTemplateGroups()
            setGroups(groupsData)
            toast({
                type: 'success',
                title: id ? '分组已更新' : '分组已创建',
            })
            return true
        } catch (error) {
            toast({
                type: 'error',
                title: '保存分组失败',
                description: error instanceof Error ? error.message : '操作失败',
            })
            return false
        } finally {
            setIsSaving(false)
        }
    }, [toast])

    return {
        templates,
        groups,
        filteredTemplates,
        isLoading,
        isSaving,
        isTestingRender,
        filterGroupId,
        setFilterGroupId,
        refresh: loadData,
        createTemplate,
        updateTemplate,
        deleteTemplate,
        toggleActive,
        testRender,
        saveGroup,
    }
}
