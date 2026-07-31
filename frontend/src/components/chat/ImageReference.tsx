/**
 * 图片引用组件
 * 
 * 用于渲染 AI 消息中的图片引用 (支持双格式):
 *   - 旧格式: [[IMAGE:file_id:image_id:caption]]
 *   - 新格式: [[IMG:file_id:image_id:caption]] (v2.4+)
 * 
 * 支持:
 *   - 固定 4:3 宽高比，布局整齐
 *   - 集成 yet-another-react-lightbox，支持 Zoom/Pan/手势
 *   - Shimmer 加载动画
 */
import { useState } from 'react'
import Lightbox from 'yet-another-react-lightbox'
import Zoom from 'yet-another-react-lightbox/plugins/zoom'
import 'yet-another-react-lightbox/styles.css'
import { cn } from '@/lib/utils'

interface ImageReferenceProps {
    fileId: string
    imageId: string
    caption: string
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

export function ImageReference({ fileId, imageId, caption }: ImageReferenceProps) {
    const [showLightbox, setShowLightbox] = useState(false)
    const [isLoaded, setIsLoaded] = useState(false)
    const [loadError, setLoadError] = useState(false)

    // [v2.4] 统一使用带扩展名的 URL，符合 Web 静态资源惯例
    const imageUrl = `${API_BASE_URL}/knowledge/images/${fileId}/${imageId}.png`

    if (loadError) return null

    return (
        <>
            {/* 固定 4:3 宽高比容器 */}
            <div className="my-4 block group select-none">
                <div
                    className={cn(
                        "relative overflow-hidden rounded-xl cursor-zoom-in",
                        "aspect-[4/3] w-full",  // 占满消息区域宽度
                        // Glassmorphism 风格
                        "bg-neutral-100/50 dark:bg-white/5",
                        "border border-black/5 dark:border-white/10",
                        "shadow-sm hover:shadow-lg",
                        // 交互动画
                        "transition-all duration-300 ease-out",
                        "hover:-translate-y-0.5 hover:border-black/10 dark:hover:border-white/20"
                    )}
                    onClick={() => setShowLightbox(true)}
                >
                    {/* Shimmer 加载动画 */}
                    {!isLoaded && (
                        <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent dark:via-white/10 animate-shimmer" />
                    )}

                    {/* 图片层 - 使用 object-cover 裁剪填充 */}
                    <img
                        src={imageUrl}
                        alt={caption}
                        className={cn(
                            "absolute inset-0 w-full h-full object-cover",
                            "transition-all duration-500 ease-out",
                            isLoaded ? "opacity-100 scale-100" : "opacity-0 scale-105"
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

                    {/* 底部微光边框 (装饰) */}
                    <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-white/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                </div>
            </div>

            {/* yet-another-react-lightbox - Portal 渲染，不受父容器 overflow 限制 */}
            <Lightbox
                open={showLightbox}
                close={() => setShowLightbox(false)}
                slides={[{ src: imageUrl, alt: caption }]}
                plugins={[Zoom]}
                zoom={{
                    maxZoomPixelRatio: 5,
                    scrollToZoom: true
                }}
                carousel={{ finite: true }}
                controller={{ closeOnBackdropClick: true }}
                styles={{
                    container: { backgroundColor: 'rgba(0, 0, 0, 0.9)' }
                }}
            />
        </>
    )
}

/**
 * 解析并渲染包含图片引用的文本
 */
export function parseImageReferences(content: string): React.ReactNode[] {
    // [v2.4] 同时支持两种格式:
    // - 旧格式: [[IMAGE:fileId:imageId]] 或 [[IMAGE:fileId:imageId:caption]]
    // - 新格式: [[IMG:fileId:imageId]] 或 [[IMG:fileId:imageId:caption]]
    // 使用非贪婪匹配，避免误触普通 Markdown 方括号
    const imagePattern = /\[\[(IMAGE|IMG):([^:\]]+):([^:\]]+)(?::([^\]]*))?\]\]/g
    const parts: React.ReactNode[] = []
    let lastIndex = 0
    let match

    while ((match = imagePattern.exec(content)) !== null) {
        // 添加图片前的文本
        if (match.index > lastIndex) {
            parts.push(content.slice(lastIndex, match.index))
        }

        // [v2.4] 提取时注意捕获组索引变化：[全匹配, 标记, fileId, imageId, caption]
        const [, _tag, fileId, imageId, caption = ''] = match
        parts.push(
            <ImageReference
                key={`img-${fileId}-${imageId}`}
                fileId={fileId}
                imageId={imageId}
                caption={caption}
            />
        )

        lastIndex = match.index + match[0].length
    }

    // 添加剩余文本
    if (lastIndex < content.length) {
        parts.push(content.slice(lastIndex))
    }

    return parts.length > 0 ? parts : [content]
}
