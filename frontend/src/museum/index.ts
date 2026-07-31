/**
 * 博物馆智能导览导购模块
 * 
 * 独立于主系统的业务模块，提供：
 * - 图像人物识别
 * - 动态提示词调节
 * - 知识库导览问答
 * - TTS 语音输出
 * - 商城导购推荐
 */

// Hooks
export { useGuideStore } from './stores/guideStore'

// Types
export type { PersonType, Product, GuideSession } from './types'

// Note: Pages are lazy-loaded via router, not exported here
