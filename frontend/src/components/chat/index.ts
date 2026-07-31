/**
 * 聊天组件导出
 */
export { ChatMessage } from './ChatMessage'
export { WelcomeScreen } from './WelcomeScreen'
export { ChatInputArea } from './ChatInputArea'
export { InterruptInput, type InterruptData } from './InterruptInput'
export { DataFramePreview } from './DataFramePreview'
export { ImageReference } from './ImageReference'
export { KnowledgeScopePicker } from './KnowledgeScopePicker'
export { FloatingTaskPanel, SkeletonTaskPanel } from './TaskPanel'
export { createMarkdownComponents, preprocessMessageContent, extractImagesFromContent, removeImageTags } from './MarkdownRenderConfig'
export { ImageCarousel } from './ImageCarousel'
export type { ImageInfo } from './ImageCarousel'
// [NEW] 快捷工具相关组件
export { QuickToolBar } from './QuickToolBar'
export { ModeBadge } from './ModeBadge'
export { DeepSearchToggle } from './DeepSearchToggle'
export { QUICK_TOOL_MODES, getQuickToolMode, AUTO_MODE } from './QuickToolModes'
export type { QuickToolMode } from './QuickToolModes'

// [NEW] 场景组件
export { WelcomeScene, ChatScene } from './scenes'
export type { WelcomeSceneProps, ChatSceneProps } from './scenes'

