/**
 * 图片轮播组件
 *
 * 用于在消息末尾集中展示所有图片引用
 * 类似"卡片轮播"效果：
 * - 统一容器大小
 * - 图片自适应显示（不裁切）
 * - 水平滑动切换
 * - 支持 Lightbox 放大
 */
import { useState, useRef } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import Lightbox from 'yet-another-react-lightbox'
import Zoom from 'yet-another-react-lightbox/plugins/zoom'
import 'yet-another-react-lightbox/styles.css'
import { cn } from '@/lib/utils'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

export interface ImageInfo {
    fileId: string
    imageId: string
    caption: string
}

interface ImageCarouselProps {
    images: ImageInfo[]
}

/**
 * 单个图片卡片
 */
function ImageCard({ image, onClick }: { image: ImageInfo; onClick: () => void }) {
    const [isLoaded, setIsLoaded] = useState(false)
    const [loadError, setLoadError] = useState(false)

    const imageUrl = `${API_BASE_URL}/knowledge/images/${image.fileId}/${image.imageId}.png`

    if (loadError) return null

    return (
        <div
            className={cn(
                "flex-shrink-0 w-[280px] h-[210px] cursor-zoom-in group",
                "relative overflow-hidden rounded-xl",
                // Glassmorphism 风格
                "bg-neutral-100/50 dark:bg-white/5",
                "border border-black/5 dark:border-white/10",
                "shadow-sm hover:shadow-lg",
                // 交互动画
                "transition-all duration-300 ease-out",
                "hover:-translate-y-0.5 hover:border-black/10 dark:hover:border-white/20"
            )}
            onClick={onClick}
        >
            {/* Shimmer 加载动画 */}
            {!isLoaded && (
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent dark:via-white/10 animate-shimmer" />
            )}

            {/* 图片 - object-contain 保持原始比例，居中显示 */}
            <img
                src={imageUrl}
                alt={image.caption}
                className={cn(
                    "absolute inset-0 w-full h-full object-contain p-2",
                    "transition-all duration-500 ease-out",
                    isLoaded ? "opacity-100" : "opacity-0"
                )}
                onLoad={() => setIsLoaded(true)}
                onError={() => setLoadError(true)}
            />

            {/* 悬停时的放大图标提示 */}
            <div className={cn(
                "absolute inset-0 flex items-center justify-center",
                "bg-black/0 group-hover:bg-black/10 transition-colors duration-200"
            )}>
                <div className={cn(
                    "w-10 h-10 rounded-full bg-white/80 dark:bg-black/60 flex items-center justify-center",
                    "opacity-0 group-hover:opacity-100 scale-75 group-hover:scale-100",
                    "transition-all duration-200 backdrop-blur-sm shadow-lg"
                )}>
                    <svg className="w-5 h-5 text-neutral-700 dark:text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" />
                    </svg>
                </div>
            </div>

            {/* 底部 Caption */}
            {image.caption && (
                <div className="absolute bottom-0 inset-x-0 p-2 bg-gradient-to-t from-black/50 to-transparent">
                    <p className="text-xs text-white/90 truncate">{image.caption}</p>
                </div>
            )}
        </div>
    )
}

export function ImageCarousel({ images }: ImageCarouselProps) {
    const [lightboxIndex, setLightboxIndex] = useState(-1)
    const scrollRef = useRef<HTMLDivElement>(null)

    if (!images.length) return null

    // 滚动控制
    const scroll = (direction: 'left' | 'right') => {
        if (!scrollRef.current) return
        const scrollAmount = 300
        scrollRef.current.scrollBy({
            left: direction === 'left' ? -scrollAmount : scrollAmount,
            behavior: 'smooth'
        })
    }

    // Lightbox slides
    const slides = images.map(img => ({
        src: `${API_BASE_URL}/knowledge/images/${img.fileId}/${img.imageId}.png`,
        alt: img.caption
    }))

    return (
        <div className="relative mt-4 group/carousel px-12">
            {/* 左箭头 */}
            {images.length > 2 && (
                <button
                    onClick={() => scroll('left')}
                    className={cn(
                        "absolute left-0 top-1/2 -translate-y-1/2 z-10",
                        "w-10 h-10 rounded-full bg-white/90 dark:bg-neutral-800/90",
                        "flex items-center justify-center shadow-lg",
                        "opacity-0 group-hover/carousel:opacity-100 transition-opacity",
                        "hover:bg-white dark:hover:bg-neutral-700"
                    )}
                >
                    <ChevronLeft className="w-5 h-5 text-neutral-700 dark:text-white" />
                </button>
            )}

            {/* 图片滚动容器 */}
            <div
                ref={scrollRef}
                className={cn(
                    "flex gap-3 overflow-x-auto pb-2 scrollbar-hide",
                    "scroll-smooth snap-x snap-mandatory"
                )}
            >
                {images.map((image, idx) => (
                    <div key={`${image.fileId}-${image.imageId}`} className="snap-start">
                        <ImageCard
                            image={image}
                            onClick={() => setLightboxIndex(idx)}
                        />
                    </div>
                ))}
            </div>

            {/* 右箭头 */}
            {images.length > 2 && (
                <button
                    onClick={() => scroll('right')}
                    className={cn(
                        "absolute right-0 top-1/2 -translate-y-1/2 z-10",
                        "w-10 h-10 rounded-full bg-white/90 dark:bg-neutral-800/90",
                        "flex items-center justify-center shadow-lg",
                        "opacity-0 group-hover/carousel:opacity-100 transition-opacity",
                        "hover:bg-white dark:hover:bg-neutral-700"
                    )}
                >
                    <ChevronRight className="w-5 h-5 text-neutral-700 dark:text-white" />
                </button>
            )}

            {/* Lightbox */}
            <Lightbox
                open={lightboxIndex >= 0}
                close={() => setLightboxIndex(-1)}
                index={lightboxIndex}
                slides={slides}
                plugins={[Zoom]}
                zoom={{
                    maxZoomPixelRatio: 5,
                    scrollToZoom: true
                }}
                controller={{ closeOnBackdropClick: true }}
                styles={{
                    container: { backgroundColor: 'rgba(0, 0, 0, 0.9)' }
                }}
            />
        </div>
    )
}
