/**
 * 博物馆模块 - Zustand 状态管理
 */
import { create } from 'zustand'
import type {
    PersonType,
    GuideMessage,
    GuideSession,
    AdjustmentStep,
    SceneAnalysisResult,
    AnalysisStep,
    SceneImage,
    PersonInsight,
    GuideStatus,
} from '../types'

interface GuideState {
    // 会话状态
    session: GuideSession | null
    sessionId: string | null
    visitorUuid: string | null

    // 部门配置 (知识库隔离)
    deptId: string | null

    // 人物识别 (v1.0 兼容)
    personType: PersonType | null
    personFeatures: string[]
    isAnalyzing: boolean

    // 场景分析 (v2.0)
    sceneImage: SceneImage | null
    sceneAnalysis: SceneAnalysisResult | null
    analysisSteps: AnalysisStep[]
    targetPerson: PersonInsight | null
    highlightedPersonId: string | null  // 鼠标悬停高亮

    // 图片预上传暂存 (v2.1)
    pendingImageId: string | null
    pendingPreviewUrl: string | null

    // 多模态输出 (v2.1)
    searchImages: Array<{ fileId: string; imageId: string; caption?: string }>
    recommendedProducts: Array<{ id: string; name: string; price: number; image_url?: string | null }>

    // 调节过程
    adjustmentSteps: AdjustmentStep[]
    isAdjusting: boolean

    // 消息列表
    messages: GuideMessage[]
    isStreaming: boolean
    status: GuideStatus  // v2.2 状态机

    // TTS
    enableTts: boolean
    isPlaying: boolean
    currentAudioText: string

    // Actions - 会话
    setSession: (session: GuideSession | null) => void
    setPersonType: (type: PersonType, features?: string[]) => void
    setIsAnalyzing: (value: boolean) => void

    // Actions - 场景分析
    setSceneImage: (image: SceneImage | null) => void
    setSceneAnalysis: (result: SceneAnalysisResult | null) => void
    addAnalysisStep: (step: AnalysisStep) => void
    updateAnalysisStep: (stepId: string, updates: Partial<AnalysisStep>) => void
    clearAnalysisSteps: () => void
    setHighlightedPersonId: (id: string | null) => void

    // Actions - 图片预上传
    setPendingImage: (imageId: string | null, previewUrl: string | null) => void
    clearPendingImage: () => void

    // Actions - 多模态输出
    setSearchImages: (images: Array<{ fileId: string; imageId: string; caption?: string }>) => void
    setRecommendedProducts: (products: Array<{ id: string; name: string; price: number; image_url?: string | null }>) => void
    clearMultimodalOutput: () => void

    // Actions - 调节
    addAdjustmentStep: (step: AdjustmentStep) => void
    clearAdjustmentSteps: () => void

    // Actions - 消息
    addMessage: (message: GuideMessage) => void
    updateLastMessage: (content: string) => void
    finishLastMessage: () => void
    setIsStreaming: (value: boolean) => void
    setStatus: (status: GuideStatus) => void // v2.2

    // Actions - TTS
    setEnableTts: (value: boolean) => void
    setIsPlaying: (value: boolean) => void
    setCurrentAudioText: (text: string) => void

    // Actions - 部门配置
    setDeptId: (deptId: string | null) => void

    // Actions - 重置
    reset: () => void
}

const initialState = {
    session: null,
    sessionId: null,
    visitorUuid: null,
    deptId: localStorage.getItem('museum_dept_id') || null,  // 从 localStorage 恢复
    personType: null,
    personFeatures: [] as string[],
    isAnalyzing: false,
    // v2.0 场景分析
    sceneImage: null as SceneImage | null,
    sceneAnalysis: null as SceneAnalysisResult | null,
    analysisSteps: [] as AnalysisStep[],
    targetPerson: null as PersonInsight | null,
    highlightedPersonId: null as string | null,
    // v2.1 图片预上传暂存
    pendingImageId: null as string | null,
    pendingPreviewUrl: null as string | null,
    // v2.1 多模态输出
    searchImages: [] as Array<{ fileId: string; imageId: string; caption?: string }>,
    recommendedProducts: [] as Array<{ id: string; name: string; price: number; image_url?: string | null }>,
    // 调节
    adjustmentSteps: [] as AdjustmentStep[],
    isAdjusting: false,
    messages: [] as GuideMessage[],
    isStreaming: false,
    status: 'IDLE' as GuideStatus, // v2.2
    enableTts: true,
    isPlaying: false,
    currentAudioText: '',
}

export const useGuideStore = create<GuideState>((set) => ({
    ...initialState,

    setSession: (session) => set({
        session,
        sessionId: session?.id ?? null,
        visitorUuid: session?.visitor_uuid ?? null,
    }),

    setPersonType: (type, features = []) => set({
        personType: type,
        personFeatures: features,
        isAnalyzing: false,
    }),

    setIsAnalyzing: (value) => set({ isAnalyzing: value }),

    // v2.0 场景分析 Actions
    setSceneImage: (image) => set({ sceneImage: image }),

    setSceneAnalysis: (result) => set(() => {
        if (!result) {
            return { sceneAnalysis: null, targetPerson: null }
        }
        const target = result.persons.find(p => p.id === result.target_person_id) || result.persons[0]
        return {
            sceneAnalysis: result,
            targetPerson: target,
            personType: null,
            personFeatures: target?.visual_cues || [],
        }
    }),

    addAnalysisStep: (step) => set((state) => ({
        analysisSteps: [...state.analysisSteps, step],
    })),

    updateAnalysisStep: (stepId, updates) => set((state) => ({
        analysisSteps: state.analysisSteps.map(s =>
            s.step_id === stepId ? { ...s, ...updates } : s
        ),
    })),

    clearAnalysisSteps: () => set({ analysisSteps: [] }),

    setHighlightedPersonId: (id) => set({ highlightedPersonId: id }),

    // v2.1 图片预上传 Actions
    setPendingImage: (imageId, previewUrl) => set({
        pendingImageId: imageId,
        pendingPreviewUrl: previewUrl
    }),

    clearPendingImage: () => set({
        pendingImageId: null,
        pendingPreviewUrl: null
    }),

    // v2.1 多模态输出 Actions
    setSearchImages: (images) => set((state) => ({ 
        searchImages: [...state.searchImages, ...images]  // 追加模式，支持多图
    })),
    setRecommendedProducts: (products) => set({ recommendedProducts: products }),
    clearMultimodalOutput: () => set({ searchImages: [], recommendedProducts: [] }),

    addAdjustmentStep: (step) => set((state) => ({
        adjustmentSteps: [...state.adjustmentSteps, step],
        isAdjusting: step.progress < 100,
    })),

    clearAdjustmentSteps: () => set({ adjustmentSteps: [], isAdjusting: false }),

    addMessage: (message) => set((state) => ({
        messages: [...state.messages, message],
    })),

    updateLastMessage: (content) => set((state) => {
        const messages = [...state.messages]
        if (messages.length > 0) {
            const last = messages[messages.length - 1]
            messages[messages.length - 1] = { ...last, content: last.content + content }
        }
        return { messages }
    }),

    finishLastMessage: () => set((state) => {
        const messages = [...state.messages]
        if (messages.length > 0) {
            const last = messages[messages.length - 1]
            messages[messages.length - 1] = { ...last, isStreaming: false }
        }
        return { messages }
    }),

    setIsStreaming: (value) => set({ isStreaming: value }),
    setStatus: (status) => set({ status }), // v2.2

    setEnableTts: (value) => set({ enableTts: value }),

    setIsPlaying: (value) => set({ isPlaying: value }),

    setCurrentAudioText: (text) => set({ currentAudioText: text }),

    setDeptId: (deptId) => {
        // 持久化到 localStorage
        if (deptId) {
            localStorage.setItem('museum_dept_id', deptId)
        } else {
            localStorage.removeItem('museum_dept_id')
        }
        return set({ deptId })
    },

    reset: () => set(initialState),
}))
