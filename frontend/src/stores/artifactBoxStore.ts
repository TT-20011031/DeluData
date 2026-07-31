/**
 * 临时产物暂存箱状态管理
 */
import { create } from 'zustand'
import { artifactApi, type TempArtifact } from '@/api/artifactApi'

interface ArtifactBoxState {
    isOpen: boolean
    artifacts: TempArtifact[]
    isLoading: boolean
    error: string | null

    open: () => void
    close: () => void
    toggle: () => void

    fetchArtifacts: () => Promise<void>
    deleteArtifact: (id: string) => Promise<void>
    notifyNewArtifact: () => void   // 新产物生成时触发刷新提示
}

export const useArtifactBoxStore = create<ArtifactBoxState>((set, get) => ({
    isOpen: false,
    artifacts: [],
    isLoading: false,
    error: null,

    open: () => {
        set({ isOpen: true })
        get().fetchArtifacts()
    },
    close: () => set({ isOpen: false }),
    toggle: () => {
        const { isOpen } = get()
        if (!isOpen) {
            set({ isOpen: true })
            get().fetchArtifacts()
        } else {
            set({ isOpen: false })
        }
    },

    fetchArtifacts: async () => {
        set({ isLoading: true, error: null })
        try {
            const items = await artifactApi.list()
            set({ artifacts: items, isLoading: false })
        } catch (e) {
            set({ error: String(e), isLoading: false })
        }
    },

    deleteArtifact: async (id) => {
        await artifactApi.delete(id)
        set((state) => ({
            artifacts: state.artifacts.filter((a) => a.id !== id),
        }))
    },

    notifyNewArtifact: () => {
        // 无论面板是否打开都拉取一次，保证徽章和列表及时更新。
        // 再次延迟重试一次，覆盖极端情况下的写盘/索引延迟。
        void get().fetchArtifacts()
        setTimeout(() => {
            void get().fetchArtifacts()
        }, 800)
    },
}))
