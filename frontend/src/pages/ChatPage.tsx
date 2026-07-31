/**
 * 聊天页面
 * 
 * 职责：作为容器组件，协调场景组件和全局状态
 * - 场景切换：欢迎页 / 对话页
 * - 全局 Sheet/Panel 管理
 * - Hook 组合：Actions + SSE Handlers + Planner
 * 
 * HITL 工作流：发送 -> 生成计划 -> 用户确认 -> 执行
 * 
 * Hook 依赖结构（单向流）：
 * thinkingMsgIdRef (来自 useRef)
 *    ↓
 * useChatSSEHandlers → sseCallbacks
 *    ↓
 * useChatSSE → connect/disconnect
 *    ↓
 * useChatActions → handleSend/handleConfirmPlan 等
 */
import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useChatStore } from '@/stores/chatStore'
import { useMissionStore } from '@/stores/missionStore'
import { useAuthStore } from '@/stores/authStore'
import { useLayoutStore } from '@/stores/layoutStore'
import { FilePreviewSheet } from '@/components/knowledge/FilePreviewSheet'

// Chat 组件
import { WelcomeScene, ChatScene, DataFramePreview } from '@/components/chat'
import { ReplyModelSelector } from '@/components/chat/ReplyModelSelector'
import type { InterruptData } from '@/components/chat/InterruptInput'
import type { QuickToolMode } from '@/components/chat/QuickToolModes'
import { HtmlReportPanel } from '@/components/HtmlReportPanel'

// Hooks
import { useTaskPlanner, useChatSSE, useChatActions, useChatSSEHandlers, useWorkspaceReadiness } from '@/hooks/chat'
import { useUserAgentConfig } from '@/hooks/chat/useUserAgentConfig'

// Types
import type { UploadedFile } from '@/types/chat'
import type { DocScope } from '@/types/docScope'
import { DEFAULT_REPLY_MODEL_KEY, REPLY_MODEL_STORAGE_KEY, type ReplyModelKey } from '@/types/replyModel'
import { compareDocScope, compactDocScope } from '@/types/docScope'

const DEFAULT_PREVIEW_PANEL_WIDTH = 520
const DEFAULT_MIN_PREVIEW_PANEL_WIDTH = 420
const MIN_CHAT_WIDTH = 300
const CHAT_LEFT_INSET = 256

export default function ChatPage() {
    const navigate = useNavigate()
    // ========== 1. Global Store ==========
    const {
        currentSessionId,
        sessions,
        inputMessage,
        isLoading,
        deepSearch,
        roundArtifacts,
        setInputMessage,
    } = useChatStore()

    // ========== 2. Task Planner ==========
    const planner = useTaskPlanner(currentSessionId)

    // ========== 3. Local UI State ==========
    const [uploadedFile, setUploadedFile] = useState<UploadedFile | null>(null)
    const [interruptData, setInterruptData] = useState<InterruptData | null>(null)
    const [isSubmittingInterrupt, setIsSubmittingInterrupt] = useState(false)
    const [previewFile, setPreviewFile] = useState<{ id: string; name: string; type: string; page?: number; anchor?: string; _ts?: number } | null>(null)
    const [previewPanelWidth, setPreviewPanelWidth] = useState(DEFAULT_PREVIEW_PANEL_WIDTH)
    const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth)
    const [previewDataFrame, setPreviewDataFrame] = useState<string | null>(null)
    const [htmlReport, setHtmlReport] = useState<{ id: string; title: string; content: string } | null>(null)
    const [showHtmlReport, setShowHtmlReport] = useState(false)
    const [selectedMode, setSelectedMode] = useState<QuickToolMode | null>(null)
    const [selectedSkill, setSelectedSkill] = useState<{ id: string; name: string } | null>(null)
    const [replyModelKey, setReplyModelKey] = useState<ReplyModelKey>(() => {
        if (typeof window === 'undefined') {
            return DEFAULT_REPLY_MODEL_KEY
        }
        const saved = window.localStorage.getItem(REPLY_MODEL_STORAGE_KEY)
        return saved === 'plus' || saved === 'flash' || saved === 'max' ? saved : DEFAULT_REPLY_MODEL_KEY
    })
    const [sessionDocScopes, setSessionDocScopes] = useState<Record<string, DocScope | null>>({})
    const [pendingDocScope, setPendingDocScope] = useState<DocScope | null>(null)

    // ========== 4. User Agent Config ([FIX] 执行模式限制) ==========
    const { executionMode: userExecutionMode, isLoading: configLoading } = useUserAgentConfig()
    const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
    const sidebarCollapsed = useLayoutStore((state) => state.sidebarCollapsed)
    const { hasDb, hasKnowledge, isAvailable: readinessAvailable } = useWorkspaceReadiness()

    // ========== 5. Shared Ref (解决循环依赖) ==========
    // thinkingMsgIdRef 在组件顶层定义，供 SSE Handlers 和 Actions 共享
    const thinkingMsgIdRef = useRef<string | null>(null)
    const sendSubmitLockRef = useRef(false)

    // ========== 6. SSE Handlers (先于 SSE 建立) ==========
    const { sseCallbacks } = useChatSSEHandlers({
        sessionId: currentSessionId,
        planner,
        thinkingMsgIdRef,
        setInterruptData,
        setHtmlReport,
        setShowHtmlReport,
    })

    // ========== 7. SSE Connection ==========
    const { connect: connectSSE, disconnect: disconnectSSE } = useChatSSE(
        currentSessionId,
        sseCallbacks
    )

    // ========== 8. Actions Hook ==========
    // thinkingMsgIdRef 由组件顶层定义，传递给 Actions，实现单一数据源
    const actions = useChatActions({
        sessionId: currentSessionId,
        planner,
        connectSSE,
        disconnectSSE,
        thinkingMsgIdRef,  // 传入唯一的 Ref，消除竞态条件
        userExecutionMode,  // [FIX] 强制直连执行模式
        deepSearch,
    })

    // ========== 9. Computed Values ==========
    const currentSession = currentSessionId ? sessions[currentSessionId] : null
    const messages = currentSession?.messages || []
    const isWelcomeMode = messages.length === 0 && !planner.taskPlan
    const hasRuntimeData = useMemo(() => {
        if (uploadedFile) return true
        if (messages.some((msg) => msg.role === 'assistant' && Boolean(msg.content?.trim()))) return true
        return Object.values(roundArtifacts).some((artifacts) =>
            artifacts?.some((artifact) => artifact.status === 'completed'),
        )
    }, [messages, roundArtifacts, uploadedFile])
    const currentDocScope = currentSessionId
        ? (sessionDocScopes[currentSessionId] ?? null)
        : pendingDocScope
    const scopeConflictMessage = useMemo(() => {
        const selectedScope = compactDocScope(currentDocScope)
        if (!selectedScope || !planner.taskPlan) return null
        const hasConflict = planner.taskPlan.steps.some((step) => {
            if (step.worker !== 'doc_worker') return false
            const stepScope = (step.params || {}).doc_scope
            if (!stepScope) return false
            return !compareDocScope(stepScope, selectedScope)
        })
        if (!hasConflict) return null
        const skillName = planner.taskPlan.selected_skill_name
        if (skillName) {
            return `当前执行采用 DeluSkills 指定范围，已覆盖输入区范围。来自：${skillName}`
        }
        return '当前执行采用 DeluSkills 指定范围，已覆盖输入区范围。'
    }, [currentDocScope, planner.taskPlan])
    const maxPreviewPanelWidth = useMemo(() => {
        const viewportLimit = Math.floor(viewportWidth * 0.9)
        const chatReserved = Math.floor(viewportWidth - CHAT_LEFT_INSET - MIN_CHAT_WIDTH)
        const chatConstrainedLimit = Math.max(260, chatReserved)
        return Math.max(260, Math.min(viewportLimit, chatConstrainedLimit))
    }, [viewportWidth])
    const minPreviewPanelWidth = useMemo(() => {
        return Math.min(DEFAULT_MIN_PREVIEW_PANEL_WIDTH, maxPreviewPanelWidth)
    }, [maxPreviewPanelWidth])
    const clampedPreviewPanelWidth = useMemo(() => {
        return Math.min(Math.max(previewPanelWidth, minPreviewPanelWidth), maxPreviewPanelWidth)
    }, [previewPanelWidth, minPreviewPanelWidth, maxPreviewPanelWidth])
    const previewRightInset = previewFile ? clampedPreviewPanelWidth : 0

    // ========== 10. Effects ==========

    // [会话隔离修复] 会话切换时重置状态
    useEffect(() => {
        setInterruptData(null)
        useChatStore.getState().resetAllRoundArtifacts()
        useMissionStore.getState().reset()
    }, [currentSessionId])

    // 清理 SSE 连接
    useEffect(() => {
        return () => {
            disconnectSSE()
            useChatStore.getState().resetAllRoundArtifacts()
            setSessionDocScopes({})
            setPendingDocScope(null)
        }
    }, [disconnectSSE])

    useEffect(() => {
        if (!isAuthenticated) {
            setSessionDocScopes({})
            setPendingDocScope(null)
        }
    }, [isAuthenticated])

    useEffect(() => {
        const handleResize = () => setViewportWidth(window.innerWidth)
        window.addEventListener('resize', handleResize)
        return () => window.removeEventListener('resize', handleResize)
    }, [])

    useEffect(() => {
        if (typeof window === 'undefined') {
            return
        }
        window.localStorage.setItem(REPLY_MODEL_STORAGE_KEY, replyModelKey)
    }, [replyModelKey])

    useEffect(() => {
        setPreviewPanelWidth((prev) =>
            Math.min(Math.max(prev, minPreviewPanelWidth), maxPreviewPanelWidth),
        )
    }, [minPreviewPanelWidth, maxPreviewPanelWidth])

    // ========== 11. Wrapped Handlers ==========

    const handleSelectSkill = useCallback((skillId: string, skillName: string) => {
        setSelectedSkill({ id: skillId, name: skillName })
        setSelectedMode(null)
        setInputMessage('执行')
    }, [setInputMessage])

    const handleSelectMode = useCallback((mode: QuickToolMode) => {
        setSelectedMode(mode)
        setSelectedSkill(null)
    }, [])

    const handleOpenWikiSlug = useCallback((slug: string) => {
        const cleanSlug = slug.trim()
        if (!cleanSlug) return
        navigate(`/knowledge?view=wiki&wiki_slug=${encodeURIComponent(cleanSlug)}`)
    }, [navigate])

    // 发送消息（封装 actions.handleSend）
    // [FIX] 配置加载中时阻止发送，避免首条消息跳过配置
    const handleSend = useCallback(async () => {
        const message = inputMessage.trim()
        if (!message || isLoading || configLoading) return
        if (sendSubmitLockRef.current) return
        sendSubmitLockRef.current = true

        setInputMessage('')

        try {
            await actions.handleSend({
                message,
                uploadedFile,
                selectedMode,
                selectedSkillId: selectedSkill?.id ?? null,
                docScope: currentDocScope,
                replyModelKey,
                onAfterSend: () => {
                    setUploadedFile(null)
                    setSelectedSkill(null)
                },
                onSessionResolved: (resolvedSessionId) => {
                    if (!currentSessionId && pendingDocScope) {
                        setSessionDocScopes((prev) => ({
                            ...prev,
                            [resolvedSessionId]: pendingDocScope,
                        }))
                        setPendingDocScope(null)
                    }
                },
            })
        } finally {
            window.setTimeout(() => {
                sendSubmitLockRef.current = false
            }, 600)
        }
    }, [
        inputMessage,
        isLoading,
        configLoading,
        uploadedFile,
        selectedMode,
        selectedSkill,
        currentDocScope,
        replyModelKey,
        currentSessionId,
        pendingDocScope,
        actions,
        setInputMessage,
    ])

    const handleDocScopeChange = useCallback((scope: DocScope | null) => {
        if (currentSessionId) {
            setSessionDocScopes((prev) => ({
                ...prev,
                [currentSessionId]: scope,
            }))
            return
        }
        setPendingDocScope(scope)
    }, [currentSessionId])

    // 中断提交（封装 actions.handleInterruptSubmit）
    const handleInterruptSubmit = useCallback(async (input: string) => {
        if (!interruptData) return

        setIsSubmittingInterrupt(true)
        try {
            await actions.handleInterruptSubmit(input, interruptData)
            setInterruptData(null)
        } finally {
            setIsSubmittingInterrupt(false)
        }
    }, [interruptData, actions])

    // 中断取消
    const handleInterruptCancel = useCallback(() => {
        setInterruptData(null)
    }, [])

    // ========== 12. Render ==========
    return (
        <div className="flex-1 flex flex-col h-full bg-manus relative overflow-hidden">
            <div
                className="absolute left-0 right-0 top-5 z-40 pointer-events-none"
                style={{
                    left: `${sidebarCollapsed ? 48 : 24}px`,
                }}
            >
                <div className="pointer-events-auto w-fit">
                        <ReplyModelSelector
                            value={replyModelKey}
                            onChange={setReplyModelKey}
                            disabled={isLoading || configLoading || planner.isExecuting}
                        />
                </div>
            </div>

            {isWelcomeMode ? (
                <WelcomeScene
                    inputMessage={inputMessage}
                    setInputMessage={setInputMessage}
                    isLoading={isLoading || configLoading}
                    isExecuting={planner.isExecuting}
                    currentSessionId={currentSessionId}
                    selectedMode={selectedMode}
                    userExecutionMode={userExecutionMode}
                    docScope={currentDocScope}
                    hasData={hasRuntimeData}
                    hasDb={hasDb}
                    hasKnowledge={hasKnowledge}
                    readinessAvailable={readinessAvailable}
                    onSend={handleSend}
                    onSelectMode={handleSelectMode}
                    onRemoveMode={() => setSelectedMode(null)}
                    selectedSkill={selectedSkill}
                    onSelectSkill={handleSelectSkill}
                    onRemoveSkill={() => setSelectedSkill(null)}
                    onDocScopeChange={handleDocScopeChange}
                    onFileChange={setUploadedFile}
                    onError={(msg) => console.error('[ChatPage] 上传错误:', msg)}
                />
            ) : (
                <ChatScene
                    messages={messages}
                    roundArtifacts={roundArtifacts}
                    planner={planner}
                    interruptData={interruptData}
                    isSubmittingInterrupt={isSubmittingInterrupt}
                    inputMessage={inputMessage}
                    setInputMessage={setInputMessage}
                    isLoading={isLoading || configLoading}
                    currentSessionId={currentSessionId}
                    selectedMode={selectedMode}
                    userExecutionMode={userExecutionMode}
                    docScope={currentDocScope}
                    scopeConflictMessage={scopeConflictMessage}
                    rightInset={previewRightInset}
                    hasData={hasRuntimeData}
                    hasDb={hasDb}
                    hasKnowledge={hasKnowledge}
                    readinessAvailable={readinessAvailable}
                    onSend={handleSend}
                    onConfirmPlan={actions.handleConfirmPlan}
                    onCancelPlan={actions.handleCancelPlan}
                    onInterruptSubmit={handleInterruptSubmit}
                    onInterruptCancel={handleInterruptCancel}
                    onPreviewFile={(id, name, type, page, anchor) => setPreviewFile({ id, name, type, page, anchor, _ts: Date.now() })}
                    onPreviewDataFrame={(dfKey) => setPreviewDataFrame(dfKey)}
                    onOpenWikiSlug={handleOpenWikiSlug}
                    onShowHtmlReport={(report) => {
                        setHtmlReport(report)
                        setShowHtmlReport(true)
                    }}
                    onFileChange={setUploadedFile}
                    onSelectMode={handleSelectMode}
                    onRemoveMode={() => setSelectedMode(null)}
                    selectedSkill={selectedSkill}
                    onSelectSkill={handleSelectSkill}
                    onRemoveSkill={() => setSelectedSkill(null)}
                    onDocScopeChange={handleDocScopeChange}
                    onError={(msg) => console.error('[ChatPage] 上传错误:', msg)}
                />
            )}

            {/* 文件预览侧边栏 */}
            <FilePreviewSheet
                isOpen={!!previewFile}
                onClose={() => setPreviewFile(null)}
                fileId={previewFile?.id || null}
                fileName={previewFile?.name || null}
                fileType={previewFile?.type || null}
                initialPage={previewFile?.page}
                initialAnchor={previewFile?.anchor}
                requestKey={previewFile?._ts}
                sessionId={currentSessionId}
                panelWidth={clampedPreviewPanelWidth}
                onPanelWidthChange={setPreviewPanelWidth}
                minPanelWidth={minPreviewPanelWidth}
                maxPanelWidth={maxPreviewPanelWidth}
            />

            {/* DataFrame 预览侧边栏 */}
            <DataFramePreview
                isOpen={!!previewDataFrame}
                onClose={() => setPreviewDataFrame(null)}
                sessionId={currentSessionId || ''}
                dfKey={previewDataFrame || ''}
            />

            {/* HTML 可视化报告面板 */}
            {htmlReport && (
                <HtmlReportPanel
                    report={htmlReport}
                    isOpen={showHtmlReport}
                    onClose={() => setShowHtmlReport(false)}
                />
            )}
        </div>
    )
}
