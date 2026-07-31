import type { SemanticReadiness, SemanticSchemaDiffItem } from '@/types/extendConfig'

export function readinessLabel(status: SemanticReadiness['status']) {
    return {
        not_scanned: '未建立基线',
        degraded: '可信度降级',
        partially_ready: '部分可用',
        ready: '可信就绪',
    }[status]
}

export function readinessGuidance(readiness: SemanticReadiness) {
    if (readiness.status === 'not_scanned') {
        return '先运行并应用 Schema Diff，建立可信基线。'
    }
    if (readiness.status === 'degraded') {
        return readiness.blockers?.[0] || '存在结构或安全阻塞，请先处理后再开放问数。'
    }
    if (readiness.status === 'partially_ready') {
        const staleAssets = readiness.semantics.stale_assets || 0
        if (staleAssets > 0) {
            return `${staleAssets} 个资产待复核；当前仅已治理范围可问数。`
        }
        return '基础结构可用；继续确认表、字段、指标和关系，扩大可回答范围。'
    }
    return '结构与安全无阻塞，语义目录可用；具体问题仍需单题诊断。'
}

export function diffRisk(item: SemanticSchemaDiffItem) {
    if (item.blocking) return 'critical'
    if (item.change_type === 'modified') return 'review'
    return 'safe'
}
