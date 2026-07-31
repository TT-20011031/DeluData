/**
 * 多模态输出组件
 * 
 * 在 AI 回复下方展示：
 * - 检索到的展品图片（水平滑动）
 * - 推荐的文创商品卡片（水平滑动）
 * 
 * 设计原则：
 * - 移动端优先，水平滑动不占用过多垂直空间
 * - 博物馆场景用户通常站立手持手机
 */
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { Camera, ShoppingBag, Bot, ArrowUp, Loader2 } from 'lucide-react'
import Lightbox from 'yet-another-react-lightbox'
import Zoom from 'yet-another-react-lightbox/plugins/zoom'
import 'yet-another-react-lightbox/styles.css'
import { ProductCard, type ProductInfo } from './ProductCard'
import { API_BASE_URL, resolveMuseumAssetUrl } from '../config'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog'
import { shopApi } from '../api'

export interface ImageInfo {
    fileId: string
    imageId: string
    caption?: string
}

interface MultimodalOutputProps {
    images?: ImageInfo[]
    products?: ProductInfo[]
    onProductClick?: (productId: string) => void
    className?: string
}

function ImageCard({ image, onClick }: { image: ImageInfo; onClick: () => void }) {
    const [isLoaded, setIsLoaded] = useState(false)
    const [loadError, setLoadError] = useState(false)

    const hasExtension = /\.(png|jpg|jpeg|gif|webp)$/i.test(image.imageId)
    const imageUrl = `${API_BASE_URL}/knowledge/images/${image.fileId}/${image.imageId}${hasExtension ? '' : '.png'}`

    if (loadError) return null

    return (
        <div
            className={cn(
                "cursor-zoom-in group",
                "relative overflow-hidden rounded-lg aspect-square",
                "bg-manus-tertiary border border-manus-border",
                "transition-all duration-200 hover:shadow-md hover:-translate-y-0.5"
            )}
            onClick={onClick}
        >
            {!isLoaded && (
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent animate-shimmer" />
            )}

            <img
                src={imageUrl}
                alt={image.caption || '展品图片'}
                className={cn(
                    "w-full h-full object-cover",
                    "transition-all duration-300",
                    isLoaded ? "opacity-100" : "opacity-0",
                    "group-hover:scale-105"
                )}
                onLoad={() => setIsLoaded(true)}
                onError={() => setLoadError(true)}
            />

            {image.caption && isLoaded && (
                <div className="absolute bottom-0 inset-x-0 p-1 bg-gradient-to-t from-black/60 to-transparent">
                    <p className="text-[10px] text-white/90 truncate">{image.caption}</p>
                </div>
            )}
        </div>
    )
}

export function MultimodalOutput({
    images = [],
    products = [],
    // onProductClick - 已改为使用弹窗，保留参数兼容性
    className
}: MultimodalOutputProps) {
    const [lightboxIndex, setLightboxIndex] = useState(-1)

    // 商品详情弹窗状态
    const [selectedProduct, setSelectedProduct] = useState<ProductInfo | null>(null)
    const [inquiryQuestion, setInquiryQuestion] = useState('')
    const [inquiryAnswer, setInquiryAnswer] = useState('')
    const [isInquiring, setIsInquiring] = useState(false)

    const openProductDetail = (product: ProductInfo) => {
        setSelectedProduct(product)
        setInquiryQuestion('')
        setInquiryAnswer('')
    }

    const closeProductDetail = () => {
        setSelectedProduct(null)
        setInquiryQuestion('')
        setInquiryAnswer('')
    }

    const handleInquiry = async (question?: string) => {
        const q = question || inquiryQuestion
        if (!selectedProduct || !q.trim()) return

        setIsInquiring(true)
        setInquiryAnswer('')
        if (question) setInquiryQuestion(question)

        try {
            const result = await shopApi.inquiry({
                product_id: selectedProduct.id,
                question: q,
            })
            setInquiryAnswer(result.answer)
        } catch (error) {
            console.error('咨询失败:', error)
            setInquiryAnswer('抱歉，咨询失败了，请稍后重试。')
        } finally {
            setIsInquiring(false)
        }
    }

    // 如果没有内容，不渲染
    if (images.length === 0 && products.length === 0) {
        return null
    }

    // Lightbox slides
    const slides = images.map(img => {
        const hasExt = /\.(png|jpg|jpeg|gif|webp)$/i.test(img.imageId)
        return {
            src: `${API_BASE_URL}/knowledge/images/${img.fileId}/${img.imageId}${hasExt ? '' : '.png'}`,
            alt: img.caption || '展品图片'
        }
    })

    return (
        <div className={cn("mt-4", className)}>
            {/* 统一居中布局 */}
            <div className="flex flex-col items-center gap-6 w-full">
                {/* 检索图片区域 - 居中网格 */}
                {images.length > 0 && (
                    <div className="w-full space-y-3">
                        <h4 className="text-xs font-medium text-manus-muted flex items-center justify-center gap-1.5">
                            <Camera className="h-3.5 w-3.5" /> 相关展品
                        </h4>
                        <div className="flex flex-wrap justify-center gap-3 animate-in fade-in zoom-in duration-300">
                            {images.slice(0, 12).map((img, idx) => (
                                <div key={`${img.fileId}-${img.imageId}`} className="w-24 sm:w-28 group relative">
                                    <ImageCard
                                        image={img}
                                        onClick={() => setLightboxIndex(idx)}
                                    />
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* 文创推荐区域 - 居中网格 */}
                {products.length > 0 && (
                    <div className="w-full space-y-3">
                        <h4 className="text-xs font-medium text-manus-muted flex items-center justify-center gap-1.5">
                            <ShoppingBag className="h-3.5 w-3.5" /> 文创推荐
                        </h4>
                        <div className="flex flex-wrap justify-center gap-3 animate-in fade-in zoom-in duration-300 delay-100">
                            {products.slice(0, 5).map(product => (
                                <div key={product.id} className="w-32 sm:w-36">
                                    <ProductCard
                                        product={product}
                                        onClick={() => openProductDetail(product)}
                                        variant="grid"
                                        className="h-full"
                                    />
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>

            {/* 图片 Lightbox */}
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

            {/* 商品详情弹窗 */}
            <Dialog open={!!selectedProduct} onOpenChange={(open) => !open && closeProductDetail()}>
                <DialogContent className="bg-manus-secondary border-manus-border text-manus-text max-w-md max-h-[80vh] overflow-hidden flex flex-col">
                    <DialogHeader className="flex-shrink-0">
                        <DialogTitle className="text-base font-medium text-manus-text pr-8">
                            {selectedProduct?.name}
                        </DialogTitle>
                        <DialogDescription className="sr-only">
                            查看文创商品详情并向 AI 导购发起咨询。
                        </DialogDescription>
                    </DialogHeader>

                    {selectedProduct && (
                        <ScrollArea className="flex-1 -mx-6 px-6">
                            <div className="space-y-4 pb-4">
                                {/* 商品图片 */}
                                <div className="aspect-video bg-manus-tertiary rounded-lg overflow-hidden">
                                    {resolveMuseumAssetUrl(selectedProduct.image_url) ? (
                                        <img
                                            src={resolveMuseumAssetUrl(selectedProduct.image_url) ?? undefined}
                                            alt={selectedProduct.name}
                                            className="w-full h-full object-cover"
                                        />
                                    ) : (
                                        <div className="flex items-center justify-center h-full">
                                            <ShoppingBag className="h-12 w-12 text-manus-muted" />
                                        </div>
                                    )}
                                </div>

                                {/* 商品信息 */}
                                <div className="space-y-2">
                                    <div className="flex items-center justify-between">
                                        <span className="text-xl font-bold text-accent">
                                            ¥{selectedProduct.price.toFixed(0)}
                                        </span>
                                    </div>
                                    {selectedProduct.relation && (
                                        <p className="text-sm text-manus-muted">
                                            {selectedProduct.relation}
                                        </p>
                                    )}
                                </div>

                                {/* AI 导购咨询 */}
                                <div className="border-t border-manus-border pt-4">
                                    <div className="flex items-center gap-2 mb-3">
                                        <div className="w-5 h-5 rounded-md bg-accent/20 flex items-center justify-center">
                                            <Bot className="h-3 w-3 text-accent" />
                                        </div>
                                        <h3 className="text-xs font-medium text-manus-text">AI 导购咨询</h3>
                                    </div>

                                    {/* 咨询输入 */}
                                    <div className={cn(
                                        "relative overflow-hidden bg-manus-tertiary rounded-lg border transition-all",
                                        "border-manus-border focus-within:border-accent/50"
                                    )}>
                                        <Input
                                            value={inquiryQuestion}
                                            onChange={(e) => setInquiryQuestion(e.target.value)}
                                            onKeyDown={(e) => e.key === 'Enter' && handleInquiry()}
                                            placeholder="请问这件商品..."
                                            className="border-0 bg-transparent text-sm text-manus-text placeholder:text-manus-subtle focus-visible:ring-0 pr-10"
                                            disabled={isInquiring}
                                        />
                                        <Button
                                            onClick={() => handleInquiry()}
                                            disabled={!inquiryQuestion.trim() || isInquiring}
                                            size="icon"
                                            className={cn(
                                                "absolute right-1 top-1/2 -translate-y-1/2 h-6 w-6 rounded-md transition-all",
                                                inquiryQuestion.trim() && !isInquiring
                                                    ? "bg-accent hover:bg-accent/90 text-white"
                                                    : "bg-manus-hover text-manus-subtle"
                                            )}
                                        >
                                            {isInquiring ? (
                                                <Loader2 className="h-3 w-3 animate-spin" />
                                            ) : (
                                                <ArrowUp className="h-3 w-3" />
                                            )}
                                        </Button>
                                    </div>

                                    {/* 快捷问题 */}
                                    <div className="flex flex-wrap gap-1.5 mt-2">
                                        {[
                                            '有什么特点？',
                                            '适合送给谁？',
                                            '和展品有什么关联？',
                                        ].map((q) => (
                                            <button
                                                key={q}
                                                onClick={() => handleInquiry(q)}
                                                disabled={isInquiring}
                                                className="px-2 py-1 text-xs bg-manus-hover hover:bg-manus-tertiary text-manus-muted hover:text-manus-text rounded-md transition-colors disabled:opacity-50"
                                            >
                                                {q}
                                            </button>
                                        ))}
                                    </div>

                                    {/* 咨询答复 */}
                                    {inquiryAnswer && (
                                        <div className="mt-3 p-3 bg-manus-hover rounded-lg">
                                            <div className="flex items-start gap-2">
                                                <div className="w-5 h-5 rounded-md bg-accent/20 flex items-center justify-center shrink-0 mt-0.5">
                                                    <Bot className="h-3 w-3 text-accent" />
                                                </div>
                                                <p className="text-xs text-manus-text leading-relaxed whitespace-pre-wrap">
                                                    {inquiryAnswer}
                                                </p>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            </div>
                        </ScrollArea>
                    )}
                </DialogContent>
            </Dialog>
        </div >
    )
}

export default MultimodalOutput
