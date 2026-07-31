/**
 * 博物馆模块 - TypeScript 类型定义
 */

// ========== 人物类型 ==========

export type PersonType = '商务人士' | '儿童' | '妇女' | '老年人' | '通用访客'

export interface PersonAnalysisResult {
    person_type: PersonType
    confidence: number
    features: string[]
    fallback?: boolean
    reason?: string
}

// ========== 商品类型 ==========

export type ProductCategory = '文创' | '纪念品' | '仿制品' | '书籍' | '其他'
export type ProductStatus = 'active' | 'inactive'

export interface Product {
    id: string
    name: string
    description?: string
    price: number
    category: ProductCategory
    related_exhibit_ids: string[]
    image_urls: string[]
    stock: number
    status: ProductStatus
    created_at: string
    updated_at: string
}

export interface ProductRecommendItem {
    id: string
    name: string
    price: number
    image_url?: string
    relation: string
}

// ========== 导览会话 ==========

export type GuideStatus = 'IDLE' | 'THINKING' | 'STREAMING' | 'INTERACTIVE'

export interface GuideSession {
    id: string
    visitor_uuid: string
    person_type?: PersonType
    person_features?: string[]
    created_at: string
    updated_at: string
}

// ========== 导览消息 ==========

export interface GuideMessage {
    id: string
    role: 'user' | 'assistant' | 'system'
    content: string
    timestamp: number
    isStreaming?: boolean
    audioUrl?: string
    images?: ImageRef[]
    products?: ProductRecommendItem[]
}

export interface ImageRef {
    id: string
    url: string
    caption?: string
}

// ========== SSE 事件 ==========

export type SSEEventType =
    | 'HEARTBEAT'
    | 'PERSON_ANALYSIS'
    | 'ADJUSTMENT_PROCESS'
    | 'SEARCH_RESULT'
    | 'SEARCH_IMAGES'          // v2.1: 检索图片事件
    | 'MESSAGE_CHUNK'
    | 'MESSAGE_END'
    | 'IMAGE_REF'
    | 'TTS_CHUNK'
    | 'PRODUCT_RECOMMEND'
    | 'GUIDE_END'
    | 'ERROR'
    // v2.0 场景分析事件
    | 'SCENE_ANALYSIS_STEP'     // 分析步骤更新
    | 'SCENE_ANALYSIS_RESULT'   // 完整分析结果

export interface SSEEvent<T = unknown> {
    type: SSEEventType
    payload: T
}

export interface AdjustmentStep {
    step: 'style' | 'focus' | 'vocabulary' | 'voice'
    label: string
    value: string
    progress: number
}

// ========== VLM 场景分析 (v2.0) ==========

/** 人物三维评分 */
export interface PersonScores {
    purchasing_power: number  // 消费能力 (0-10)
    engagement: number        // 专注度 (0-10)
    influence: number         // 影响力 (0-10)
}

/** 单个人物的高维分析结果 */
export interface PersonInsight {
    id: string
    bbox: [number, number, number, number]  // [x1, y1, x2, y2] 归一化坐标
    role_label: string          // 显性标签：如 "商务男士", "带孩子的母亲"
    visual_cues: string[]       // 视觉线索：如 "佩戴名表", "手持导览图"
    scores: PersonScores        // 三维评分
    final_weight: number        // 综合权重 (0-100)
}

/** 分析步骤 */
export interface AnalysisStep {
    step_id: string
    title: string
    description: string
    status: 'pending' | 'running' | 'done'
}

/** 整体场景分析结果 */
export interface SceneAnalysisResult {
    total_count: number
    group_dynamic: string           // 群体关系：如 "一家三口", "情侣", "考察团"
    persons: PersonInsight[]
    target_person_id: string        // 建议主要对话对象
    suggested_tone: string          // 建议语气：如 "专业干练", "亲切活泼"
    engagement_strategy: string     // 具体的切入话术建议
    analysis_steps: AnalysisStep[]  // 分析过程步骤
}

/** 上传的场景图片 */
export interface SceneImage {
    id: string
    url: string
    thumbnailUrl?: string
    uploadedAt: number
}

// [Zero Tech Debt] TTSChunk 改用公用模块定义
// 保持向后兼容，原有代码无需修改
export type { TTSChunk } from '@/shared/voice/types'

// ========== API 请求/响应 ==========

export interface GuideStartRequest {
    query?: string
    enable_tts?: boolean
    visitor_uuid?: string
}

export interface GuideChatRequest {
    session_id: string
    message: string
    person_type?: PersonType
    enable_tts?: boolean
}

export interface PaginatedResponse<T> {
    items: T[]
    total: number
    page: number
    page_size: number
    total_pages: number
}
