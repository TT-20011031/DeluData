/**
 * 未授权页面（高级版）
 * 
 * 未登录用户直接访问受保护路由时显示此页面
 * 支持明暗模式：参照 LoginPage 使用 useLayoutStore
 * 包含：轨道动画 + 扫描线 + 渐变文字 + 粒子效果 + 高级毛玻璃卡片
 */
import { useEffect, useState } from 'react'
import { ShieldAlert, LogIn, Lock, KeyRound } from 'lucide-react'
import { Button } from '@/components/ui/button'
import FlickeringGrid from '@/components/ui/flickering-grid'
import { useLayoutStore } from '@/stores/layoutStore'

// ========== 悬浮粒子组件 ==========
function FloatingParticles({ isLight }: { isLight: boolean }) {
    const particles = Array.from({ length: 20 }, (_, i) => ({
        id: i,
        size: Math.random() * 3 + 1,
        x: Math.random() * 100,
        y: Math.random() * 100,
        duration: Math.random() * 8 + 6,
        delay: Math.random() * 5,
        opacity: Math.random() * 0.3 + 0.1,
    }))

    return (
        <div className="absolute inset-0 overflow-hidden pointer-events-none z-[5]">
            {particles.map(p => (
                <div
                    key={p.id}
                    className="absolute rounded-full"
                    style={{
                        width: p.size,
                        height: p.size,
                        left: `${p.x}%`,
                        top: `${p.y}%`,
                        background: isLight
                            ? `radial-gradient(circle, rgba(220,38,38,${p.opacity}) 0%, transparent 70%)`
                            : `radial-gradient(circle, rgba(239,68,68,${p.opacity}) 0%, transparent 70%)`,
                        animation: `floatParticle ${p.duration}s ease-in-out ${p.delay}s infinite`,
                    }}
                />
            ))}
        </div>
    )
}

// ========== 轨道环组件 ==========
function OrbitalRings({ isLight }: { isLight: boolean }) {
    const ringColor = isLight ? 'rgba(220,38,38,' : 'rgba(239,68,68,'
    const dotColor = isLight ? 'rgba(220,38,38,' : 'rgba(239,68,68,'

    return (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            {/* 外环 */}
            <div
                className="absolute rounded-full"
                style={{
                    width: 180,
                    height: 180,
                    border: `1px solid ${ringColor}${isLight ? '0.15)' : '0.1)'}`,
                    animation: 'orbitSpin 12s linear infinite',
                }}
            >
                <div
                    className="absolute -top-1 left-1/2 -translate-x-1/2 w-2 h-2 rounded-full"
                    style={{
                        background: `${dotColor}0.4)`,
                        boxShadow: `0 0 8px ${dotColor}0.4)`,
                    }}
                />
            </div>
            {/* 中环 */}
            <div
                className="absolute rounded-full"
                style={{
                    width: 140,
                    height: 140,
                    border: `1px solid ${ringColor}${isLight ? '0.2)' : '0.15)'}`,
                    animation: 'orbitSpin 8s linear infinite reverse',
                }}
            >
                <div
                    className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1.5 h-1.5 rounded-full"
                    style={{
                        background: `${dotColor}0.5)`,
                        boxShadow: `0 0 6px ${dotColor}0.5)`,
                    }}
                />
            </div>
            {/* 内环 */}
            <div
                className="absolute rounded-full"
                style={{
                    width: 100,
                    height: 100,
                    border: `1px solid ${ringColor}${isLight ? '0.25)' : '0.2)'}`,
                    animation: 'orbitSpin 6s linear infinite',
                }}
            >
                <div
                    className="absolute top-1/2 -right-1 -translate-y-1/2 w-1 h-1 rounded-full"
                    style={{ background: `${dotColor}0.6)` }}
                />
            </div>
        </div>
    )
}

// ========== 错误码显示 ==========
function ErrorCodeDisplay({ isLight }: { isLight: boolean }) {
    const [code, setCode] = useState('403')

    useEffect(() => {
        const glitchInterval = setInterval(() => {
            const chars = '40312567890ABCDEF'
            const glitched = Array.from({ length: 3 }, () => chars[Math.floor(Math.random() * chars.length)]).join('')
            setCode(glitched)
            setTimeout(() => setCode('403'), 80)
        }, 4000)
        return () => clearInterval(glitchInterval)
    }, [])

    return (
        <div className="text-center mb-2">
            <span
                className="text-[80px] font-black leading-none tracking-tighter"
                style={{
                    background: isLight
                        ? 'linear-gradient(135deg, #dc2626 0%, #ef4444 30%, #f87171 50%, #ef4444 70%, #dc2626 100%)'
                        : 'linear-gradient(135deg, #ef4444 0%, #f87171 30%, #fca5a5 50%, #f87171 70%, #ef4444 100%)',
                    backgroundSize: '200% 200%',
                    animation: 'gradientShift 3s ease infinite',
                    WebkitBackgroundClip: 'text',
                    WebkitTextFillColor: 'transparent',
                    filter: isLight
                        ? 'drop-shadow(0 0 20px rgba(220,38,38,0.2))'
                        : 'drop-shadow(0 0 30px rgba(239,68,68,0.3))',
                    fontFamily: "'Inter', 'SF Pro Display', system-ui, sans-serif",
                }}
            >
                {code}
            </span>
        </div>
    )
}

// ========== 主页面 ==========
export default function UnauthorizedPage() {
    const [mounted, setMounted] = useState(false)
    const theme = useLayoutStore((state) => state.theme)
    const isLight = theme === 'light'

    useEffect(() => {
        document.documentElement.classList.remove('light', 'dark')
        document.documentElement.classList.add(theme)
    }, [theme])

    useEffect(() => {
        setMounted(true)
    }, [])

    return (
        <div className={`min-h-screen flex items-center justify-center p-4 relative overflow-hidden w-full ${isLight ? 'bg-[#f3f5f9]' : 'bg-[#06060a]'}`}>
            {/* 背景网格 */}
            <div className="absolute inset-0 z-0">
                <FlickeringGrid
                    squareSize={3}
                    gridGap={8}
                    flickerChance={0.08}
                    color={isLight ? 'rgb(220, 38, 38)' : 'rgb(239, 68, 68)'}
                    maxOpacity={isLight ? 0.06 : 0.08}
                    className="w-full h-full"
                />
            </div>

            {/* 白天模式装饰光球 */}
            {isLight && (
                <>
                    <div className="absolute top-[-5%] left-[-5%] w-[40vw] h-[40vw] rounded-full filter blur-[100px] opacity-30 animate-pulse bg-red-200 pointer-events-none" style={{ animationDuration: '4s' }} />
                    <div className="absolute bottom-[-10%] right-[-10%] w-[40vw] h-[40vw] rounded-full filter blur-[120px] opacity-25 animate-pulse bg-rose-200 pointer-events-none" style={{ animationDuration: '6s' }} />
                    <div className="absolute top-[20%] left-[50%] w-[30vw] h-[30vw] rounded-full filter blur-[100px] opacity-15 animate-pulse bg-orange-200 pointer-events-none" style={{ animationDuration: '8s' }} />
                </>
            )}

            {/* 径向光晕 */}
            <div
                className="absolute z-[1] pointer-events-none"
                style={{
                    width: 800,
                    height: 800,
                    left: '50%',
                    top: '50%',
                    transform: 'translate(-50%, -50%)',
                    background: isLight
                        ? 'radial-gradient(ellipse at center, rgba(220,38,38,0.04) 0%, rgba(220,38,38,0.01) 40%, transparent 70%)'
                        : 'radial-gradient(ellipse at center, rgba(239,68,68,0.06) 0%, rgba(239,68,68,0.02) 40%, transparent 70%)',
                }}
            />

            {/* 扫描线 */}
            <div
                className="absolute inset-0 z-[2] pointer-events-none overflow-hidden"
                style={{ opacity: isLight ? 0.02 : 0.03 }}
            >
                <div
                    className="absolute w-full h-[2px]"
                    style={{
                        background: isLight
                            ? 'linear-gradient(90deg, transparent 0%, rgba(220,38,38,0.8) 50%, transparent 100%)'
                            : 'linear-gradient(90deg, transparent 0%, rgba(239,68,68,0.8) 50%, transparent 100%)',
                        animation: 'scanLine 4s ease-in-out infinite',
                    }}
                />
            </div>

            {/* 竖向渐变遮罩 */}
            <div className={`absolute inset-0 z-[3] pointer-events-none ${isLight ? 'bg-gradient-to-t from-[#f3f5f9] via-transparent to-[#f3f5f9]' : 'bg-gradient-to-t from-[#06060a] via-transparent to-[#06060a]'}`} />

            {/* 悬浮粒子 */}
            <FloatingParticles isLight={isLight} />

            {/* 主卡片 */}
            <div
                className="relative z-20 w-full max-w-md"
                style={{
                    opacity: mounted ? 1 : 0,
                    transform: mounted ? 'translateY(0) scale(1)' : 'translateY(20px) scale(0.96)',
                    transition: 'all 0.8s cubic-bezier(0.16, 1, 0.3, 1)',
                }}
            >
                <div className="relative">
                    {/* 卡片外发光 */}
                    <div
                        className="absolute -inset-[1px] rounded-3xl opacity-40"
                        style={{
                            background: isLight
                                ? 'linear-gradient(135deg, rgba(220,38,38,0.12) 0%, transparent 40%, transparent 60%, rgba(220,38,38,0.08) 100%)'
                                : 'linear-gradient(135deg, rgba(239,68,68,0.2) 0%, transparent 40%, transparent 60%, rgba(239,68,68,0.15) 100%)',
                        }}
                    />

                    {/* 卡片主体 */}
                    <div className={`relative backdrop-blur-2xl rounded-3xl p-8 overflow-hidden ${isLight ? 'bg-white/90 border border-black/[0.08] shadow-[0_20px_50px_-20px_rgba(0,0,0,0.15)]' : 'bg-white/[0.03] border border-white/[0.06]'}`}>
                        {/* 卡片内部光效 */}
                        <div
                            className="absolute top-0 left-0 right-0 h-[1px]"
                            style={{
                                background: isLight
                                    ? 'linear-gradient(90deg, transparent 0%, rgba(0,0,0,0.04) 30%, rgba(0,0,0,0.06) 50%, rgba(0,0,0,0.04) 70%, transparent 100%)'
                                    : 'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.08) 30%, rgba(255,255,255,0.12) 50%, rgba(255,255,255,0.08) 70%, transparent 100%)',
                            }}
                        />

                        {/* 图标区域 + 轨道环 */}
                        <div className="relative w-[200px] h-[200px] mx-auto mb-4">
                            <OrbitalRings isLight={isLight} />
                            <div className="absolute inset-0 flex items-center justify-center">
                                <div
                                    className="w-16 h-16 rounded-2xl flex items-center justify-center relative"
                                    style={{
                                        background: isLight
                                            ? 'linear-gradient(135deg, rgba(220,38,38,0.1) 0%, rgba(220,38,38,0.04) 100%)'
                                            : 'linear-gradient(135deg, rgba(239,68,68,0.15) 0%, rgba(239,68,68,0.05) 100%)',
                                        border: isLight ? '1px solid rgba(220,38,38,0.15)' : '1px solid rgba(239,68,68,0.2)',
                                        boxShadow: isLight
                                            ? '0 0 30px rgba(220,38,38,0.06), inset 0 1px 0 rgba(255,255,255,0.5)'
                                            : '0 0 40px rgba(239,68,68,0.1), inset 0 1px 0 rgba(255,255,255,0.05)',
                                    }}
                                >
                                    <ShieldAlert
                                        className={`h-8 w-8 ${isLight ? 'text-red-600' : 'text-red-400'}`}
                                        style={{ filter: isLight ? 'drop-shadow(0 0 6px rgba(220,38,38,0.3))' : 'drop-shadow(0 0 8px rgba(239,68,68,0.4))' }}
                                    />
                                </div>
                            </div>
                        </div>

                        {/* 错误码 */}
                        <ErrorCodeDisplay isLight={isLight} />

                        {/* 标题 */}
                        <div className="text-center mb-6">
                            <h1 className={`text-xl font-semibold mb-2 tracking-wide ${isLight ? 'text-zinc-900' : 'text-white/90'}`}>
                                访问受限
                            </h1>
                            <p className={`text-sm leading-relaxed ${isLight ? 'text-zinc-500' : 'text-white/40'}`}>
                                当前会话未经认证，需要登录后才能继续访问
                            </p>
                        </div>

                        {/* 信息卡片列表 */}
                        <div className="space-y-2 mb-6">
                            <div className={`flex items-center gap-3 rounded-xl px-4 py-3 transition-colors ${isLight ? 'border border-zinc-100 bg-zinc-50/80 hover:bg-zinc-100/80' : 'border border-white/[0.04] bg-white/[0.02] hover:bg-white/[0.04]'}`}>
                                <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${isLight ? 'bg-red-50' : 'bg-red-500/10'}`}>
                                    <Lock className={`h-4 w-4 ${isLight ? 'text-red-500' : 'text-red-400/70'}`} />
                                </div>
                                <div>
                                    <p className={`text-sm ${isLight ? 'text-zinc-700' : 'text-white/70'}`}>身份认证</p>
                                    <p className={`text-xs ${isLight ? 'text-zinc-400' : 'text-white/30'}`}>请使用账号密码完成登录验证</p>
                                </div>
                            </div>
                            <div className={`flex items-center gap-3 rounded-xl px-4 py-3 transition-colors ${isLight ? 'border border-zinc-100 bg-zinc-50/80 hover:bg-zinc-100/80' : 'border border-white/[0.04] bg-white/[0.02] hover:bg-white/[0.04]'}`}>
                                <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${isLight ? 'bg-blue-50' : 'bg-blue-500/10'}`}>
                                    <KeyRound className={`h-4 w-4 ${isLight ? 'text-blue-500' : 'text-blue-400/70'}`} />
                                </div>
                                <div>
                                    <p className={`text-sm ${isLight ? 'text-zinc-700' : 'text-white/70'}`}>权限获取</p>
                                    <p className={`text-xs ${isLight ? 'text-zinc-400' : 'text-white/30'}`}>登录后将自动获取您的操作权限</p>
                                </div>
                            </div>
                        </div>

                        {/* 按钮 */}
                        <Button
                            onClick={() => (window.location.href = '/login')}
                            className="w-full h-12 text-sm font-medium relative overflow-hidden group"
                            style={{
                                background: isLight
                                    ? 'linear-gradient(135deg, #dc2626 0%, #b91c1c 100%)'
                                    : 'linear-gradient(135deg, #ef4444 0%, #dc2626 100%)',
                                border: 'none',
                                boxShadow: isLight
                                    ? '0 4px 16px rgba(220,38,38,0.25), 0 2px 8px rgba(0,0,0,0.1)'
                                    : '0 0 20px rgba(239,68,68,0.2), 0 4px 12px rgba(0,0,0,0.3)',
                            }}
                        >
                            {/* 按钮光效 */}
                            <div
                                className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-500"
                                style={{
                                    background: isLight
                                        ? 'linear-gradient(135deg, #ef4444 0%, #dc2626 100%)'
                                        : 'linear-gradient(135deg, #f87171 0%, #ef4444 100%)',
                                }}
                            />
                            <span className="relative flex items-center justify-center gap-2 text-white">
                                <LogIn className="h-4 w-4" />
                                前往登录
                            </span>
                        </Button>

                        {/* 底部装饰线 */}
                        <div
                            className="mt-6 h-[1px] mx-8"
                            style={{
                                background: isLight
                                    ? 'linear-gradient(90deg, transparent 0%, rgba(0,0,0,0.06) 50%, transparent 100%)'
                                    : 'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.06) 50%, transparent 100%)',
                            }}
                        />
                        <p className={`text-center text-xs mt-3 tracking-widest uppercase ${isLight ? 'text-zinc-300' : 'text-white/20'}`}>
                            DeluData Security
                        </p>
                    </div>
                </div>
            </div>

            {/* 内联样式：动画关键帧 */}
            <style>{`
                @keyframes orbitSpin {
                    from { transform: rotate(0deg); }
                    to { transform: rotate(360deg); }
                }
                @keyframes gradientShift {
                    0%, 100% { background-position: 0% 50%; }
                    50% { background-position: 100% 50%; }
                }
                @keyframes scanLine {
                    0% { top: -2px; }
                    100% { top: 100%; }
                }
                @keyframes floatParticle {
                    0%, 100% { transform: translate(0, 0) scale(1); opacity: 0.3; }
                    25% { transform: translate(10px, -20px) scale(1.2); opacity: 0.6; }
                    50% { transform: translate(-5px, -40px) scale(0.8); opacity: 0.2; }
                    75% { transform: translate(15px, -20px) scale(1.1); opacity: 0.5; }
                }
            `}</style>
        </div>
    )
}
