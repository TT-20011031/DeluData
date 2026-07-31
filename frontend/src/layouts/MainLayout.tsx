/**
 * 主布局组件
 * 
 * 简化两栏布局：Sidebar + Content
 * 支持侧边栏收缩和主题切换（带高级动画）
 */
import { useEffect, useRef, useState, useMemo } from 'react'
import { Outlet } from 'react-router-dom'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import Sidebar from './Sidebar'
import { useAuthStore, getAuthHeader } from '@/stores/authStore'
import { useLayoutStore } from '@/stores/layoutStore'
import { useUploadStore } from '@/stores/uploadStore'
import { FloatingUploadQueue } from '@/components/knowledge/FloatingUploadQueue'
import { cn } from '@/lib/utils'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

// 网格配置
const GRID_COLS = 12
const GRID_ROWS = 8
const TOTAL_CELLS = GRID_COLS * GRID_ROWS

export default function MainLayout() {

    const { logout, isAuthenticated, syncCurrentUser } = useAuthStore()
    const { sidebarCollapsed, toggleSidebar, theme, themeTransition, finishThemeTransition } = useLayoutStore()


    // 使用 ref 避免重复检查
    const authChecked = useRef(false)

    // 动画状态
    const [isAnimating, setIsAnimating] = useState(false)
    const [overlayTheme, setOverlayTheme] = useState<'dark' | 'light' | null>(null)
    // 阶段：'in' (出现) | 'out' (消失) | null
    const [animPhase, setAnimPhase] = useState<'in' | 'out' | null>(null)

    // 计算每个格子的延迟
    const { gridDelays, maxDelay } = useMemo(() => {
        if (!themeTransition.x || !themeTransition.y) {
            return { gridDelays: new Array(TOTAL_CELLS).fill(0), maxDelay: 0 }
        }

        // 简单的按距离计算延迟
        const delays: number[] = []
        let max = 0
        const cellWidth = window.innerWidth / GRID_COLS
        const cellHeight = window.innerHeight / GRID_ROWS

        for (let i = 0; i < TOTAL_CELLS; i++) {
            const row = Math.floor(i / GRID_COLS)
            const col = i % GRID_COLS

            // 单元格中心点
            const cx = col * cellWidth + cellWidth / 2
            const cy = row * cellHeight + cellHeight / 2

            // 计算到点击位置的距离
            const dist = Math.hypot(cx - themeTransition.x, cy - themeTransition.y)
            // 归一化延迟 (加快速度: 0.00025)
            // 1920px 屏幕对角线约 2200px -> 0.55s max delay
            const delay = dist * 0.00025
            delays.push(delay)
            if (delay > max) max = delay
        }
        return { gridDelays: delays, maxDelay: max }
    }, [themeTransition.x, themeTransition.y])

    // 应用主题到 html 元素
    useEffect(() => {
        document.documentElement.classList.remove('light', 'dark')
        document.documentElement.classList.add(theme)
    }, [theme])

    // 处理主题切换动画
    useEffect(() => {
        if (themeTransition.isAnimating && themeTransition.targetTheme) {
            setOverlayTheme(themeTransition.targetTheme)
            setIsAnimating(true)

            // 下一帧触发动画，确保 DOM 先渲染
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    setAnimPhase('in')
                })
            })

            // 动态计算等待时间：最大延迟 + CSS动画时长(0.3s) + 小缓冲(0.05s)
            // 这样一旦全屏覆盖，立即切换主题并开始消失动画
            const switchDelay = (maxDelay * 1000) + 350

            const switchTimer = setTimeout(() => {
                // 切换主题
                finishThemeTransition()
                // 开始 'out' 动画
                setAnimPhase('out')
            }, switchDelay)

            // 清理时间：switchDelay + CSS动画时长(0.3s) + 缓冲
            const cleanupTimer = setTimeout(() => {
                setIsAnimating(false)
                setAnimPhase(null)
                setOverlayTheme(null)
            }, switchDelay + 400)

            return () => {
                clearTimeout(switchTimer)
                clearTimeout(cleanupTimer)
            }
        }
    }, [themeTransition.isAnimating, themeTransition.targetTheme, finishThemeTransition, maxDelay])

    // 首次加载时验证 token 是否有效
    useEffect(() => {
        if (authChecked.current || !isAuthenticated) return
        authChecked.current = true

        const checkAuth = async () => {
            try {
                const response = await fetch(`${API_BASE_URL}/auth/me`, {
                    headers: getAuthHeader(),
                })

                if (response.status === 401) {
                    logout()
                    setTimeout(() => {
                        window.location.href = '/login'
                    }, 0)
                } else if (response.ok) {
                    const latestUser = await response.json()
                    syncCurrentUser(latestUser)
                }
            } catch {
                // 网络错误，不处理
            }
        }

        checkAuth()
    }, [isAuthenticated, logout, syncCurrentUser])

    // 初始化尚未完成的上传任务（比如处理中）
    useEffect(() => {
        if (isAuthenticated) {
            useUploadStore.getState().initializeActiveTasks()
        }
    }, [isAuthenticated])

    // 计算侧边栏是否收缩
    const isCollapsed = sidebarCollapsed

    return (
        <div className="flex h-screen overflow-hidden bg-manus relative">
            {/* 像素风网格动画遮罩层 */}
            {isAnimating && overlayTheme && (
                <div className="fixed inset-0 z-[9999] pointer-events-none grid"
                    style={{
                        gridTemplateColumns: `repeat(${GRID_COLS}, 1fr)`,
                        gridTemplateRows: `repeat(${GRID_ROWS}, 1fr)`
                    }}
                >
                    {Array.from({ length: TOTAL_CELLS }).map((_, i) => (
                        <div
                            key={i}
                            className={cn(
                                "w-full h-full transition-all duration-300 ease-out transform origin-center",
                                overlayTheme === 'dark' ? 'bg-[#0d1117]' : 'bg-[#f8fafc]'
                            )}
                            style={{
                                transitionDelay: `${gridDelays[i]}s`,
                                opacity: animPhase === 'in' ? 1 : 0,
                                transform: animPhase === 'in' ? 'scale(1.05)' : 'scale(0)'
                            }}
                        />
                    ))}
                </div>
            )}

            {/* 侧边栏 */}
            <Sidebar collapsed={isCollapsed} />

            {/* 收缩切换按钮 */}
            <button
                onClick={toggleSidebar}
                className={cn(
                    "absolute top-4 z-50 p-1.5 rounded-full",
                    "bg-manus-secondary border border-manus-border",
                    "text-manus-muted hover:text-manus-text hover:bg-manus-hover",
                    "transition-all duration-300 shadow-sm",
                    isCollapsed ? "left-[4.5rem]" : "left-[15.5rem]"
                )}
                title={isCollapsed ? '展开侧边栏' : '收起侧边栏'}
            >
                {isCollapsed ? (
                    <ChevronRight className="h-4 w-4" />
                ) : (
                    <ChevronLeft className="h-4 w-4" />
                )}
            </button>

            {/* 主内容区 */}
            <main className="flex-1 flex flex-col overflow-hidden">
                <Outlet />
            </main>

            {/* 悬浮上传进度面板 */}
            <FloatingUploadQueue />
        </div>
    )
}
