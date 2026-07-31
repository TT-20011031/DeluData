/**
 * SQL 示例管理主组件
 * 
 * 组装工具栏、列表和弹窗
 */
import { useState, useCallback } from 'react'
import {
    Code, Plus, RefreshCw, FolderPlus,
    Trash2, Loader2, CheckSquare, Square
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useSqlExamples } from '@/hooks/extendConfig'
import { GroupFilter, ConfirmDialog, useConfirmDialog } from '../common'
import { SqlExampleList } from './SqlExampleList'
import { SqlExampleDialog } from './SqlExampleDialog'
import { SqlGroupDialog } from './SqlGroupDialog'
import { BatchMoveDialog } from './BatchMoveDialog'
import {
    EMPTY_SQL_EXAMPLE_FORM,
    EMPTY_SQL_GROUP_FORM,
    type SqlExample,
    type SqlExampleForm,
    type SqlGroupForm,
} from '@/types/extendConfig'

export function SqlExampleManager() {
    const {
        examples,
        groups,
        filteredExamples,
        isLoading,
        isSaving,
        filterGroupId,
        setFilterGroupId,
        selectedIds,
        toggleSelect,
        selectAll,
        refresh,
        saveExample,
        deleteExample,
        toggleActive,
        batchDelete,
        batchMove,
        saveGroup,
    } = useSqlExamples()

    // Dialog 状态管理（留在 Manager 组件层）
    const [showExampleDialog, setShowExampleDialog] = useState(false)
    const [exampleForm, setExampleForm] = useState<SqlExampleForm>(EMPTY_SQL_EXAMPLE_FORM)
    const [editingExampleId, setEditingExampleId] = useState<number | null>(null)

    const [showGroupDialog, setShowGroupDialog] = useState(false)
    const [groupForm, setGroupForm] = useState<SqlGroupForm>(EMPTY_SQL_GROUP_FORM)
    const [editingGroupId, setEditingGroupId] = useState<number | null>(null)

    const [showBatchMoveDialog, setShowBatchMoveDialog] = useState(false)
    const [batchMoveTargetId, setBatchMoveTargetId] = useState<number | null>(null)
    const [isBatchMoving, setIsBatchMoving] = useState(false)
    const [isBatchDeleting, setIsBatchDeleting] = useState(false)

    const confirmDialog = useConfirmDialog()

    // 新建示例
    const handleCreateExample = useCallback(() => {
        setEditingExampleId(null)
        setExampleForm(EMPTY_SQL_EXAMPLE_FORM)
        setShowExampleDialog(true)
    }, [])

    // 编辑示例
    const handleEditExample = useCallback((example: SqlExample) => {
        setEditingExampleId(example.id)
        setExampleForm({
            question: example.question,
            sql: example.sql,
            description: example.description || '',
            tables: example.tables || '',
            group_id: example.group_id,
        })
        setShowExampleDialog(true)
    }, [])

    // 保存示例
    const handleSaveExample = useCallback(async () => {
        if (!exampleForm.question.trim() || !exampleForm.sql.trim()) {
            confirmDialog.showError('问题描述和示例 SQL 不能为空')
            return
        }
        const success = await saveExample(editingExampleId, exampleForm)
        if (success) {
            setShowExampleDialog(false)
        }
    }, [exampleForm, editingExampleId, saveExample, confirmDialog])

    // 删除示例
    const handleDeleteExample = useCallback((id: number) => {
        confirmDialog.showConfirm('确定要删除此 SQL 示例吗？', async () => {
            await deleteExample(id)
        })
    }, [deleteExample, confirmDialog])

    // 新建分组
    const handleCreateGroup = useCallback(() => {
        setEditingGroupId(null)
        setGroupForm(EMPTY_SQL_GROUP_FORM)
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

    // 批量删除
    const handleBatchDelete = useCallback(async () => {
        if (selectedIds.size === 0) return

        confirmDialog.showConfirm(
            `确定要删除选中的 ${selectedIds.size} 条 SQL 示例吗？`,
            async () => {
                setIsBatchDeleting(true)
                await batchDelete()
                setIsBatchDeleting(false)
            }
        )
    }, [selectedIds.size, batchDelete, confirmDialog])

    // 批量移动
    const handleBatchMove = useCallback(async () => {
        setIsBatchMoving(true)
        const success = await batchMove(batchMoveTargetId)
        if (success) {
            setShowBatchMoveDialog(false)
        }
        setIsBatchMoving(false)
    }, [batchMoveTargetId, batchMove])

    // 打开批量移动弹窗
    const handleOpenBatchMove = useCallback(() => {
        setBatchMoveTargetId(null)
        setShowBatchMoveDialog(true)
    }, [])

    if (isLoading) {
        return (
            <div className="flex items-center justify-center py-12">
                <Loader2 className="h-8 w-8 animate-spin text-accent" />
            </div>
        )
    }

    return (
        <>
            <Card className="bg-manus-secondary border-manus-border">
                <CardHeader className="flex flex-row items-center justify-between">
                    <div>
                        <CardTitle className="text-manus-text flex items-center gap-2">
                            <Code className="h-5 w-5" />
                            SQL 示例列表
                            {groups.length > 0 && (
                                <span className="text-sm font-normal text-manus-muted">
                                    ({groups.length} 个分组)
                                </span>
                            )}
                        </CardTitle>
                        <CardDescription className="text-manus-muted">
                            这些示例将在 AI 生成 SQL 时作为参考
                        </CardDescription>
                    </div>
                    <div className="flex gap-2">
                        <Button
                            variant="outline"
                            onClick={refresh}
                            className="bg-manus-tertiary border-manus-border hover:bg-manus-hover text-manus-text"
                        >
                            <RefreshCw className="h-4 w-4 mr-2" />
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
                            onClick={handleCreateExample}
                            className="bg-accent hover:bg-accent/90 text-white"
                        >
                            <Plus className="h-4 w-4 mr-2" />
                            新建示例
                        </Button>
                    </div>
                </CardHeader>

                <CardContent>
                    {/* 分组筛选 */}
                    <GroupFilter
                        groups={groups}
                        selectedId={filterGroupId}
                        onSelect={setFilterGroupId}
                        totalCount={examples.length}
                    />

                    {/* 批量操作工具栏 */}
                    {filteredExamples.length > 0 && (
                        <div className="flex items-center gap-4 mb-4 p-3 bg-manus rounded-lg border border-manus-border">
                            <button
                                onClick={selectAll}
                                className="flex items-center gap-2 text-sm text-manus-muted hover:text-manus-text"
                            >
                                {selectedIds.size === filteredExamples.length && filteredExamples.length > 0 ? (
                                    <CheckSquare className="h-4 w-4 text-accent" />
                                ) : (
                                    <Square className="h-4 w-4" />
                                )}
                                全选
                            </button>

                            {selectedIds.size > 0 && (
                                <>
                                    <span className="text-sm text-manus-muted">
                                        已选 {selectedIds.size} 项
                                    </span>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={handleOpenBatchMove}
                                        className="bg-manus-tertiary border-manus-border text-manus-text"
                                    >
                                        <FolderPlus className="h-4 w-4 mr-1" />
                                        移动到分组
                                    </Button>
                                    <Button
                                        variant="destructive"
                                        size="sm"
                                        onClick={handleBatchDelete}
                                        disabled={isBatchDeleting}
                                    >
                                        {isBatchDeleting ? (
                                            <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                                        ) : (
                                            <Trash2 className="h-4 w-4 mr-1" />
                                        )}
                                        批量删除
                                    </Button>
                                </>
                            )}
                        </div>
                    )}

                    {/* 列表 */}
                    <SqlExampleList
                        examples={filteredExamples}
                        selectedIds={selectedIds}
                        onToggleSelect={toggleSelect}
                        onEdit={handleEditExample}
                        onDelete={handleDeleteExample}
                        onToggleActive={toggleActive}
                        isFiltered={filterGroupId !== 'all'}
                    />
                </CardContent>
            </Card>

            {/* 弹窗 */}
            <SqlExampleDialog
                open={showExampleDialog}
                onClose={() => setShowExampleDialog(false)}
                form={exampleForm}
                onFormChange={setExampleForm}
                onSave={handleSaveExample}
                isSaving={isSaving}
                isEditing={editingExampleId !== null}
                groups={groups}
            />

            <SqlGroupDialog
                open={showGroupDialog}
                onClose={() => setShowGroupDialog(false)}
                form={groupForm}
                onFormChange={setGroupForm}
                onSave={handleSaveGroup}
                isSaving={isSaving}
                isEditing={editingGroupId !== null}
            />

            <BatchMoveDialog
                open={showBatchMoveDialog}
                onClose={() => setShowBatchMoveDialog(false)}
                selectedCount={selectedIds.size}
                groups={groups}
                targetGroupId={batchMoveTargetId}
                onTargetChange={setBatchMoveTargetId}
                onConfirm={handleBatchMove}
                isMoving={isBatchMoving}
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
