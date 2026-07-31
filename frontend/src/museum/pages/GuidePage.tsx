/**
 * 博物馆导览页面 (v2.0)
 * 
 * 双栏布局：
 * - 左侧：智能客群洞察面板（场景分析）
 * - 右侧：语音导览交互区（大屏风格）
 * 
 * 使用 Manus 设计系统，语音输入替代文字输入
 */
import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { Landmark, Volume2, VolumeX, ImagePlus, Camera, PanelLeftClose, Users, Sparkles, ShoppingBag, X, MessageSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useGuideStore } from '../stores/guideStore'
import { useGuideSSE } from '../hooks/useGuideSSE'
import { useAudioPlayer } from '../hooks/useAudioPlayer'
import { AudioPlayer } from '../components/AudioPlayer'
import { AnalysisSidebar } from '../components/AnalysisSidebar'
import { VoiceInput } from '../components/VoiceInput'
import { CameraCapture } from '../components/CameraCapture'
import { MultimodalOutput } from '../components/MultimodalOutput'
import { SettingsPanel } from '../components/SettingsPanel'
import { TextInput } from '../components/TextInput'
import { MuseumLoader } from '../components/MuseumLoader'

// 欢迎语
const WELCOME_TEXT = '我是博物馆智能导览员'

export default function GuidePage() {
    const navigate = useNavigate()
    const [displayedText, setDisplayedText] = useState('')
    const [showCursor, setShowCursor] = useState(true)
    const [showSidebar, setShowSidebar] = useState(false) // 默认隐藏侧边栏
    const messagesEndRef = useRef<HTMLDivElement>(null)
    const fileInputRef = useRef<HTMLInputElement>(null)

    // 使用 ref 跟踪最新的 sessionId，避免闭包捕获旧值
    const sessionIdRef = useRef<string | null>(null)

    const {
        sessionId,
        deptId,  // 部门ID（知识库隔离）
        sceneAnalysis,
        targetPerson,
        isAnalyzing,
        messages,
        status, // v2.2
        enableTts,
        setEnableTts,
        setIsPlaying,
        setCurrentAudioText,
        setSceneAnalysis,
        clearAnalysisSteps,
        // v2.1: 图片暂存（等待语音输入）
        pendingPreviewUrl,
        setPendingImage,
        clearPendingImage,
        // v2.1: 多模态输出
        searchImages,
        recommendedProducts,
        clearMultimodalOutput,
    } = useGuideStore()

    // TTS 音频播放器
    const audioPlayer = useAudioPlayer({
        sampleRate: 24000,
        onPlayStart: () => setIsPlaying(true),
        onPlayEnd: () => {
            setIsPlaying(false)
            setCurrentAudioText('')
        },
    })

    // SSE 导览连接
    const { startGuide, continueChat } = useGuideSSE({
        onTTSChunk: (chunk) => {
            if (enableTts && chunk.audio_base64) {
                audioPlayer.enqueue(chunk)
            }
        },
        onError: (err) => {
            console.error('[GuidePage] SSE 错误:', err)
        },
    })

    // 同步 sessionId 到 ref（避免闭包捕获旧值）
    useEffect(() => {
        sessionIdRef.current = sessionId
        console.log('[GuidePage] sessionId 更新:', sessionId)
    }, [sessionId])

    // 场景图片上传分析（侧边栏使用）
    const handleSceneImageUpload = async (file: File) => {
        // 打断当前 TTS 播放
        audioPlayer.stop()
        clearAnalysisSteps()
        setSceneAnalysis(null)
        await startGuide({ image: file, enableTts, deptId: deptId || undefined })
    }

    // 语音识别结果处理 - 携带暂存图片一起发送
    const handleVoiceTranscribe = async (text: string) => {
        if (!text.trim()) return

        // 防止重复提交 (思考/流式中禁止)
        if (status === 'THINKING' || status === 'STREAMING') return

        // 打断当前 TTS 播放，清空上一轮音频队列
        audioPlayer.stop()

        // 获取暂存的图片（如果有）
        const pendingFile = pendingImageFile.current
        pendingImageFile.current = null
        clearPendingImage()

        clearMultimodalOutput()

        // 使用 ref 获取最新的 sessionId（避免闭包问题）
        const currentSessionId = sessionIdRef.current
        console.log('[GuidePage] handleVoiceTranscribe, sessionId=', currentSessionId, 'text=', text.slice(0, 20))

        if (currentSessionId) {
            // 继续对话：发送文本 + 可选图片（用于VL检索，不做人物识别）
            console.log('[GuidePage] -> continueChat')
            await continueChat({
                sessionId: currentSessionId,
                message: text,
                enableTts,
                deptId: deptId || undefined,
            })
        } else {
            // 新对话：发送文本 + 可选图片（用于VL检索，不做人物识别）
            console.log('[GuidePage] -> startGuide (new session)')
            await startGuide({
                query: text,
                image: pendingFile || undefined,
                enableTts,
                deptId: deptId || undefined,
                skipPersonRecognition: true  // 对话区图片不做人物识别
            })
        }
    }

    // 抓拍/上传图片处理（主对话区）- 暂存图片等待语音输入
    const pendingImageFile = useRef<File | null>(null)

    const handleChatImageCapture = async (file: File) => {
        // 暂存图片，等待语音输入
        pendingImageFile.current = file
        const previewUrl = URL.createObjectURL(file)
        setPendingImage(file.name, previewUrl)
    }

    // 清除暂存图片
    const handleClearPendingImage = () => {
        if (pendingPreviewUrl) {
            URL.revokeObjectURL(pendingPreviewUrl)
        }
        pendingImageFile.current = null
        clearPendingImage()
    }

    // v2.1: "听更详细的"处理 - 打断TTS + 发起深入讲解请求
    const handleDetailedExplanation = async () => {
        const currentSessionId = sessionIdRef.current
        if (!currentSessionId) {
            console.log('[GuidePage] handleDetailedExplanation blocked, sessionId=', currentSessionId)
            return
        }
        if (status === 'THINKING' || status === 'STREAMING') return

        console.log('[GuidePage] handleDetailedExplanation, sessionId=', currentSessionId)

        // 打断当前 TTS 播放
        audioPlayer.stop()

        // 清除多模态输出（新一轮对话）
        clearMultimodalOutput()

        // 发起深入讲解请求
        await continueChat({
            sessionId: currentSessionId,
            message: "请更详细地讲解一下刚才提到的内容，像讲故事一样，生动有趣地介绍",
            enableTts,
            deptId: deptId || undefined,
        })
    }

    const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]
        if (file && file.type.startsWith('image/')) {
            handleChatImageCapture(file)
        }
        e.target.value = ''
    }

    // 打字机效果
    useEffect(() => {
        if (messages.length > 0) return
        setDisplayedText('')
        let index = 0
        const timer = setInterval(() => {
            if (index < WELCOME_TEXT.length) {
                setDisplayedText(WELCOME_TEXT.slice(0, index + 1))
                index++
            } else {
                clearInterval(timer)
            }
        }, 80)
        return () => clearInterval(timer)
    }, [messages.length])

    // 光标闪烁
    useEffect(() => {
        const cursorTimer = setInterval(() => {
            setShowCursor(prev => !prev)
        }, 530)
        return () => clearInterval(cursorTimer)
    }, [])

    // 消息列表滚动到底部
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages])

    const isWelcomeMode = messages.length === 0

    // 从场景分析获取显示信息
    const displayTone = sceneAnalysis?.suggested_tone
    const displayStrategy = sceneAnalysis?.engagement_strategy

    return (
        <div className="flex-1 flex h-full bg-manus">
            {/* 左侧边栏：智能客群洞察（可隐藏） */}
            {showSidebar && (
                <AnalysisSidebar
                    onImageUpload={handleSceneImageUpload}
                    isAnalyzing={isAnalyzing}
                    className="w-72 shrink-0"
                />
            )}

            {/* 右侧主区域 */}
            <div className="flex-1 flex flex-col min-w-0">
                {/* 顶部状态栏 */}
                <header className="h-14 flex items-center justify-between px-6 border-b border-manus-border shrink-0">
                    <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-lg bg-accent/20 flex items-center justify-center">
                            <Landmark className="h-4 w-4 text-accent" />
                        </div>
                        <div>
                            <h1 className="text-sm font-medium text-manus-text">博物馆智能导览</h1>
                            {targetPerson && (
                                <div className="flex items-center gap-1.5 text-xs text-manus-muted">
                                    <span className="px-1.5 py-0.5 bg-accent/10 text-accent rounded">
                                        {targetPerson.role_label}
                                    </span>
                                    {displayTone && (
                                        <span className="text-manus-subtle">· {displayTone}</span>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {/* 客群洞察切换 */}
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setShowSidebar(!showSidebar)}
                            className={cn(
                                "h-8 w-8 rounded-lg",
                                showSidebar ? "text-accent bg-accent/10" : "text-manus-muted hover:text-manus-text"
                            )}
                            title={showSidebar ? "隐藏客群洞察" : "显示客群洞察"}
                        >
                            {showSidebar ? <PanelLeftClose className="h-4 w-4" /> : <Users className="h-4 w-4" />}
                        </Button>
                        {/* TTS 切换 */}
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setEnableTts(!enableTts)}
                            className={cn(
                                "h-8 w-8 rounded-lg",
                                enableTts ? "text-accent bg-accent/10" : "text-manus-muted hover:text-manus-text"
                            )}
                            title={enableTts ? "关闭语音播报" : "开启语音播报"}
                        >
                            {enableTts ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
                        </Button>
                        {/* 知识库设置 */}
                        <SettingsPanel />
                    </div>
                </header>

                {/* TTS 播放器 */}
                {enableTts && audioPlayer.isPlaying && (
                    <AudioPlayer
                        isPlaying={audioPlayer.isPlaying}
                        currentText={audioPlayer.currentText}
                        queueLength={audioPlayer.queueLength}
                        onPause={audioPlayer.pause}
                        onResume={audioPlayer.resume}
                        onStop={audioPlayer.stop}
                        className="mx-6 mt-3"
                    />
                )}

                {/* 策略提示条（有分析结果时显示） */}
                {displayStrategy && (
                    <div className="mx-6 mt-3 p-3 bg-accent/5 border border-accent/20 rounded-xl">
                        <p className="text-sm text-manus-text">
                            <span className="text-accent font-medium">💡 建议：</span>
                            {displayStrategy}
                        </p>
                    </div>
                )}

                {/* 主内容区 - 相对定位容器 (BUG3 修复: 添加过渡动画) */}
                <div className="flex-1 flex flex-col items-center px-6 md:px-16 lg:px-24 relative pb-32 overflow-hidden transition-all duration-300">
                    {/* AI 回复内容 - 全宽布局，可滚轮滑动 */}
                    <div
                        className="flex-1 w-full max-w-6xl py-4 overflow-y-auto transition-all duration-300 flex flex-col"
                        style={{ scrollbarWidth: 'thin', scrollbarColor: 'rgba(255,255,255,0.2) transparent' }}
                    >
                        {isWelcomeMode ? (
                            // 欢迎语 - 居中显示
                            <div className="h-full flex items-center justify-center">
                                <h1 className="text-3xl font-light text-manus-text tracking-tight">
                                    {displayedText}
                                    <span
                                        className={cn(
                                            "inline-block w-0.5 h-7 bg-accent ml-1 align-middle transition-opacity",
                                            showCursor ? 'opacity-100' : 'opacity-0'
                                        )}
                                    />
                                </h1>
                            </div>
                        ) : (
                            // 最新 AI 回复 + 多模态输出 + 操作按钮 (BUG3 修复: 条件性垂直居中 -> 方案一: 智能居中)
                            <div className={cn(
                                "w-full max-w-5xl mx-auto animate-in fade-in duration-500 transition-all",
                                // 核心魔法：my-auto 实现内容少时居中，内容多时正常显示
                                "my-auto"
                            )}>
                                {(() => {
                                    const lastAssistantMsg = [...messages].reverse().find(m => m.role === 'assistant')

                                    // v2.2: 使用明确的状态机判断 Loading (BUG3 修复: 居中包裹)
                                    if (status === 'THINKING') {
                                        return (
                                            <div className="min-h-full flex items-center justify-center">
                                                <MuseumLoader text="正在查阅典籍..." />
                                            </div>
                                        )
                                    }

                                    if (!lastAssistantMsg) return null

                                    return (
                                        <>
                                            {/* AI 文字回复 - 简洁美观，居中显示 */}
                                            <div className="max-w-none mx-auto text-center">
                                                <ReactMarkdown
                                                    components={{
                                                        p: ({ children }) => (
                                                            <p className="text-lg md:text-2xl text-manus-text/90 leading-loose mb-6" style={{ wordBreak: 'keep-all' }}>
                                                                {children}
                                                            </p>
                                                        ),
                                                        strong: ({ children }) => (
                                                            <strong className="font-semibold text-amber-300">{children}</strong>
                                                        ),
                                                        ul: ({ children }) => (
                                                            <ul className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4 my-6">{children}</ul>
                                                        ),
                                                        li: ({ children }) => (
                                                            <li className="text-lg md:text-xl text-manus-text/80 flex items-start gap-2">
                                                                <span className="text-amber-400/70 mt-1.5">▸</span>
                                                                <span>{children}</span>
                                                            </li>
                                                        ),
                                                        hr: () => (
                                                            <div className="my-5 flex items-center justify-center gap-3">
                                                                <span className="h-px w-12 bg-gradient-to-r from-transparent to-amber-400/30" />
                                                                <span className="text-amber-400/50 text-xs">◆</span>
                                                                <span className="h-px w-12 bg-gradient-to-l from-transparent to-amber-400/30" />
                                                            </div>
                                                        ),
                                                    }}
                                                >
                                                    {lastAssistantMsg.content}
                                                </ReactMarkdown>
                                                {/* 流式生成光标 */}
                                                {status === 'STREAMING' && (
                                                    <span className="inline-block w-0.5 h-6 ml-1 bg-accent animate-pulse align-middle" />
                                                )}
                                            </div>

                                            {/* v2.1: 多模态输出（检索图片 + 文创推荐） */}
                                            <MultimodalOutput
                                                images={searchImages}
                                                products={recommendedProducts}
                                                onProductClick={(productId) => {
                                                    navigate(`/museum/shop?product=${productId}`)
                                                }}
                                                className="mt-6"
                                            />

                                            {/* v2.1: 对话后操作按钮 - 仅在交互态或空闲态显示 */}
                                            {(status === 'INTERACTIVE' || status === 'IDLE') && messages.length > 0 && (
                                                <div className="flex gap-3 mt-6 justify-center animate-in fade-in slide-in-from-bottom-2 duration-300">
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        onClick={handleDetailedExplanation}
                                                        className="flex items-center gap-2"
                                                    >
                                                        <Sparkles className="h-4 w-4" />
                                                        听更详细的
                                                    </Button>
                                                    <Button
                                                        variant="default"
                                                        size="sm"
                                                        onClick={() => navigate('/museum/shop')}
                                                        className="flex items-center gap-2"
                                                    >
                                                        <ShoppingBag className="h-4 w-4" />
                                                        文创商城
                                                    </Button>
                                                </div>
                                            )}
                                        </>
                                    )
                                })()}
                                <div ref={messagesEndRef} />
                            </div>
                        )}
                    </div>

                    {/* v2.1: 暂存图片预览 - 等待语音输入 */}
                    {pendingPreviewUrl && (
                        <div className="flex items-center justify-center gap-3 py-3 px-4 bg-manus-secondary/50 rounded-lg mx-8 mb-4">
                            <img
                                src={pendingPreviewUrl}
                                alt="待发送图片"
                                className="w-16 h-16 object-cover rounded-md border border-manus-border"
                            />
                            <div className="flex-1 text-sm text-manus-text">
                                <p className="font-medium">图片已暂存</p>
                                <p className="text-manus-muted text-xs">请语音描述您想了解的内容</p>
                            </div>
                            <button
                                onClick={handleClearPendingImage}
                                className="p-1.5 rounded-full hover:bg-manus-tertiary text-manus-muted hover:text-manus-text transition-colors"
                                title="移除图片"
                            >
                                <X className="h-4 w-4" />
                            </button>
                        </div>
                    )}

                    {/* 底部输入区 - 绝对定位在底部 */}
                    <div className="absolute bottom-0 left-0 right-0 pb-6 pt-4 bg-gradient-to-t from-manus-bg via-manus-bg to-transparent">
                        <div className="flex flex-col items-center">
                            <div className="flex items-start justify-center gap-6 md:gap-10 w-fit">
                                {/* 占位符：为了让第二个按钮（Voice）在视觉上居中，我们在最左侧加一个等宽的占位 */}
                                <div className="w-20 shrink-0" aria-hidden="true" />

                                <div className="flex flex-col items-center w-20">
                                    <div className="h-20 flex items-center justify-center">
                                        <CameraCapture
                                            onCapture={handleChatImageCapture}
                                            size="md"
                                            disabled={status === 'THINKING' || status === 'STREAMING' || isAnalyzing}
                                        />
                                    </div>
                                    <span className="text-xs text-manus-muted flex items-center gap-1 h-6">
                                        <Camera className="h-3 w-3" /> 抓拍
                                    </span>
                                </div>

                                {/* 中：语音 */}
                                <div className="flex flex-col items-center w-20">
                                    <div className="h-20 flex items-center justify-center">
                                        <VoiceInput
                                            onTranscribe={handleVoiceTranscribe}
                                            onError={(err) => console.error('[Voice] 错误:', err)}
                                            disabled={status === 'THINKING' || status === 'STREAMING' || isAnalyzing}
                                            showIdleHint={false}
                                        />
                                    </div>
                                    <span className="text-xs text-manus-muted flex items-center justify-center h-6">
                                        按住说话
                                    </span>
                                </div>

                                {/* 右：上传 */}
                                <div className="flex flex-col items-center w-20">
                                    <div className="h-20 flex items-center justify-center">
                                        <button
                                            onClick={() => fileInputRef.current?.click()}
                                            disabled={status === 'THINKING' || status === 'STREAMING' || isAnalyzing}
                                            className={cn(
                                                "w-14 h-14 rounded-full flex items-center justify-center transition-all duration-200",
                                                "bg-manus-secondary hover:bg-manus-tertiary border border-manus-border",
                                                (status === 'THINKING' || status === 'STREAMING' || isAnalyzing) && "opacity-50 cursor-not-allowed"
                                            )}
                                            title="上传图片"
                                        >
                                            <ImagePlus className="h-6 w-6 text-manus-text" />
                                        </button>
                                    </div>
                                    <span className="text-xs text-manus-muted flex items-center gap-1 h-6">
                                        <ImagePlus className="h-3 w-3" /> 上传
                                    </span>
                                </div>

                                {/* 文字输入（调试用） */}
                                <div className="flex flex-col items-center w-20">
                                    <div className="h-20 flex items-center justify-center">
                                        <TextInput
                                            onSubmit={handleVoiceTranscribe}
                                            disabled={status === 'THINKING' || status === 'STREAMING' || isAnalyzing}
                                        />
                                    </div>
                                    <span className="text-xs text-manus-muted flex items-center gap-1 h-6">
                                        <MessageSquare className="h-3 w-3" /> 文字
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                {/* 隐藏的文件输入 */}
                <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={handleFileInputChange}
                />
            </div>
        </div>
    )
}
