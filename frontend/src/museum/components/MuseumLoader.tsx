/**
 * 博物馆主题加载动画 (v2.0)
 * 
 * 高级视觉效果，融合博物馆文化元素
 */
import { cn } from '@/lib/utils'

interface MuseumLoaderProps {
    className?: string
    text?: string
}

export function MuseumLoader({ className, text = '正在思考...' }: MuseumLoaderProps) {
    return (
        <div className={cn(
            "flex flex-col items-center justify-center gap-6 py-12",
            className
        )}>
            {/* 主动画容器 - 古铜鼎造型 (新版 SVG) */}
            <div className="relative w-32 h-32">
                {/* 外圈光晕 */}
                <div className="absolute inset-0 rounded-full bg-gradient-to-r from-amber-500/10 via-amber-300/5 to-amber-500/10 animate-spin-slow" />

                {/* SVG 图标 */}
                <svg
                    className="w-full h-full text-amber-500/80 drop-shadow-[0_0_15px_rgba(245,158,11,0.3)]"
                    viewBox="0 0 1024 1024"
                    fill="currentColor"
                >
                    <path d="M427.12763 106.090463c0 76.385133 21.218093 106.090463 84.87237 106.090463s84.87237-29.70533 84.87237-106.090463S575.654278 0 512 0s-84.87237 29.70533-84.87237 106.090463z" opacity="0.9">
                        <animate attributeName="opacity" values="0.9;0.4;0.9" dur="3s" repeatCount="indefinite" />
                    </path>
                    <path d="M53.689201 220.668163c42.436185 182.475596 25.461711 585.619355-29.70533 721.415147-50.923422 127.308555 140.039411 97.603226 199.45007-29.70533 59.410659-127.308555 203.693689-144.283029 203.693689-21.218092 0 59.410659 29.70533 84.87237 101.846844 84.87237 80.628752 0 97.603226-16.974474 80.628752-84.87237-16.974474-63.654278 0-84.87237 67.897896-84.87237 46.679804 0 89.115989 25.461711 89.115989 59.410659 0 80.628752 114.5777 165.501122 182.475596 140.039411 46.679804-16.974474 46.679804-72.141515 12.730855-280.078822-29.70533-178.231978-29.70533-326.758626 0-471.041655 42.436185-203.693689 42.436185-212.180926-55.16704-212.180926-67.897896 0-101.846844 29.70533-123.064937 106.090463C758.129874 246.129874 736.911781 254.617111 512 254.617111c-224.911781 0-246.129874-8.487237-271.591585-106.090463C219.190323 72.141515 185.241374 42.436185 113.09986 42.436185 15.496634 42.436185 15.496634 50.923422 53.689201 220.668163z m704.440673 297.053296c-12.730856 84.87237-42.436185 97.603226-233.399018 110.334081-216.424544 12.730856-224.911781 8.487237-224.911782-93.359607 0-106.090463 12.730856-110.334081 237.642637-110.334082 220.668163 0 233.399018 4.243619 220.668163 93.359608z">
                        <animateTransform attributeName="transform" type="translate" values="0 0; 0 -5; 0 0" dur="4s" repeatCount="indefinite" />
                    </path>
                </svg>
            </div>

            {/* 文字提示 - 古典风格 */}
            <div className="flex flex-col items-center gap-2">
                <p className="text-base text-amber-200/80 tracking-widest font-light">{text}</p>
                <div className="flex items-center gap-1.5">
                    {[0, 1, 2, 3, 4].map((i) => (
                        <span
                            key={i}
                            className="w-1.5 h-1.5 rounded-full bg-amber-400/80"
                            style={{
                                animation: `waveDot 1.4s ease-in-out infinite`,
                                animationDelay: `${i * 0.1}s`,
                            }}
                        />
                    ))}
                </div>
            </div>

            {/* CSS 动画 */}
            <style>{`
                @keyframes spin-slow {
                    from { transform: rotate(0deg); }
                    to { transform: rotate(360deg); }
                }
                .animate-spin-slow {
                    animation: spin-slow 8s linear infinite;
                }
                @keyframes orbitPulse {
                    0%, 100% {
                        opacity: 0.4;
                        transform: rotate(var(--rotate, 0deg)) translateY(-20px) translateX(-50%) scale(0.8);
                    }
                    50% {
                        opacity: 1;
                        transform: rotate(var(--rotate, 0deg)) translateY(-20px) translateX(-50%) scale(1.2);
                    }
                }
                @keyframes waveDot {
                    0%, 60%, 100% {
                        transform: translateY(0);
                        opacity: 0.4;
                    }
                    30% {
                        transform: translateY(-6px);
                        opacity: 1;
                    }
                }
            `}</style>
        </div>
    )
}

/**
 * 简洁版加载动画 - 卷轴展开效果
 */
export function MuseumScrollLoader({ className, text = '展开古卷...' }: MuseumLoaderProps) {
    return (
        <div className={cn(
            "flex flex-col items-center justify-center gap-3 py-6",
            className
        )}>
            {/* 卷轴图标 */}
            <div className="relative w-16 h-10">
                {/* 左轴 */}
                <div className="absolute left-0 top-0 bottom-0 w-2 bg-gradient-to-r from-amber-600 to-amber-500 rounded-full shadow-md" />
                {/* 右轴 */}
                <div className="absolute right-0 top-0 bottom-0 w-2 bg-gradient-to-l from-amber-600 to-amber-500 rounded-full shadow-md" />
                {/* 卷轴纸张 - 展开动画 */}
                <div
                    className="absolute left-2 right-2 top-1 bottom-1 bg-gradient-to-b from-amber-50 to-amber-100 rounded-sm overflow-hidden"
                    style={{
                        animation: 'scrollUnroll 2s ease-in-out infinite',
                    }}
                >
                    {/* 文字线条效果 */}
                    <div className="absolute inset-1 flex flex-col gap-1 justify-center">
                        {[0, 1, 2].map((i) => (
                            <div
                                key={i}
                                className="h-0.5 bg-amber-300/50 rounded-full"
                                style={{
                                    width: `${60 + i * 15}%`,
                                    animation: 'textLine 1.5s ease-in-out infinite',
                                    animationDelay: `${i * 0.3}s`,
                                }}
                            />
                        ))}
                    </div>
                </div>
            </div>

            {/* 文字提示 */}
            <p className="text-sm text-manus-muted animate-pulse">{text}</p>

            <style>{`
                @keyframes scrollUnroll {
                    0%, 100% {
                        transform: scaleX(0.8);
                        opacity: 0.6;
                    }
                    50% {
                        transform: scaleX(1);
                        opacity: 1;
                    }
                }
                @keyframes textLine {
                    0%, 100% {
                        opacity: 0.3;
                    }
                    50% {
                        opacity: 0.8;
                    }
                }
            `}</style>
        </div>
    )
}

/**
 * 内联加载点 - 用于流式输出时
 */
export function MuseumDots({ className }: { className?: string }) {
    return (
        <span className={cn("inline-flex items-center gap-0.5 ml-1", className)}>
            {[0, 1, 2].map((i) => (
                <span
                    key={i}
                    className="w-1.5 h-1.5 bg-amber-400 rounded-full"
                    style={{
                        animation: 'dotPulse 1.4s ease-in-out infinite',
                        animationDelay: `${i * 0.2}s`,
                    }}
                />
            ))}
            <style>{`
                @keyframes dotPulse {
                    0%, 80%, 100% {
                        transform: scale(0.6);
                        opacity: 0.4;
                    }
                    40% {
                        transform: scale(1);
                        opacity: 1;
                    }
                }
            `}</style>
        </span>
    )
}

export default MuseumLoader
