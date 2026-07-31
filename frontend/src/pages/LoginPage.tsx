/**
 * 登录页面
 * 
 * FlickeringGrid 动态背景 + Glassmorphism 登录卡片
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogIn, Eye, EyeOff, Loader2, Mail } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import FlickeringGrid from '@/components/ui/flickering-grid'
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog"
import { useAuthStore } from '@/stores/authStore'
import { useLayoutStore } from '@/stores/layoutStore'

export default function LoginPage() {
    const navigate = useNavigate()
    const { login, isLoading, error } = useAuthStore()
    const theme = useLayoutStore((state) => state.theme)
    const isLight = theme === 'light'

    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [showPassword, setShowPassword] = useState(false)

    useEffect(() => {
        document.documentElement.classList.remove('light', 'dark')
        document.documentElement.classList.add(theme)
    }, [theme])

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault()

        const success = await login(username, password)

        if (success) {
            navigate('/')
        }
    }

    return (
        <div className={`min-h-screen flex items-center justify-center p-4 relative overflow-hidden w-full ${isLight ? 'bg-[#f3f5f9]' : 'bg-[#0a0a0f]'}`}>
            {/* FlickeringGrid 动态背景 - 覆盖整个屏幕 */}
            <div className="absolute inset-0 z-0">
                <FlickeringGrid
                    squareSize={4}
                    gridGap={6}
                    flickerChance={0.15}
                    color={isLight ? 'rgb(59, 130, 246)' : 'rgb(0, 136, 255)'}
                    maxOpacity={isLight ? 0.14 : 0.25}
                    className="w-full h-full"
                />
            </div>

            {/* 白天模式专署的发光球体修饰 */}
            {isLight && (
                <>
                    <div className="absolute top-[-5%] left-[-5%] w-[40vw] h-[40vw] rounded-full filter blur-[100px] opacity-40 animate-pulse bg-blue-300 pointer-events-none" style={{ animationDuration: '4s' }} />
                    <div className="absolute bottom-[-10%] right-[-10%] w-[40vw] h-[40vw] rounded-full filter blur-[120px] opacity-30 animate-pulse bg-indigo-300 pointer-events-none" style={{ animationDuration: '6s' }} />
                    <div className="absolute top-[20%] left-[50%] w-[30vw] h-[30vw] rounded-full filter blur-[100px] opacity-20 animate-pulse bg-sky-300 pointer-events-none" style={{ animationDuration: '8s' }} />
                </>
            )}

            {/* 渐变遮罩 */}
            <div className={`absolute inset-0 z-10 pointer-events-none ${isLight ? 'bg-gradient-to-t from-[#f3f5f9] via-white/20 to-[#f3f5f9]' : 'bg-gradient-to-t from-[#0a0a0f] via-transparent to-[#0a0a0f]'}`} />

            {/* Glassmorphism 登录卡片 */}
            <div className="relative z-20 w-full max-w-md">
                {/* 主卡片 */}
                <div className={`relative backdrop-blur-xl rounded-2xl p-8 ${isLight ? 'bg-white/90 border border-black/10 shadow-[0_20px_50px_-30px_rgba(15,23,42,0.45)]' : 'bg-white/[0.05] border border-white/[0.1]'}`}>
                    {/* Logo */}
                    <div className="text-center mb-8">
                        <div className={`w-20 h-20 mx-auto mb-4 rounded-2xl flex items-center justify-center ${isLight ? 'bg-black/[0.04] border border-black/10' : 'bg-white/10 border border-white/20'}`}>
                            <span className={`text-4xl font-bold ${isLight ? 'text-zinc-900' : 'text-white'}`}>D</span>
                        </div>
                        <h1 className={`text-2xl font-semibold mb-1 ${isLight ? 'text-zinc-900' : 'text-white'}`}>
                            欢迎回来
                        </h1>
                    </div>

                    <form onSubmit={handleSubmit} className="space-y-6">
                        {/* 用户名 */}
                        <div className="relative group">
                            <div className={`absolute inset-0 rounded-xl bg-gradient-to-r from-blue-500/20 to-purple-500/20 blur opacity-0 group-hover:opacity-100 transition duration-500 ${isLight ? 'group-hover:opacity-50' : ''}`}></div>
                            <div className="relative flex items-center">
                                <Input
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    placeholder="输入用户名"
                                    required
                                    className={`peer h-14 pl-4 pr-4 w-full rounded-xl outline-none ring-0 transition-all duration-300 focus:ring-2 focus:ring-blue-500/50 ${isLight ? 'bg-white/80 border-slate-200/60 text-slate-900 placeholder-transparent hover:border-slate-300 focus:border-blue-500/50 shadow-sm' : 'bg-zinc-900/50 border-white/10 text-white placeholder-transparent hover:bg-zinc-800/50 focus:bg-zinc-900/80 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.1)]'}`}
                                />
                                <label className={`absolute left-4 top-4 text-sm transition-all duration-300 pointer-events-none peer-placeholder-shown:translate-y-0 peer-placeholder-shown:text-base peer-focus:-translate-y-7 peer-focus:text-sm peer-focus:-translate-x-1 ${username ? '-translate-y-7 text-sm -translate-x-1' : ''} ${isLight ? 'text-slate-500 peer-focus:text-blue-600' : 'text-zinc-400 peer-focus:text-blue-400'}`}>
                                    用户名
                                </label>
                            </div>
                        </div>

                        {/* 密码 */}
                        <div className="relative group mt-8">
                            <div className={`absolute inset-0 rounded-xl bg-gradient-to-r from-blue-500/20 to-purple-500/20 blur opacity-0 group-hover:opacity-100 transition duration-500 ${isLight ? 'group-hover:opacity-50' : ''}`}></div>
                            <div className="relative flex items-center">
                                <Input
                                    type={showPassword ? 'text' : 'password'}
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    placeholder="输入密码"
                                    required
                                    className={`peer h-14 pl-4 pr-12 w-full rounded-xl outline-none ring-0 transition-all duration-300 focus:ring-2 focus:ring-blue-500/50 ${isLight ? 'bg-white/80 border-slate-200/60 text-slate-900 placeholder-transparent hover:border-slate-300 focus:border-blue-500/50 shadow-sm' : 'bg-zinc-900/50 border-white/10 text-white placeholder-transparent hover:bg-zinc-800/50 focus:bg-zinc-900/80 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.1)]'}`}
                                />
                                <label className={`absolute left-4 top-4 text-sm transition-all duration-300 pointer-events-none peer-placeholder-shown:translate-y-0 peer-placeholder-shown:text-base peer-focus:-translate-y-7 peer-focus:text-sm peer-focus:-translate-x-1 ${password ? '-translate-y-7 text-sm -translate-x-1' : ''} ${isLight ? 'text-slate-500 peer-focus:text-blue-600' : 'text-zinc-400 peer-focus:text-blue-400'}`}>
                                    密码
                                </label>
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    className={`absolute right-2 h-10 w-10 rounded-lg transition-colors duration-200 ${isLight ? 'text-slate-400 hover:text-slate-600 hover:bg-slate-100/50' : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'}`}
                                    onClick={() => setShowPassword(!showPassword)}
                                >
                                    {showPassword ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                                </Button>
                            </div>
                        </div>

                        {/* 错误提示 */}
                        {error && (
                            <div className={`flex items-center gap-2 text-sm p-4 rounded-xl border animate-in slide-in-from-top-2 fade-in-0 duration-300 ${isLight ? 'text-red-600 bg-red-50 border-red-100 shadow-sm' : 'text-red-400 bg-red-500/10 border-red-500/20'}`}>
                                <div className="w-1.5 h-1.5 rounded-full bg-red-500" />
                                {error}
                            </div>
                        )}

                        {/* 提交按钮 */}
                        <div className="pt-2">
                            <Button
                                type="submit"
                                disabled={isLoading || !username || !password}
                                className={`group relative w-full h-14 rounded-xl font-medium overflow-hidden transition-all duration-300 ${!username || !password ? 'opacity-50 cursor-not-allowed' : 'hover:scale-[1.02] active:scale-[0.98] shadow-xl'} ${isLight ? 'bg-zinc-900 text-white shadow-zinc-900/20' : 'bg-white text-zinc-900 shadow-white/10'}`}
                            >
                                <div className={`absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-300 bg-gradient-to-r ${isLight ? 'from-transparent via-white/10 to-transparent' : 'from-transparent via-black/5 to-transparent'} translate-x-[-100%] group-hover:translate-x-[100%]`} style={{ transition: 'transform 1s ease, opacity 0.3s ease' }}></div>
                                <span className="relative flex items-center justify-center gap-2 text-base">
                                    {isLoading ? (
                                        <Loader2 className="h-5 w-5 animate-spin" />
                                    ) : (
                                        <>
                                            登录
                                            <LogIn className="h-5 w-5 opacity-70 group-hover:translate-x-1 transition-transform duration-300" />
                                        </>
                                    )}
                                </span>
                            </Button>
                        </div>

                        <div className={`flex justify-center mt-6`}>
                            <Dialog>
                                <DialogTrigger asChild>
                                    <button
                                        type="button"
                                        className={`flex items-center gap-2 text-sm px-4 py-2 font-medium rounded-full transition-all duration-300 ${isLight ? 'text-slate-500 hover:text-slate-900 hover:bg-slate-100 hover:shadow-sm' : 'text-zinc-400 hover:text-white hover:bg-white/10 hover:shadow-[0_0_15px_rgba(255,255,255,0.05)]'}`}
                                    >
                                        <Mail className="h-4 w-4" />
                                        <span>通过邮箱申请注册</span>
                                    </button>
                                </DialogTrigger>
                                <DialogContent className={`sm:max-w-md ${isLight ? 'bg-white border-zinc-200' : 'bg-[#18181b] border-white/10'}`}>
                                    <DialogHeader>
                                        <DialogTitle className={isLight ? 'text-zinc-900' : 'text-white'}>系统通知</DialogTitle>
                                        <DialogDescription className={`text-base pt-4 pb-2 ${isLight ? 'text-zinc-600' : 'text-zinc-400'}`}>
                                            目前本系统为邀请制，请您联系管理员。
                                        </DialogDescription>
                                    </DialogHeader>
                                </DialogContent>
                            </Dialog>
                        </div>
                    </form>

                </div>
            </div>
        </div>
    )
}
