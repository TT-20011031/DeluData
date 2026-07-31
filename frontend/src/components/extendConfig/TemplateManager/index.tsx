/**
 * 模板管理主组件
 * 
 * 组装工具栏、列表和弹窗
 * 
 * 重构：模板编辑已迁移到独立页面 TemplateEditorPage
 * - 新建模板：跳转 /extend-config/templates/new
 * - 编辑模板：跳转 /extend-config/templates/:id
 */
import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Upload, RefreshCw, FolderPlus, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useTemplates } from '@/hooks/extendConfig'
import { GroupFilter, ConfirmDialog, useConfirmDialog } from '../common'
import { TemplateList } from './TemplateList'
import { TemplateGroupDialog } from './TemplateGroupDialog'
import {
    EMPTY_TEMPLATE_GROUP_FORM,
    type Template,
    type TemplateGroupForm,
} from '@/types/extendConfig'

export function TemplateManager() {
    const navigate = useNavigate()

    const {
        templates,
        groups,
        filteredTemplates,
        isLoading,
        isSaving,
        isTestingRender,
        filterGroupId,
        setFilterGroupId,
        refresh,
        deleteTemplate,
        toggleActive,
        testRender,
        saveGroup,
    } = useTemplates()

    // 分组弹窗状态
    const [showGroupDialog, setShowGroupDialog] = useState(false)
    const [groupForm, setGroupForm] = useState<TemplateGroupForm>(EMPTY_TEMPLATE_GROUP_FORM)
    const [editingGroupId, setEditingGroupId] = useState<number | null>(null)

    const confirmDialog = useConfirmDialog()

    // 新建模板 - 跳转到独立页面
    const handleCreateTemplate = useCallback(() => {
        navigate('/extend-config/templates/new')
    }, [navigate])

    // 编辑模板 - 跳转到独立页面
    const handleEditTemplate = useCallback((template: Template) => {
        navigate(`/extend-config/templates/${template.id}`)
    }, [navigate])

    // 删除模板
    const handleDeleteTemplate = useCallback((id: number) => {
        confirmDialog.showConfirm('确定要删除此模板吗？', async () => {
            await deleteTemplate(id)
        })
    }, [deleteTemplate, confirmDialog])

    // 测试渲染
    const handleTestRender = useCallback((template: Template) => {
        testRender(template.id, template.file_type)
    }, [testRender])

    // 新建分组
    const handleCreateGroup = useCallback(() => {
        setEditingGroupId(null)
        setGroupForm(EMPTY_TEMPLATE_GROUP_FORM)
        setShowGroupDialog(true)
    }, [])

    // 保存分组
    const handleSaveGroup = useCallback(async () => {
        if (!groupForm.name.trim()) {
            confirmDialog.showError('分组名称不能为空')
            return
        }
        const success = await saveGroup(editingGroupId, groupForm)
        if (success) {
            setShowGroupDialog(false)
        }
    }, [groupForm, editingGroupId, saveGroup, confirmDialog])

    return (
        <>
            <Card className="bg-manus-secondary border-manus-border">
                <CardHeader className="flex flex-row items-center justify-between">
                    <div>
                        <CardTitle className="text-manus-text flex items-center gap-2">
                            <FileText className="h-5 w-5" />
                            模板列表
                        </CardTitle>
                        <CardDescription className="text-manus-muted">
                            上传 Word/Excel 模板，AI 可使用模板生成文档
                        </CardDescription>
                    </div>
                    <div className="flex gap-2">
                        <Button
                            variant="outline"
                            onClick={refresh}
                            className="bg-manus-tertiary border-manus-border hover:bg-manus-hover text-manus-text"
                            disabled={isLoading}
                        >
                            <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
                            刷新
                        </Button>
                        <Button
                            variant="outline"
                            onClick={handleCreateGroup}
                            className="bg-manus-tertiary border-manus-border hover:bg-manus-hover text-manus-text"
                        >
                            <FolderPlus className="h-4 w-4 mr-2" />
                            新建分组
                        </Button>
                        <Button
                            onClick={handleCreateTemplate}
                            className="bg-accent hover:bg-accent/90 text-white"
                        >
                            <Upload className="h-4 w-4 mr-2" />
                            上传模板
                        </Button>
                    </div>
                </CardHeader>

                <CardContent>
                    {/* 分组筛选 */}
                    <GroupFilter
                        groups={groups}
                        selectedId={filterGroupId}
                        onSelect={setFilterGroupId}
                        totalCount={templates.length}
                    />

                    {/* 列表区域 - 处理加载状态 */}
                    {isLoading && templates.length === 0 ? (
                        <div className="flex items-center justify-center py-12">
                            <Loader2 className="h-8 w-8 animate-spin text-accent" />
                        </div>
                    ) : (
                        <div className="relative">
                            {/* 刷新时的遮罩层，避免闪烁且提示用户正在更新 */}
                            {isLoading && (
                                <div className="absolute inset-0 bg-manus-secondary/50 backdrop-blur-[1px] z-10 flex items-center justify-center">
                                    <Loader2 className="h-6 w-6 animate-spin text-accent" />
                                </div>
                            )}
                            <TemplateList
                                templates={filteredTemplates}
                                onEdit={handleEditTemplate}
                                onDelete={handleDeleteTemplate}
                                onToggleActive={toggleActive}
                                onTestRender={handleTestRender}
                                isTestingRender={isTestingRender}
                                isFiltered={filterGroupId !== 'all'}
                            />
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* 分组弹窗 */}
            <TemplateGroupDialog
                open={showGroupDialog}
                onClose={() => setShowGroupDialog(false)}
                form={groupForm}
                onFormChange={setGroupForm}
                onSave={handleSaveGroup}
                isSaving={isSaving}
                isEditing={editingGroupId !== null}
            />

            <ConfirmDialog
                type={confirmDialog.state.type}
                isOpen={confirmDialog.state.isOpen}
                onClose={confirmDialog.close}
                title={confirmDialog.state.title}
                message={confirmDialog.state.message}
                onConfirm={confirmDialog.state.onConfirm}
                isLoading={confirmDialog.state.isLoading}
            />
        </>
    )
}
