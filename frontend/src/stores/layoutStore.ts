/**
 * 布局状态管理
 * 
 * 管理侧边栏收缩状态和主题切换
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type Theme = 'dark' | 'light'

interface ThemeTransition {
    isAnimating: boolean
    x: number
    y: number
    targetTheme: Theme | null
}

interface LayoutState {
    sidebarCollapsed: boolean
    theme: Theme
    themeTransition: ThemeTransition
    toggleSidebar: () => void
    setTheme: (theme: Theme) => void
    toggleTheme: () => void
    // 高级动画切换
    toggleThemeWithAnimation: (event: React.MouseEvent) => void
    finishThemeTransition: () => void
}

export const useLayoutStore = create<LayoutState>()(
    persist(
        (set, get) => ({
            sidebarCollapsed: false,
            theme: 'light',
            themeTransition: {
                isAnimating: false,
                x: 0,
                y: 0,
                targetTheme: null
            },
            toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
            setTheme: (theme) => set({ theme }),
            toggleTheme: () => set((state) => ({ theme: state.theme === 'dark' ? 'light' : 'dark' })),

            // 高级动画切换：从点击位置开始圆形扩散
            toggleThemeWithAnimation: (event: React.MouseEvent) => {
                const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
                const x = rect.left + rect.width / 2
                const y = rect.top + rect.height / 2
                const targetTheme = get().theme === 'dark' ? 'light' : 'dark'

                set({
                    themeTransition: {
                        isAnimating: true,
                        x,
                        y,
                        targetTheme
                    }
                })
            },

            finishThemeTransition: () => {
                const { themeTransition } = get()
                if (themeTransition.targetTheme) {
                    set({
                        theme: themeTransition.targetTheme,
                        themeTransition: {
                            isAnimating: false,
                            x: 0,
                            y: 0,
                            targetTheme: null
                        }
                    })
                }
            }
        }),
        {
            name: 'layout-storage',
            partialize: (state) => ({
                sidebarCollapsed: state.sidebarCollapsed,
                theme: state.theme
            })
        }
    )
)
