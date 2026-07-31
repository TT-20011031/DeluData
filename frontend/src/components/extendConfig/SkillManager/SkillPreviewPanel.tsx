/**
 * Skill 详情预览组件
 */
import type { Skill } from '@/services/skillService'

interface SkillPreviewProps {
    skill: Skill
}

const TOOL_LABELS: Record<string, string> = {
    sql_worker: 'SQL 查询',
    doc_worker: '文档检索',
    chart_worker: '图表生成',
    synthesizer: '汇总分析',
    office_worker: '文档处理',
}

const formatDocScope = (scope?: Record<string, any>) => {
    if (!scope) return ''
    const folderIds = Array.isArray(scope.folder_ids) ? scope.folder_ids : []
    const fileIds = Array.isArray(scope.file_ids) ? scope.file_ids : []
    const includeSubfolders = scope.include_subfolders !== false
    if (folderIds.length === 0 && fileIds.length === 0) return '默认全库'
    return `文件夹 ${folderIds.length} / 文件 ${fileIds.length} / 含子目录 ${includeSubfolders ? '是' : '否'}`
}

export function SkillPreviewPanel({ skill }: SkillPreviewProps) {
    return (
        <div className="space-y-4">
            {/* 标签 */}
            {skill.tags && skill.tags.length > 0 && (
                <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm text-manus-muted">标签:</span>
                    {skill.tags.map((tag, i) => (
                        <span
                            key={i}
                            className="px-2 py-0.5 text-xs rounded bg-accent/20 text-accent"
                        >
                            {tag}
                        </span>
                    ))}
                </div>
            )}

            {/* 示例问题 */}
            {skill.example_queries && skill.example_queries.length > 0 && (
                <div className="space-y-1">
                    <span className="text-sm text-manus-muted">示例问题:</span>
                    <ul className="text-sm text-manus-text pl-4 list-disc">
                        {skill.example_queries.map((q, i) => (
                            <li key={i}>{q}</li>
                        ))}
                    </ul>
                </div>
            )}

            {/* 步骤 */}
            <div className="space-y-2">
                <span className="text-sm text-manus-muted">执行步骤:</span>
                <div className="space-y-2">
                    {skill.steps.map((step, index) => (
                        <div
                            key={index}
                            className="flex items-start gap-3 p-3 bg-manus rounded border border-manus-border"
                        >
                            <span className="w-6 h-6 rounded-full bg-accent text-white text-xs flex items-center justify-center shrink-0">
                                {step.step}
                            </span>
                            <div className="flex-1 min-w-0">
                                <p className="text-sm text-manus-text">{step.action}</p>
                                <div className="flex items-center gap-3 mt-1 text-xs text-manus-muted">
                                    {step.tool && (
                                        <span className="px-2 py-0.5 rounded bg-manus-tertiary">
                                            {TOOL_LABELS[step.tool] || step.tool}
                                        </span>
                                    )}
                                    {step.template && (
                                        <code className="px-2 py-0.5 rounded bg-manus-tertiary font-mono truncate max-w-xs">
                                            {step.template}
                                        </code>
                                    )}
                                    {step.template_id && (
                                        <span className="px-2 py-0.5 rounded bg-manus-tertiary">
                                            模板ID: {step.template_id}
                                        </span>
                                    )}
                                    {step.output_filename && (
                                        <span className="px-2 py-0.5 rounded bg-manus-tertiary">
                                            输出: {step.output_filename}
                                        </span>
                                    )}
                                    {step.doc_scope && (
                                        <span className="px-2 py-0.5 rounded bg-manus-tertiary">
                                            文档范围: {formatDocScope(step.doc_scope)}
                                        </span>
                                    )}
                                    {step.keywords && step.keywords.length > 0 && (
                                        <span>关键词: {step.keywords.join(', ')}</span>
                                    )}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            {/* 元信息 */}
            <div className="flex items-center gap-4 text-xs text-manus-muted pt-2 border-t border-manus-border">
                <span>创建于: {new Date(skill.created_at).toLocaleString()}</span>
                <span>更新于: {new Date(skill.updated_at).toLocaleString()}</span>
            </div>
        </div>
    )
}
