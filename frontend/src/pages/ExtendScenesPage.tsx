/**
 * 扩展场景选择页面
 * 
 * 展示可用的扩展场景卡片，点击进入对应场景
 * 进入场景后，侧边栏菜单隐藏，只显示场景内容
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Landmark, ShoppingBag, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'

interface SceneCard {
    id: string
    title: string
    description: string
    icon: React.ComponentType<{ className?: string }>
    path: string
    color: string
    available: boolean
}

const scenes: SceneCard[] = [
    {
        id: 'museum-guide',
        title: '博物馆智能导览',
        description: '语音交互式导览，智能客群洞察，个性化讲解服务',
        icon: Landmark,
        path: '/museum/guide',
        color: 'from-amber-500/20 to-orange-500/20 border-amber-500/30',
        available: true,
    },
    {
        id: 'museum-shop',
        title: '博物馆文创商城',
        description: '智能商品推荐，个性化购物体验',
        icon: ShoppingBag,
        path: '/museum/shop',
        color: 'from-emerald-500/20 to-teal-500/20 border-emerald-500/30',
        available: true,
    },
]

export default function ExtendScenesPage() {
    const navigate = useNavigate()
    const [hoveredScene, setHoveredScene] = useState<string | null>(null)

    const handleSceneSelect = (scene: SceneCard) => {
        if (!scene.available) return
        // 进入场景页面（全屏模式，隐藏侧边栏）
        navigate(scene.path)
    }

    return (
        <div className="flex-1 flex flex-col h-full bg-manus">
            {/* 顶部标题 */}
            <header className="h-14 flex items-center justify-between px-6 border-b border-manus-border shrink-0">
                <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-accent/20 flex items-center justify-center">
                        <Sparkles className="h-4 w-4 text-accent" />
                    </div>
                    <div>
                        <h1 className="text-sm font-medium text-manus-text">扩展场景</h1>
                        <p className="text-xs text-manus-muted">选择一个场景开始体验</p>
                    </div>
                </div>
            </header>

            {/* 场景卡片网格 */}
            <div className="flex-1 flex items-center justify-center p-8">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-3xl w-full">
                    {scenes.map((scene) => (
                        <button
                            key={scene.id}
                            onClick={() => handleSceneSelect(scene)}
                            onMouseEnter={() => setHoveredScene(scene.id)}
                            onMouseLeave={() => setHoveredScene(null)}
                            disabled={!scene.available}
                            className={cn(
                                "relative p-6 rounded-2xl border-2 text-left transition-all duration-300",
                                "bg-gradient-to-br",
                                scene.color,
                                scene.available 
                                    ? "hover:scale-[1.02] hover:shadow-xl cursor-pointer"
                                    : "opacity-50 cursor-not-allowed",
                                hoveredScene === scene.id && "ring-2 ring-accent ring-offset-2 ring-offset-manus"
                            )}
                        >
                            {/* 图标 */}
                            <div className={cn(
                                "w-12 h-12 rounded-xl flex items-center justify-center mb-4",
                                "bg-white/10 backdrop-blur-sm"
                            )}>
                                <scene.icon className="h-6 w-6 text-manus-text" />
                            </div>

                            {/* 标题 */}
                            <h3 className="text-lg font-medium text-manus-text mb-2">
                                {scene.title}
                            </h3>

                            {/* 描述 */}
                            <p className="text-sm text-manus-muted leading-relaxed">
                                {scene.description}
                            </p>

                            {/* 状态标签 */}
                            {!scene.available && (
                                <span className="absolute top-4 right-4 text-xs px-2 py-1 bg-manus-tertiary text-manus-muted rounded">
                                    即将推出
                                </span>
                            )}
                        </button>
                    ))}
                </div>
            </div>

            {/* 底部提示 */}
            <div className="p-4 text-center">
                <p className="text-xs text-manus-subtle">
                    更多场景持续开发中...
                </p>
            </div>
        </div>
    )
}
