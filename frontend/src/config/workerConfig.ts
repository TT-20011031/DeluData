/**
 * Worker 配置
 * 
 * 定义各 Worker 类型的显示名称和子步骤映射
 * 支持未来扩展新 Worker
 */

// Worker 类型到显示名称的映射
export const WORKER_LABELS: Record<string, string> = {
    'sql_worker': 'SQL 查询',
    'doc_worker': '知识库检索',
    'chart_worker': '可视化图表',
    'office_worker': '文件处理',
    'inspect_file': '文件预览',  // 预执行步骤
    'parameter_definition': '参数定义',
    // 反思机制节点（系统内部，用户不可选）
    'reflector': '质量检查',
    'failsafe': '兜底回复',
}

// [新增] 用户可选的工具列表（排除系统内部工具）
export const USER_SELECTABLE_WORKERS: Record<string, string> = {
    'sql_worker': 'SQL 查询',
    'doc_worker': '知识库检索',
    'chart_worker': '可视化图表',
    'office_worker': '文件处理',
}

// [新增] Worker 依赖的外部配置（用于判断是否可用）
export const WORKER_REQUIRES_CONFIG: Record<string, 'database' | 'knowledge'> = {
    'sql_worker': 'database',
    'doc_worker': 'knowledge',
}

// 子步骤 ID 到显示名称的映射 (按 Worker 分组)
export const SUB_STEP_LABELS: Record<string, Record<string, string>> = {
    // SQL Worker 子步骤 (xiyan-* 前缀)
    'sql_worker': {
        'parse': '解析需求',
        'generate': '生成 SQL',
        'validate': '校验语法',
        'execute': '执行查询',
        'format': '格式化结果',
    },
    // Doc Worker 子步骤 (doc_* 前缀)
    'doc_worker': {
        'doc_rewrite': '理解问题',
        'doc_retrieval': '翻阅知识库',
        'doc_rerank': '筛选内容',
        'doc_expand': '补充上下文',
    },
    // Office Worker 子步骤 (office-* 前缀)
    'office_worker': {
        '连接MCP': '连接执行环境',
        '分析任务': '分析任务并生成代码',
        '执行代码': '执行处理代码',
        '执行': '执行中',
    },
}

// 根据 step ID 获取子步骤显示名称
export function getSubStepLabel(stepId: string, workerType?: string): string {
    // 检查 xiyan-* 前缀 (SQL Worker)
    if (stepId.startsWith('xiyan-')) {
        const subKey = stepId.replace('xiyan-', '')
        return SUB_STEP_LABELS['sql_worker']?.[subKey] || subKey
    }

    // 检查 doc_* 前缀 (Doc Worker)
    if (stepId.startsWith('doc_')) {
        return SUB_STEP_LABELS['doc_worker']?.[stepId] || stepId.replace('doc_', '')
    }

    // 通用情况：尝试从指定 worker 类型查找
    if (workerType && SUB_STEP_LABELS[workerType]) {
        return SUB_STEP_LABELS[workerType][stepId] || stepId
    }

    return stepId
}

// 获取 Worker 显示名称
export function getWorkerLabel(workerType: string): string {
    return WORKER_LABELS[workerType] || workerType
}

// 判断是否为子步骤
export function isSubStep(stepId: string): boolean {
    return stepId.startsWith('xiyan-') || stepId.startsWith('doc_')
}

// 获取子步骤的 Worker 类型
export function getSubStepWorkerType(stepId: string): string | null {
    if (stepId.startsWith('xiyan-')) return 'sql_worker'
    if (stepId.startsWith('doc_')) return 'doc_worker'
    return null
}
