/**
 * 对话场景组件
 * 
 * 职责：渲染对话模式界面
 * - 消息列表滚动区域
 * - 中断输入组件
 * - 底部固定输入框
 * - 悬浮任务面板
 */
import { useRef, useEffect, useCallback, useMemo } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
    ChatMessage,
    ChatInputArea,
    InterruptInput,
    FloatingTaskPanel,
    SkeletonTaskPanel,
    QuickToolBar,
} from '@/components/chat'
import { ArtifactBoxButton, ArtifactBoxPanel } from '@/components/chat/ArtifactBox'
import type { InterruptData } from '@/components/chat/InterruptInput'
import type { QuickToolMode } from '@/components/chat/QuickToolModes'
import type { UseTaskPlannerReturn } from '@/hooks/chat/useTaskPlanner'
import type { Message } from '@/stores/chatStore'
import type { TaskStep, UploadedFile } from '@/types/chat'
import type { Artifact } from '@/types/sessionRound'
import type { DocScope } from '@/types/docScope'

function userMessageFingerprint(content: string): string {
    return (content || '')
        .replace(/\s+/g, ' ')
        .trim()
        .normalize('NFKC')
        .replace(/[\u200B-\u200D\uFEFF]/g, '')
        .toLowerCase()
        .replace(/[^\p{Script=Han}a-z0-9]+/gu, '')
}

// ========== Props 接口定义 ==========
export interface ChatSceneProps {
    /** 消息列表 */
    messages: Message[]
    /** 轮次产物映射 */
    roundArtifacts: Record<number, Artifact[]>
    /** 任务计划器 Hook 返回值 */
    planner: UseTaskPlannerReturn
    /** 中断数据 */
    interruptData: InterruptData | null
    /** 是否正在提交中断 */
    isSubmittingInterrupt: boolean
    /** 输入消息内容 */
    inputMessage: string
    /** 设置输入消息 */
    setInputMessage: (message: string) => void
    /** 是否正在加载 */
    isLoading: boolean
    /** 当前会话 ID */
    currentSessionId: string | null
    /** 已选择的直连模式 */
    selectedMode: QuickToolMode | null
    /** 已选择的技能 */
    selectedSkill?: { id: string; name: string } | null
    /** [FIX] 用户配置的执行模式，用于隐藏工具栏 */
    userExecutionMode?: string
    /** 会话级知识库范围 */
    docScope?: DocScope | null
    hasData?: boolean
    hasDb?: boolean
    hasKnowledge?: boolean
    readinessAvailable?: boolean
    /** 范围冲突提示 */
    scopeConflictMessage?: string | null
    rightInset?: number

    // ========== 回调函数 ==========
    /** 发送消息 */
    onSend: () => void
    /** 确认计划 */
    onConfirmPlan: (steps: TaskStep[]) => Promise<void>
    /** 取消计划 */
    onCancelPlan: () => void
    /** 中断提交 */
    onInterruptSubmit: (input: string) => Promise<void>
    /** 中断取消 */
    onInterruptCancel: () => void
    /** 预览文件 */
    onPreviewFile: (id: string, name: string, type: string, page?: number, anchor?: string) => void
    /** 预览 DataFrame */
    onPreviewDataFrame: (dfKey: string) => void
    /** 打开 Wiki 引用 */
    onOpenWikiSlug?: (slug: string) => void
    /** 显示 HTML 报告 */
    onShowHtmlReport: (report: { id: string; title: string; content: string }) => void
    /** 文件变化 */
    onFileChange: (file: UploadedFile | null) => void
    /** 选择模式回调 */
    onSelectMode: (mode: QuickToolMode) => void
    /** 移除模式 */
    onRemoveMode: () => void
    /** 选择技能回调 */
    onSelectSkill?: (skillId: string, skillName: string) => void
    /** 清除已选技能 */
    onRemoveSkill?: () => void
    /** 知识库范围切换 */
    onDocScopeChange: (scope: DocScope | null) => void
    /** 错误处理 */
    onError: (msg: string) => void
}

/**
 * 对话场景组件
 * 
 * 在有消息或任务计划时显示，包含消息列表、任务面板和输入框
 */
export function ChatScene({
    messages,
    roundArtifacts,
    planner,
    interruptData,
    isSubmittingInterrupt,
    inputMessage,
    setInputMessage,
    isLoading,
    currentSessionId,
    selectedMode,
    selectedSkill = null,
    userExecutionMode = 'auto',
    docScope = null,
    hasData = false,
    hasDb = false,
    hasKnowledge = false,
    readinessAvailable = false,
    scopeConflictMessage = null,
    rightInset = 0,
    onSend,
    onConfirmPlan,
    onCancelPlan,
    onInterruptSubmit,
    onInterruptCancel,
    onPreviewFile,
    onPreviewDataFrame,
    onOpenWikiSlug,
    onShowHtmlReport,
    onFileChange,
    onSelectMode,
    onRemoveMode,
    onSelectSkill,
    onRemoveSkill,
    onDocScopeChange,
    onError,
}: ChatSceneProps) {
    // ========== Refs ==========
    const messagesEndRef = useRef<HTMLDivElement>(null)
    const visibleMessages = useMemo(() => {
        const result: Message[] = []
        const seenUserFingerprints = new Set<string>()
        for (const message of messages) {
            if (message.role === 'user') {
                const fingerprint = userMessageFingerprint(message.content)
                if (fingerprint && seenUserFingerprints.has(fingerprint)) continue
                if (fingerprint) seenUserFingerprints.add(fingerprint)
            }
            if (result.some((existing) => existing.id === message.id)) continue
            result.push(message)
        }
        return result
    }, [messages])

    // ========== 滚动到底部 ==========
    const scrollToBottom = useCallback(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }, [])

    useEffect(() => {
        scrollToBottom()
    }, [visibleMessages, scrollToBottom])

    // ========== 计算条件 ==========
    // [修复] 快速路径/直连模式返回 completed 时不显示骨架屏
    const isSemanticClarification = interruptData?.signal_type === 'semantic_clarification'
    const blocksFloatingPanel = Boolean(interruptData && !isSemanticClarification)

    const showSkeletonPanel = (
        (isLoading && !planner.taskPlan) ||
        (planner.taskPlan?.status === 'planning' &&
            (!planner.taskPlan.steps || planner.taskPlan.steps.length === 0))
    ) && !blocksFloatingPanel && planner.taskPlan?.status !== 'completed'

    const showFloatingPanel = (
        planner.taskPlan &&
        (planner.taskPlan.status !== 'planning' ||
            (planner.taskPlan.steps && planner.taskPlan.steps.length > 0))
    ) && !blocksFloatingPanel

    // ========== Render ==========
    return (
        <>
            {/* 右上角暂存箱入口 */}
            <div
                className="fixed top-4 z-50"
                style={{
                    right: `${Math.max(16, rightInset + 16)}px`,
                    transition: 'right 300ms ease-out',
                }}
            >
                <ArtifactBoxButton />
            </div>

            {/* 消息区域 */}
            <div
                className="flex-1 overflow-hidden flex flex-col pb-56 pt-16"
                style={{
                    paddingRight: `${rightInset}px`,
                    transition: 'padding-right 300ms ease-out',
                }}
            >
                <ScrollArea className="h-full">
                    <div className="max-w-5xl mx-auto p-6">
                        {visibleMessages.map((message) => {
                            // [Session Round] 使用消息的 roundIndex 获取对应的 Artifacts
                            const artifacts = message.roundIndex !== undefined
                                ? roundArtifacts[message.roundIndex]
                                : undefined

                            return (
                                <ChatMessage
                                    key={message.id}
                                    message={message}
                                    artifacts={artifacts}
                                    onPreviewFile={(id, name, type, page, anchor) => onPreviewFile(id, name, type, page, anchor)}
                                    onPreviewDataFrame={(dfKey) => onPreviewDataFrame(dfKey)}
                                    onOpenWikiSlug={onOpenWikiSlug}
                                    onShowHtmlReport={onShowHtmlReport}
                                />
                            )
                        })}

                        {/* InterruptInput 在消息区域内显示 */}
                        {interruptData && !isSemanticClarification && currentSessionId && (
                            <div className="ml-12 mt-2 mb-6">
                                <InterruptInput
                                    data={interruptData}
                                    sessionId={currentSessionId}
                                    planId={currentSessionId}
                                    onSubmit={onInterruptSubmit}
                                    onCancel={onInterruptCancel}
                                    isSubmitting={isSubmittingInterrupt}
                                />
                            </div>
                        )}

                        <div ref={messagesEndRef} />
                    </div>
                </ScrollArea>
            </div>

            {/* 底部固定输入区域 */}
            <div
                className="fixed bottom-0 left-64 z-40 pointer-events-none"
                style={{
                    right: `${rightInset}px`,
                    transition: 'right 300ms ease-out',
                }}
            >
                {/* 渐变遮罩层 - 仅在最底部48px区域 */}
                <div className="absolute bottom-0 left-0 right-0 h-12 bg-gradient-to-t from-manus to-transparent" />
                <div className="relative p-6">
                    <div className="max-w-3xl mx-auto relative pointer-events-auto">
                        {/* 悬浮任务面板（骨架屏） */}
                        {showSkeletonPanel && <SkeletonTaskPanel />}

                        {/* 悬浮任务面板（正常） */}
                        {showFloatingPanel && (
                            <FloatingTaskPanel
                                plan={planner.taskPlan!}
                                isExpanded={planner.isPanelExpanded}
                                onToggle={() => planner.setIsPanelExpanded(!planner.isPanelExpanded)}
                                onConfirm={onConfirmPlan}
                                onCancel={onCancelPlan}
                                isExecuting={planner.isExecuting}
                                onRemoveStep={planner.removeStep}
                                isAppending={planner.taskPlan!.status === 'planning'}
                                scopeConflictMessage={scopeConflictMessage}
                                interruptData={isSemanticClarification ? interruptData : null}
                                currentSessionId={currentSessionId}
                                onInterruptSubmit={onInterruptSubmit}
                                onInterruptCancel={onInterruptCancel}
                                isSubmittingInterrupt={isSubmittingInterrupt}
                            />
                        )}

                        {/* 输入框 */}
                        <ChatInputArea
                            inputMessage={inputMessage}
                            setInputMessage={setInputMessage}
                            isLoading={isLoading}
                            isExecuting={planner.isExecuting}
                            currentSessionId={currentSessionId}
                            onSend={onSend}
                            onError={onError}
                            onFileChange={onFileChange}
                            selectedMode={selectedMode}
                            onRemoveMode={onRemoveMode}
                            selectedSkill={selectedSkill}
                            onRemoveSkill={onRemoveSkill}
                            docScope={docScope}
                            onDocScopeChange={onDocScopeChange}
                        />

                        {/* 快捷工具栏 */}
                        <div className="flex items-center justify-center">
                            <QuickToolBar
                                onSelectMode={onSelectMode}
                                onSelectSkill={onSelectSkill}
                                hasData={hasData}
                                userExecutionMode={userExecutionMode}
                                hasDb={hasDb}
                                hasKnowledge={hasKnowledge}
                                readinessAvailable={readinessAvailable}
                            />
                        </div>
                    </div>
                </div>
            </div>

            <ArtifactBoxPanel
                onViewChart={(html) => onShowHtmlReport({
                    id: `artifact-chart-${Date.now()}`,
                    title: '暂存图表',
                    content: html,
                })}
            />
        </>
    )
}
