/**
 * 博物馆商城页面
 * 
 * 使用 Manus 设计系统，与主程序 UI 风格一致
 */
import { useEffect, useState } from 'react'
import { ShoppingBag, Search, ArrowUp, Loader2, Package, Bot, Download, Upload, FileSpreadsheet, Settings, Pencil, Save, X } from 'lucide-react'
import { useAuthStore } from '@/stores/authStore'
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
import { cn } from '@/lib/utils'
import { shopApi } from '../api'
import { getMuseumPrimaryImageUrl, normalizeMuseumImageUrls } from '../config'
import type { Product, ProductCategory } from '../types'

const CATEGORIES: { value: ProductCategory | null; label: string }[] = [
    { value: null, label: '全部' },
    { value: '文创', label: '文创' },
    { value: '纪念品', label: '纪念品' },
    { value: '仿制品', label: '仿制品' },
    { value: '书籍', label: '书籍' },
    { value: '其他', label: '其他' },
]

export default function ShopPage() {
    const [products, setProducts] = useState<Product[]>([])
    const [loading, setLoading] = useState(true)
    const [selectedCategory, setSelectedCategory] = useState<ProductCategory | null>(null)
    const [searchQuery, setSearchQuery] = useState('')
    
    // 商品详情弹窗
    const [selectedProduct, setSelectedProduct] = useState<Product | null>(null)
    const [inquiryQuestion, setInquiryQuestion] = useState('')
    const [inquiryAnswer, setInquiryAnswer] = useState('')
    const [isInquiring, setIsInquiring] = useState(false)
    
    // 导入导出状态
    const [isImporting, setIsImporting] = useState(false)
    const [importResult, setImportResult] = useState<{ success: number; failed: number; errors: string[] } | null>(null)
    
    // BUG4: 管理模式状态
    const [isManageMode, setIsManageMode] = useState(false)
    const [editingProduct, setEditingProduct] = useState<Product | null>(null)
    const [editForm, setEditForm] = useState({ name: '', price: '', stock: '', description: '' })
    const [isSaving, setIsSaving] = useState(false)
    
    // 获取用户信息检查权限
    const { user } = useAuthStore()
    const isAdmin = Boolean(user?.permissions?.some((code) => code === '*' || code === 'museum:manage' || code === 'museum:product_manage'))

    useEffect(() => {
        loadProducts()
    }, [selectedCategory])

    const normalizeProduct = (product: Product): Product => ({
        ...product,
        image_urls: normalizeMuseumImageUrls((product as Product & { image_urls?: unknown }).image_urls),
    })

    const loadProducts = async () => {
        setLoading(true)
        try {
            const response = await shopApi.listProducts({
                category: selectedCategory || undefined,
                page: 1,
                page_size: 20,
            })
            setProducts(response.items.map(normalizeProduct))
        } catch (error) {
            console.error('加载商品失败:', error)
        } finally {
            setLoading(false)
        }
    }

    const handleInquiry = async () => {
        if (!selectedProduct || !inquiryQuestion.trim()) return
        
        setIsInquiring(true)
        setInquiryAnswer('')
        
        try {
            const result = await shopApi.inquiry({
                product_id: selectedProduct.id,
                question: inquiryQuestion,
            })
            setInquiryAnswer(result.answer)
        } catch (error) {
            console.error('咨询失败:', error)
            setInquiryAnswer('抱歉，咨询失败了，请稍后重试。')
        } finally {
            setIsInquiring(false)
        }
    }

    const openProductDetail = (product: Product) => {
        setSelectedProduct(product)
        setInquiryQuestion('')
        setInquiryAnswer('')
    }

    const closeProductDetail = () => {
        setSelectedProduct(null)
        setInquiryQuestion('')
        setInquiryAnswer('')
    }

    const handleDownloadTemplate = async () => {
        try {
            await shopApi.downloadTemplate()
        } catch (error) {
            console.error('下载模板失败:', error)
        }
    }

    const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]
        if (!file) return
        
        setIsImporting(true)
        setImportResult(null)
        
        try {
            const result = await shopApi.importProducts(file)
            setImportResult(result.result)
            loadProducts() // 刷新商品列表
        } catch (error) {
            console.error('导入失败:', error)
            setImportResult({ success: 0, failed: 1, errors: [(error as Error).message] })
        } finally {
            setIsImporting(false)
            e.target.value = '' // 重置 input
        }
    }
    
    // BUG4: 编辑商品处理函数
    const openEditDialog = (product: Product, e: React.MouseEvent) => {
        e.stopPropagation() // 阻止触发商品详情弹窗
        setEditingProduct(product)
        setEditForm({
            name: product.name,
            price: product.price.toString(),
            stock: product.stock.toString(),
            description: product.description || ''
        })
    }
    
    const closeEditDialog = () => {
        setEditingProduct(null)
        setEditForm({ name: '', price: '', stock: '', description: '' })
    }
    
    const handleSaveProduct = async () => {
        if (!editingProduct) return
        
        setIsSaving(true)
        try {
            await shopApi.updateProduct(editingProduct.id, {
                name: editForm.name,
                price: parseFloat(editForm.price),
                stock: parseInt(editForm.stock),
                description: editForm.description
            })
            closeEditDialog()
            loadProducts() // 刷新列表
        } catch (error) {
            console.error('保存失败:', error)
        } finally {
            setIsSaving(false)
        }
    }

    return (
        <div className="flex-1 flex flex-col h-full bg-manus">
            {/* 顶部栏 */}
            <header className="h-14 flex items-center justify-between px-6 border-b border-manus-border">
                <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-accent/20 flex items-center justify-center">
                        <ShoppingBag className="h-4 w-4 text-accent" />
                    </div>
                    <h1 className="text-sm font-medium text-manus-text">博物馆文创商城</h1>
                </div>
                
                {/* 导入导出按钮 + 管理模式切换 */}
                <div className="flex items-center gap-2">
                    {/* BUG4: 管理模式切换按钮 (仅管理员可见) */}
                    {isAdmin && (
                        <Button
                            variant={isManageMode ? "default" : "outline"}
                            size="sm"
                            onClick={() => setIsManageMode(!isManageMode)}
                            className={cn(
                                "h-8 px-3 text-xs",
                                isManageMode 
                                    ? "bg-amber-500 hover:bg-amber-600 text-white" 
                                    : "border-manus-border text-manus-muted hover:text-manus-text hover:bg-manus-hover"
                            )}
                        >
                            <Settings className="h-3.5 w-3.5 mr-1.5" />
                            {isManageMode ? '退出管理' : '管理模式'}
                        </Button>
                    )}
                    
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleDownloadTemplate}
                        className="h-8 px-3 text-xs border-manus-border text-manus-muted hover:text-manus-text hover:bg-manus-hover"
                    >
                        <Download className="h-3.5 w-3.5 mr-1.5" />
                        下载模板
                    </Button>
                    
                    <label className="cursor-pointer">
                        <input
                            type="file"
                            accept=".xlsx,.xls"
                            onChange={handleImportFile}
                            className="hidden"
                            disabled={isImporting}
                        />
                        <Button
                            variant="default"
                            size="sm"
                            asChild
                            className="h-8 px-3 text-xs"
                            disabled={isImporting}
                        >
                            <span>
                                {isImporting ? (
                                    <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                                ) : (
                                    <Upload className="h-3.5 w-3.5 mr-1.5" />
                                )}
                                {isImporting ? '导入中...' : '导入商品'}
                            </span>
                        </Button>
                    </label>
                </div>
            </header>
            
            {/* 导入结果提示 */}
            {importResult && (
                <div className={cn(
                    "mx-6 mt-4 p-3 rounded-lg text-sm flex items-start gap-2",
                    importResult.failed > 0 
                        ? "bg-red-500/10 border border-red-500/30 text-red-400"
                        : "bg-green-500/10 border border-green-500/30 text-green-400"
                )}>
                    <FileSpreadsheet className="h-4 w-4 mt-0.5 shrink-0" />
                    <div>
                        <p>导入完成: 成功 {importResult.success} 条，失败 {importResult.failed} 条</p>
                        {importResult.errors.length > 0 && (
                            <ul className="mt-1 text-xs opacity-80">
                                {importResult.errors.slice(0, 3).map((err, i) => (
                                    <li key={i}>• {err}</li>
                                ))}
                                {importResult.errors.length > 3 && (
                                    <li>• ...还有 {importResult.errors.length - 3} 条错误</li>
                                )}
                            </ul>
                        )}
                    </div>
                    <button
                        onClick={() => setImportResult(null)}
                        className="ml-auto text-current opacity-60 hover:opacity-100"
                    >
                        ×
                    </button>
                </div>
            )}

            {/* 搜索和筛选 */}
            <div className="px-6 py-4 border-b border-manus-border">
                <div className="flex items-center gap-4 max-w-4xl">
                    {/* 搜索框 */}
                    <div className="flex-1 relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-manus-subtle" />
                        <Input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder="搜索商品..."
                            className="pl-10 bg-manus-secondary border-manus-border text-manus-text placeholder:text-manus-subtle focus:border-accent"
                        />
                    </div>

                    {/* 分类筛选 */}
                    <div className="flex items-center gap-1">
                        {CATEGORIES.map((cat) => (
                            <Button
                                key={cat.label}
                                variant="ghost"
                                size="sm"
                                onClick={() => setSelectedCategory(cat.value)}
                                className={cn(
                                    "h-8 px-3 rounded-lg text-sm transition-all",
                                    selectedCategory === cat.value
                                        ? "bg-accent text-white hover:bg-accent/90"
                                        : "text-manus-muted hover:text-manus-text hover:bg-manus-hover"
                                )}
                            >
                                {cat.label}
                            </Button>
                        ))}
                    </div>
                </div>
            </div>

            {/* 商品列表 */}
            <ScrollArea className="flex-1">
                <div className="p-6">
                    {loading ? (
                        <div className="flex items-center justify-center h-64">
                            <Loader2 className="h-8 w-8 animate-spin text-manus-muted" />
                        </div>
                    ) : products.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-64 text-center">
                            <div className="w-16 h-16 rounded-2xl bg-manus-tertiary flex items-center justify-center mb-4">
                                <Package className="h-8 w-8 text-manus-muted" />
                            </div>
                            <h2 className="text-lg font-medium text-manus-text mb-2">暂无商品</h2>
                            <p className="text-sm text-manus-muted">
                                {selectedCategory ? `“${selectedCategory}” 分类下暂无商品` : '暂未上架任何商品'}
                            </p>
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                            {products.map((product) => (
                                <div
                                    key={product.id}
                                    className="group bg-manus-secondary rounded-xl overflow-hidden border border-manus-border hover:border-accent/50 transition-all cursor-pointer hover:shadow-lg"
                                    onClick={() => openProductDetail(product)}
                                >
                                    {/* 商品图片 */}
                                    <div className="aspect-square bg-manus-tertiary relative overflow-hidden">
                                        {getMuseumPrimaryImageUrl(product.image_urls) ? (
                                            <img
                                                src={getMuseumPrimaryImageUrl(product.image_urls) ?? undefined}
                                                alt={product.name}
                                                className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                                            />
                                        ) : (
                                            <div className="flex items-center justify-center h-full">
                                                <ShoppingBag className="h-12 w-12 text-manus-muted" />
                                            </div>
                                        )}
                                        <span className="absolute top-2 right-2 px-2 py-1 bg-accent/90 text-white text-xs rounded-md font-medium">
                                            {product.category}
                                        </span>
                                    </div>

                                    {/* 商品信息 */}
                                    <div className="p-4">
                                        <h3 className="font-medium text-manus-text truncate text-sm">
                                            {product.name}
                                        </h3>
                                        <p className="text-xs text-manus-muted mt-1 line-clamp-2 h-8">
                                            {product.description || '暂无描述'}
                                        </p>
                                        <div className="flex items-center justify-between mt-3">
                                            <span className="text-base font-bold text-accent">
                                                ¥{product.price.toFixed(2)}
                                            </span>
                                            <span className="text-xs text-manus-subtle">
                                                库存 {product.stock}
                                            </span>
                                        </div>
                                        {/* BUG4: 管理模式下显示编辑按钮 */}
                                        {isManageMode && (
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={(e) => openEditDialog(product, e)}
                                                className="w-full mt-3 h-8 text-xs border-amber-500/50 text-amber-400 hover:bg-amber-500/10"
                                            >
                                                <Pencil className="h-3 w-3 mr-1.5" />
                                                编辑商品
                                            </Button>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </ScrollArea>

            {/* 商品详情弹窗 */}
            <Dialog open={!!selectedProduct} onOpenChange={(open) => !open && closeProductDetail()}>
                <DialogContent className="bg-manus-secondary border-manus-border text-manus-text max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
                    <DialogHeader className="flex-shrink-0">
                        <DialogTitle className="text-lg font-medium text-manus-text pr-8">
                            {selectedProduct?.name}
                        </DialogTitle>
                        <DialogDescription className="sr-only">
                            查看文创商品详情，并可向 AI 导购咨询商品信息。
                        </DialogDescription>
                    </DialogHeader>
                    
                    {selectedProduct && (
                        <ScrollArea className="flex-1 -mx-6 px-6">
                            <div className="space-y-5 pb-4">
                                {/* 商品图片 */}
                                <div className="aspect-video bg-manus-tertiary rounded-xl overflow-hidden">
                                    {getMuseumPrimaryImageUrl(selectedProduct.image_urls) ? (
                                        <img
                                            src={getMuseumPrimaryImageUrl(selectedProduct.image_urls) ?? undefined}
                                            alt={selectedProduct.name}
                                            className="w-full h-full object-cover"
                                        />
                                    ) : (
                                        <div className="flex items-center justify-center h-full">
                                            <ShoppingBag className="h-16 w-16 text-manus-muted" />
                                        </div>
                                    )}
                                </div>

                                {/* 商品信息 */}
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between">
                                        <span className="text-2xl font-bold text-accent">
                                            ¥{selectedProduct.price.toFixed(2)}
                                        </span>
                                        <span className="px-3 py-1 bg-accent/10 text-accent text-sm rounded-lg">
                                            {selectedProduct.category}
                                        </span>
                                    </div>
                                    <p className="text-manus-muted leading-relaxed">
                                        {selectedProduct.description || '暂无详细描述'}
                                    </p>
                                    <p className="text-sm text-manus-subtle">
                                        库存: {selectedProduct.stock} 件
                                    </p>
                                </div>

                                {/* AI 导购咨询 */}
                                <div className="border-t border-manus-border pt-5">
                                    <div className="flex items-center gap-2 mb-4">
                                        <div className="w-6 h-6 rounded-md bg-accent/20 flex items-center justify-center">
                                            <Bot className="h-3.5 w-3.5 text-accent" />
                                        </div>
                                        <h3 className="text-sm font-medium text-manus-text">AI 导购咨询</h3>
                                    </div>
                                    
                                    {/* 咨询输入 */}
                                    <div className={cn(
                                        "relative overflow-hidden bg-manus-tertiary rounded-xl border transition-all",
                                        "border-manus-border focus-within:border-accent/50"
                                    )}>
                                        <Input
                                            value={inquiryQuestion}
                                            onChange={(e) => setInquiryQuestion(e.target.value)}
                                            onKeyDown={(e) => e.key === 'Enter' && handleInquiry()}
                                            placeholder="请问这件商品..."
                                            className="border-0 bg-transparent text-manus-text placeholder:text-manus-subtle focus-visible:ring-0 pr-12"
                                            disabled={isInquiring}
                                        />
                                        <Button
                                            onClick={handleInquiry}
                                            disabled={!inquiryQuestion.trim() || isInquiring}
                                            size="icon"
                                            className={cn(
                                                "absolute right-1.5 top-1/2 -translate-y-1/2 h-7 w-7 rounded-lg transition-all",
                                                inquiryQuestion.trim() && !isInquiring
                                                    ? "bg-accent hover:bg-accent/90 text-white"
                                                    : "bg-manus-hover text-manus-subtle"
                                            )}
                                        >
                                            {isInquiring ? (
                                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                            ) : (
                                                <ArrowUp className="h-3.5 w-3.5" />
                                            )}
                                        </Button>
                                    </div>

                                    {/* 快捷问题 */}
                                    <div className="flex flex-wrap gap-2 mt-3">
                                        {[
                                            '这个商品有什么特点？',
                                            '适合送给谁？',
                                            '有什么注意事项？',
                                        ].map((q) => (
                                            <button
                                                key={q}
                                                onClick={() => setInquiryQuestion(q)}
                                                className="px-3 py-1.5 text-xs bg-manus-hover hover:bg-manus-tertiary text-manus-muted hover:text-manus-text rounded-lg transition-colors"
                                            >
                                                {q}
                                            </button>
                                        ))}
                                    </div>

                                    {/* 咨询答复 */}
                                    {inquiryAnswer && (
                                        <div className="mt-4 p-4 bg-manus-hover rounded-xl">
                                            <div className="flex items-start gap-3">
                                                <div className="w-6 h-6 rounded-md bg-accent/20 flex items-center justify-center shrink-0 mt-0.5">
                                                    <Bot className="h-3.5 w-3.5 text-accent" />
                                                </div>
                                                <p className="text-sm text-manus-text leading-relaxed whitespace-pre-wrap">
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
            
            {/* BUG4: 商品编辑弹窗 */}
            <Dialog open={!!editingProduct} onOpenChange={(open) => !open && closeEditDialog()}>
                <DialogContent className="bg-manus-secondary border-manus-border text-manus-text max-w-md">
                    <DialogHeader>
                        <DialogTitle className="text-lg font-medium text-manus-text flex items-center gap-2">
                            <Pencil className="h-4 w-4 text-amber-400" />
                            编辑商品
                        </DialogTitle>
                        <DialogDescription className="sr-only">
                            编辑当前文创商品的名称、价格、库存和描述。
                        </DialogDescription>
                    </DialogHeader>
                    
                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <label className="text-sm text-manus-muted">商品名称</label>
                            <Input
                                value={editForm.name}
                                onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                                className="bg-manus-tertiary border-manus-border text-manus-text"
                            />
                        </div>
                        
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <label className="text-sm text-manus-muted">价格 (¥)</label>
                                <Input
                                    type="number"
                                    value={editForm.price}
                                    onChange={(e) => setEditForm({ ...editForm, price: e.target.value })}
                                    className="bg-manus-tertiary border-manus-border text-manus-text"
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm text-manus-muted">库存</label>
                                <Input
                                    type="number"
                                    value={editForm.stock}
                                    onChange={(e) => setEditForm({ ...editForm, stock: e.target.value })}
                                    className="bg-manus-tertiary border-manus-border text-manus-text"
                                />
                            </div>
                        </div>
                        
                        <div className="space-y-2">
                            <label className="text-sm text-manus-muted">商品描述</label>
                            <textarea
                                value={editForm.description}
                                onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                                rows={3}
                                className="w-full px-3 py-2 bg-manus-tertiary border border-manus-border rounded-md text-manus-text text-sm resize-none focus:outline-none focus:border-accent"
                            />
                        </div>
                    </div>
                    
                    <div className="flex justify-end gap-2">
                        <Button
                            variant="outline"
                            onClick={closeEditDialog}
                            className="border-manus-border text-manus-muted hover:text-manus-text"
                        >
                            <X className="h-4 w-4 mr-1.5" />
                            取消
                        </Button>
                        <Button
                            onClick={handleSaveProduct}
                            disabled={isSaving}
                            className="bg-amber-500 hover:bg-amber-600 text-white"
                        >
                            {isSaving ? (
                                <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
                            ) : (
                                <Save className="h-4 w-4 mr-1.5" />
                            )}
                            {isSaving ? '保存中...' : '保存'}
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    )
}
