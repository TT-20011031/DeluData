/**
 * ArtifactCard 组件
 *
 * 用于显示图表/文档等产物的骨架屏和完成状态卡片
 */
import React from 'react'
import { FileText, FileSpreadsheet, BarChart3, Loader2, AlertCircle, ExternalLink, Ban } from 'lucide-react'
import type { Artifact } from '@/types/sessionRound'
import './ArtifactCard.css'

interface ArtifactCardProps {
    artifact: Artifact
    onViewChart?: (htmlContent: string) => void
    onDownload?: (downloadUrl: string, fileName: string) => void
}

export const ArtifactCard: React.FC<ArtifactCardProps> = ({
    artifact,
    onViewChart,
    onDownload,
}) => {
    const { stepId, type, status, title, data } = artifact
    const lowerFileName = data?.file_name?.toLowerCase() || ''
    const isExcel = type === 'doc' && (
        data?.file_type === 'excel'
        || lowerFileName.endsWith('.xlsx')
        || lowerFileName.endsWith('.xls')
        || lowerFileName.endsWith('.xlsm')
    )

    // 图标选择
    const Icon = type === 'chart' ? BarChart3 : isExcel ? FileSpreadsheet : FileText

    // 状态对应的样式类 + 类型样式类（用于颜色区分）
    // [P2] 图表绿色（洞察与生长），文档蓝色（沉淀与理性）
    const statusClass = `artifact-card artifact-card--${status} artifact-card--${type}`

    // 点击处理
    const handleClick = () => {
        if (status !== 'completed') return

        if (type === 'chart' && data?.html_content && onViewChart) {
            onViewChart(data.html_content)
        } else if (type === 'doc' && data?.download_url && onDownload) {
            onDownload(data.download_url, data.file_name || (isExcel ? '工作簿.xlsx' : '文档'))
        }
    }

    return (
        <div className={statusClass} onClick={handleClick} data-step-id={stepId}>
            {/* 骨架屏状态 */}
            {(status === 'pending' || status === 'generating') && (
                <div className="artifact-card__skeleton">
                    <div className="artifact-card__skeleton-icon">
                        <Loader2 className="artifact-card__spinner" />
                    </div>
                    <div className="artifact-card__skeleton-text">
                        <div className="artifact-card__skeleton-title" />
                        <div className="artifact-card__skeleton-subtitle" />
                    </div>
                </div>
            )}

            {/* 完成状态 */}
            {status === 'completed' && (
                <div className="artifact-card__content">
                    <Icon className="artifact-card__icon" />
                    <div className="artifact-card__info">
                        <span className="artifact-card__title">
                            {title || (type === 'chart' ? '图表报告' : isExcel ? 'Excel 工作簿' : '文档')}
                        </span>
                        <span className="artifact-card__action">
                            <ExternalLink size={12} />
                            {type === 'chart' ? '查看报告' : isExcel ? '下载 Excel' : '下载文件'}
                        </span>
                    </div>
                </div>
            )}

            {/* 错误状态 */}
            {status === 'error' && (
                <div className="artifact-card__error">
                    <AlertCircle className="artifact-card__icon artifact-card__icon--error" />
                    <span className="artifact-card__error-text">生成失败</span>
                </div>
            )}

            {/* 数据无效状态 - 灰色覆盖 + 圆形感叹号 */}
            {status === 'invalid_data' && (
                <div className="artifact-card__invalid">
                    <div className="artifact-card__invalid-badge">
                        <AlertCircle size={18} />
                    </div>
                    <span className="artifact-card__invalid-text">数据无效</span>
                </div>
            )}

            {/* 取消状态 - 灰色斜纹底色，区别于红色错误 */}
            {status === 'cancelled' && (
                <div className="artifact-card__cancelled">
                    <Ban className="artifact-card__icon artifact-card__icon--cancelled" />
                    <span className="artifact-card__cancelled-text">{title || '任务已取消'}</span>
                </div>
            )}
        </div>
    )
}

export default ArtifactCard
