/**
 * AI 思考状态配置
 *
 * 根据后端 step_update 的 label/id 映射到前端显示状态
 * 用于动态更新 AI 头像区域的状态文案和图标
 *
 * 设计原则：
 * - 配置与组件分离（Design Rigor）
 * - 关键词数组便于扩展（No Hardcoding）
 * - 优先级从具体到泛化
 */

/** 思考状态 Key */
export type ThinkingStatusKey = 'thinking' | 'rag' | 'sql' | 'chart' | 'file' | 'synthesizing'

/** 状态配置项 */
export interface ThinkingStatusItem {
    key: ThinkingStatusKey
    /** 显示文本 */
    text: string
    /** 匹配关键词（step label/id 中包含任一关键词即命中） */
    keywords: string[]
}

/**
 * 状态映射表（按优先级排列：具体 → 泛化）
 *
 * 注意：synthesizing 不参与关键词匹配（合成阶段由 onMessageChunk 自动替换 thinking 消息）
 */
export const THINKING_STATUS_MAP: ThinkingStatusItem[] = [
    {
        key: 'chart',
        text: '生成报表中',
        keywords: ['chart_worker', 'chart', '图表', '可视化'],
    },
    {
        key: 'file',
        text: '生成文件中',
        keywords: ['office_worker', 'office', '导出', 'excel', 'word'],
    },
    {
        key: 'sql',
        text: '查询数据中',
        keywords: ['sql_worker', 'sql', '数据库', 'query', 'xiyan', '析言'],
    },
    {
        key: 'rag',
        text: '获取数据中',
        keywords: ['doc_worker', 'rag', '检索', '文档', 'document'],
    },
]

/** 默认状态（无匹配时） */
export const DEFAULT_THINKING_STATUS: ThinkingStatusItem = {
    key: 'thinking',
    text: '思考中',
    keywords: [],
}

/**
 * 根据 step label 匹配状态
 *
 * @param stepLabel - step_update 的 label 或 id
 * @returns 匹配到的状态项
 */
export function matchThinkingStatus(stepLabel: string): ThinkingStatusItem {
    const normalized = stepLabel.toLowerCase()
    for (const item of THINKING_STATUS_MAP) {
        if (item.keywords.some(kw => normalized.includes(kw.toLowerCase()))) {
            return item
        }
    }
    return DEFAULT_THINKING_STATUS
}

/**
 * 前端私有标记前缀，用于识别 thinking 占位消息 content
 * 
 * 格式: `__THINKING__:{key}:{text}`
 * 例如: `__THINKING__:rag:获取数据中`
 */
export const THINKING_PREFIX = '__THINKING__'

/** 生成 thinking 占位消息 content */
export function encodeThinkingContent(status: ThinkingStatusItem): string {
    return `${THINKING_PREFIX}:${status.key}:${status.text}`
}

/** 解析 thinking 占位消息 content */
export function decodeThinkingContent(content: string): { key: ThinkingStatusKey; text: string } | null {
    if (!content.startsWith(THINKING_PREFIX + ':')) return null
    const parts = content.split(':')
    if (parts.length < 3) return null
    return {
        key: parts[1] as ThinkingStatusKey,
        text: parts.slice(2).join(':'),
    }
}

/** 判断消息内容是否为 thinking 占位 */
export function isThinkingContent(content: string): boolean {
    return content.startsWith(THINKING_PREFIX + ':') ||
        content === '思考中…' ||
        content === '思考中...' ||
        content === '正在规划任务步骤...' ||
        content === '正在处理您的回复...'
}
