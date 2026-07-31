/**
 * 首页欢迎场景组件
 * 
 * 职责：渲染首页欢迎界面
 * - 打字机效果欢迎语
 * - 居中输入框
 * - 快捷工具栏
 */
import { useEffect, useState } from 'react'
import { ChatInputArea, QuickToolBar } from '@/components/chat'
import type { QuickToolMode } from '@/components/chat/QuickToolModes'
import type { UploadedFile } from '@/types/chat'
import type { DocScope } from '@/types/docScope'

// ========== Props 接口定义 ==========
export interface WelcomeSceneProps {
    /** 输入消息内容 */
    inputMessage: string
    /** 设置输入消息 */
    setInputMessage: (message: string) => void
    /** 是否正在加载 */
    isLoading: boolean
    /** 是否正在执行 */
    isExecuting: boolean
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
    /** 发送消息回调 */
    onSend: () => void
    /** 选择模式回调 */
    onSelectMode: (mode: QuickToolMode) => void
    /** 移除模式回调 */
    onRemoveMode: () => void
    /** 选择技能回调 */
    onSelectSkill?: (skillId: string, skillName: string) => void
    /** 清除已选技能 */
    onRemoveSkill?: () => void
    /** 知识库范围切换 */
    onDocScopeChange: (scope: DocScope | null) => void
    /** 文件变化回调 */
    onFileChange: (file: UploadedFile | null) => void
    /** 错误处理回调 */
    onError: (msg: string) => void
}

// ========== 欢迎语配置 ==========
const WELCOME_TEXT = '我能为你做什么？'

/**
 * 首页欢迎场景
 * 
 * 在没有消息和任务计划时显示，包含打字机效果欢迎语和居中输入框
 */
export function WelcomeScene({
    inputMessage,
    setInputMessage,
    isLoading,
    isExecuting,
    currentSessionId,
    selectedMode,
    selectedSkill = null,
    userExecutionMode = 'auto',
    docScope = null,
    hasData = false,
    hasDb = false,
    hasKnowledge = false,
    readinessAvailable = false,
    onSend,
    onSelectMode,
    onRemoveMode,
    onSelectSkill,
    onRemoveSkill,
    onDocScopeChange,
    onFileChange,
    onError,
}: WelcomeSceneProps) {
    // ========== 打字机动画状态 ==========
    const [displayedText, setDisplayedText] = useState('')
    const [showCursor, setShowCursor] = useState(true)

    // 打字机效果
    useEffect(() => {
        setDisplayedText('')
        let index = 0
        const timer = setInterval(() => {
            if (index < WELCOME_TEXT.length) {
                setDisplayedText(WELCOME_TEXT.slice(0, index + 1))
                index++
            } else {
                clearInterval(timer)
            }
        }, 100)
        return () => clearInterval(timer)
    }, [])

    // 光标闪烁
    useEffect(() => {
        const cursorTimer = setInterval(() => {
            setShowCursor(prev => !prev)
        }, 530)
        return () => clearInterval(cursorTimer)
    }, [])

    // ========== Render ==========
    return (
        <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col items-center justify-center px-8 pb-12 pt-20">
            {/* 欢迎语（打字机效果） */}
            <h1 className="text-3xl font-light text-manus-text tracking-tight mb-8">
                {displayedText}
                <span
                    className={`inline-block w-0.5 h-7 bg-accent ml-1 align-middle transition-opacity ${showCursor ? 'opacity-100' : 'opacity-0'
                        }`}
                />
            </h1>

            {/* 居中的大输入框区域 */}
            <div className="w-full">
                <ChatInputArea
                    inputMessage={inputMessage}
                    setInputMessage={setInputMessage}
                    isLoading={isLoading}
                    isExecuting={isExecuting}
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
                {/* 快捷工具栏（居中） */}
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
    )
}
