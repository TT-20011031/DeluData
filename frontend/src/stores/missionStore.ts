/**
 * 任务状态管理 (Mission Store)
 * 
 * 管理遥测轨事件：步骤更新、思考日志、Artifact
 */
import { create } from 'zustand'
import { generateId } from '@/utils/id'

// ========== 类型定义 ==========

export interface Step {
    id: string
    status: 'pending' | 'running' | 'completed' | 'failed'
    label: string
    timestamp: string
}

export interface ThinkingLog {
    id: string
    content: string
    timestamp: string
}

export interface Artifact {
    id: string
    type: 'chart' | 'table' | 'code' | 'document'
    title: string
    data: Record<string, unknown>
    timestamp: string
}

interface MissionState {
    // 当前任务状态
    isActive: boolean
    steps: Step[]
    thinkingLogs: ThinkingLog[]
    artifacts: Artifact[]
    error: string | null

    // 面板状态
    isPanelOpen: boolean
    activeTab: 'steps' | 'thinking' | 'artifacts'

    // Actions
    startMission: () => void
    endMission: () => void
    reset: () => void

    updateStep: (step: Step) => void
    addThinkingLog: (content: string) => void
    addArtifact: (artifact: Omit<Artifact, 'id' | 'timestamp'>) => void
    setError: (error: string | null) => void

    togglePanel: () => void
    setActiveTab: (tab: 'steps' | 'thinking' | 'artifacts') => void
}

// ========== Store 实现 ==========

export const useMissionStore = create<MissionState>((set) => ({
    isActive: false,
    steps: [],
    thinkingLogs: [],
    artifacts: [],
    error: null,
    isPanelOpen: true,
    activeTab: 'steps',

    startMission: () => set({
        isActive: true,
        steps: [],
        thinkingLogs: [],
        error: null,
    }),

    endMission: () => set({ isActive: false }),

    reset: () => set({
        isActive: false,
        steps: [],
        thinkingLogs: [],
        artifacts: [],
        error: null,
    }),

    updateStep: (step) => set((state) => {
        const existingIndex = state.steps.findIndex((s) => s.id === step.id)

        if (existingIndex >= 0) {
            // 更新现有步骤
            const newSteps = [...state.steps]
            newSteps[existingIndex] = { ...step, timestamp: new Date().toISOString() }
            return { steps: newSteps }
        } else {
            // 添加新步骤
            return {
                steps: [...state.steps, { ...step, timestamp: new Date().toISOString() }],
            }
        }
    }),

    addThinkingLog: (content) => set((state) => ({
        thinkingLogs: [
            ...state.thinkingLogs,
            {
                id: generateId(),
                content,
                timestamp: new Date().toISOString(),
            },
        ],
    })),

    addArtifact: (artifact) => set((state) => ({
        artifacts: [
            ...state.artifacts,
            {
                ...artifact,
                id: generateId(),
                timestamp: new Date().toISOString(),
            },
        ],
    })),

    setError: (error) => set({ error }),

    togglePanel: () => set((state) => ({ isPanelOpen: !state.isPanelOpen })),

    setActiveTab: (activeTab) => set({ activeTab }),
}))
