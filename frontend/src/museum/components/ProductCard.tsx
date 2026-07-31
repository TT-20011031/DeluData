/**
 * 文创商品卡片组件
 * 
 * 用于在多模态输出中展示推荐的文创商品
 * 水平滑动设计，适合移动端手持操作
 */
import { cn } from '@/lib/utils'
import { resolveMuseumAssetUrl } from '../config'

export interface ProductInfo {
    id: string
    name: string
    price: number
    image_url?: string | null
    relation?: string
}

interface ProductCardProps {
    product: ProductInfo
    onClick?: () => void
    className?: string
    variant?: 'list' | 'grid'  // list: 横向列表, grid: 网格卡片
}

export function ProductCard({ product, onClick, className, variant = 'list' }: ProductCardProps) {
    const imageUrl = resolveMuseumAssetUrl(product.image_url)

    // 网格模式：竖向卡片
    if (variant === 'grid') {
        return (
            <div
                className={cn(
                    "cursor-pointer group",
                    "flex flex-col p-2",
                    "rounded-lg border border-manus-border",
                    "bg-manus-secondary hover:bg-manus-tertiary",
                    "transition-all duration-200 hover:shadow-md",
                    className
                )}
                onClick={onClick}
            >
                {/* 上方：商品图片 */}
                <div className="relative w-full aspect-square rounded-md bg-manus-tertiary overflow-hidden mb-2">
                    {imageUrl ? (
                        <img
                            src={imageUrl}
                            alt={product.name}
                            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                            onError={(e) => {
                                const target = e.target as HTMLImageElement
                                target.style.display = 'none'
                                target.parentElement!.innerHTML = '<span class="text-2xl flex items-center justify-center w-full h-full">🎁</span>'
                            }}
                        />
                    ) : (
                        <div className="w-full h-full flex items-center justify-center">
                            <span className="text-2xl">🎁</span>
                        </div>
                    )}
                </div>
                {/* 下方：商品信息 */}
                <p className="text-xs text-manus-text line-clamp-2 leading-tight" title={product.name}>
                    {product.name}
                </p>
                <p className="text-sm font-medium text-accent mt-1">
                    ¥{product.price.toFixed(0)}
                </p>
            </div>
        )
    }

    // 列表模式：横向卡片（默认）
    return (
        <div
            className={cn(
                "w-48 shrink-0 cursor-pointer group",
                "flex gap-2 p-2",
                "rounded-lg border border-manus-border",
                "bg-manus-secondary hover:bg-manus-tertiary",
                "transition-all duration-200 hover:shadow-md",
                className
            )}
            onClick={onClick}
        >
            {/* 左侧：商品图片 */}
            <div className="relative w-16 h-16 shrink-0 rounded-md bg-manus-tertiary overflow-hidden">
                {imageUrl ? (
                    <img
                        src={imageUrl}
                        alt={product.name}
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                        onError={(e) => {
                            const target = e.target as HTMLImageElement
                            target.style.display = 'none'
                            target.parentElement!.innerHTML = '<span class="text-2xl flex items-center justify-center w-full h-full">🎁</span>'
                        }}
                    />
                ) : (
                    <div className="w-full h-full flex items-center justify-center">
                        <span className="text-2xl">🎁</span>
                    </div>
                )}
            </div>

            {/* 右侧：商品信息 */}
            <div className="flex-1 min-w-0 flex flex-col justify-center">
                <p className="text-xs text-manus-text line-clamp-2 leading-tight" title={product.name}>
                    {product.name}
                </p>
                <p className="text-sm font-medium text-accent mt-1">
                    ¥{product.price.toFixed(0)}
                </p>
            </div>
        </div>
    )
}

export default ProductCard
