/**
 * 摄像头抓拍组件
 * 
 * 点击打开摄像头预览，再次点击拍照
 */
import { useState, useRef, useCallback } from 'react'
import { Camera, X, Check, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

interface CameraCaptureProps {
    onCapture: (file: File) => void
    onError?: (error: string) => void
    disabled?: boolean
    className?: string
    size?: 'sm' | 'md' | 'lg'
    variant?: 'default' | 'outline'
}

export function CameraCapture({
    onCapture,
    onError,
    disabled = false,
    className,
    size = 'md',
    variant = 'default'
}: CameraCaptureProps) {
    const [isOpen, setIsOpen] = useState(false)
    const [isCapturing, setIsCapturing] = useState(false)
    const [previewUrl, setPreviewUrl] = useState<string | null>(null)
    
    const videoRef = useRef<HTMLVideoElement>(null)
    const canvasRef = useRef<HTMLCanvasElement>(null)
    const streamRef = useRef<MediaStream | null>(null)
    
    const startCamera = useCallback(async () => {
        if (disabled) return
        
        // 先打开弹窗，让 video 元素挂载到 DOM
        setIsOpen(true)
        setPreviewUrl(null)
        
        // 等待下一个渲染周期，确保 video 元素已挂载
        await new Promise(resolve => setTimeout(resolve, 100))
        
        try {
            console.log('📷 [Camera] 请求摄像头权限...')
            const stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    facingMode: 'environment',
                    width: { ideal: 1280 },
                    height: { ideal: 720 }
                }
            })
            
            streamRef.current = stream
            
            if (videoRef.current) {
                videoRef.current.srcObject = stream
                
                // 等待视频元数据加载完成后再播放
                await new Promise<void>((resolve, reject) => {
                    const video = videoRef.current!
                    video.onloadedmetadata = () => {
                        console.log('📷 [Camera] 视频元数据已加载')
                        resolve()
                    }
                    video.onerror = () => reject(new Error('视频加载失败'))
                    // 超时保护
                    setTimeout(() => resolve(), 3000)
                })
                
                await videoRef.current.play()
                console.log('📷 [Camera] 摄像头已启动')
            }
            
        } catch (error) {
            console.error('📷 [Camera] 启动失败:', error)
            onError?.('无法访问摄像头，请检查权限设置')
            setIsOpen(false)
        }
    }, [disabled, onError])
    
    const stopCamera = useCallback(() => {
        if (streamRef.current) {
            streamRef.current.getTracks().forEach(track => track.stop())
            streamRef.current = null
        }
        if (videoRef.current) {
            videoRef.current.srcObject = null
        }
        setIsOpen(false)
        setPreviewUrl(null)
        console.log('📷 [Camera] 摄像头已关闭')
    }, [])
    
    const capturePhoto = useCallback(() => {
        if (!videoRef.current || !canvasRef.current) return
        
        setIsCapturing(true)
        
        const video = videoRef.current
        const canvas = canvasRef.current
        
        canvas.width = video.videoWidth
        canvas.height = video.videoHeight
        
        const ctx = canvas.getContext('2d')
        if (ctx) {
            ctx.drawImage(video, 0, 0)
            const dataUrl = canvas.toDataURL('image/jpeg', 0.9)
            setPreviewUrl(dataUrl)
        }
        
        setIsCapturing(false)
        console.log('📷 [Camera] 已拍照')
    }, [])
    
    const confirmCapture = useCallback(() => {
        if (!canvasRef.current) return
        
        canvasRef.current.toBlob((blob) => {
            if (blob) {
                const file = new File([blob], `capture_${Date.now()}.jpg`, { type: 'image/jpeg' })
                console.log('📷 [Camera] 确认拍照:', file.size, 'bytes')
                onCapture(file)
                stopCamera()
            }
        }, 'image/jpeg', 0.9)
    }, [onCapture, stopCamera])
    
    const retakePhoto = useCallback(() => {
        setPreviewUrl(null)
    }, [])
    
    const sizeClasses = {
        sm: 'w-10 h-10',
        md: 'w-14 h-14',
        lg: 'w-20 h-20'
    }
    
    const iconSizes = {
        sm: 'h-4 w-4',
        md: 'h-6 w-6',
        lg: 'h-8 w-8'
    }
    
    return (
        <>
            {/* 抓拍按钮 */}
            <button
                onClick={startCamera}
                disabled={disabled}
                className={cn(
                    "rounded-full flex items-center justify-center transition-all duration-200",
                    variant === 'default'
                        ? "bg-manus-secondary hover:bg-manus-tertiary border border-manus-border"
                        : "bg-transparent hover:bg-manus-secondary/50 border border-manus-border/50",
                    sizeClasses[size],
                    disabled && "opacity-50 cursor-not-allowed",
                    className
                )}
                title="摄像头抓拍"
            >
                <Camera className={cn(iconSizes[size], "text-manus-text")} />
            </button>
            
            {/* 摄像头预览弹窗 */}
            {isOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80">
                    <div className="relative w-full max-w-2xl mx-4">
                        {/* 关闭按钮 */}
                        <button
                            onClick={stopCamera}
                            className="absolute -top-12 right-0 p-2 text-white/80 hover:text-white transition-colors"
                        >
                            <X className="h-6 w-6" />
                        </button>
                        
                        {/* 视频/预览区域 */}
                        <div className="relative aspect-video bg-black rounded-xl overflow-hidden">
                            <video
                                ref={videoRef}
                                autoPlay
                                playsInline
                                muted
                                className={cn(
                                    "w-full h-full object-cover",
                                    previewUrl && "hidden"
                                )}
                            />
                            
                            {previewUrl && (
                                <img
                                    src={previewUrl}
                                    alt="预览"
                                    className="w-full h-full object-cover"
                                />
                            )}
                            
                            <canvas ref={canvasRef} className="hidden" />
                        </div>
                        
                        {/* 操作按钮 */}
                        <div className="flex items-center justify-center gap-6 mt-6">
                            {previewUrl ? (
                                <>
                                    <Button
                                        onClick={retakePhoto}
                                        variant="outline"
                                        size="lg"
                                        className="rounded-full px-6"
                                    >
                                        重拍
                                    </Button>
                                    <Button
                                        onClick={confirmCapture}
                                        size="lg"
                                        className="rounded-full px-6 bg-accent hover:bg-accent/90 text-white"
                                    >
                                        <Check className="h-5 w-5 mr-2" />
                                        确认使用
                                    </Button>
                                </>
                            ) : (
                                <button
                                    onClick={capturePhoto}
                                    disabled={isCapturing}
                                    className="w-16 h-16 rounded-full bg-white flex items-center justify-center hover:bg-white/90 transition-colors"
                                >
                                    {isCapturing ? (
                                        <Loader2 className="h-8 w-8 text-black animate-spin" />
                                    ) : (
                                        <div className="w-12 h-12 rounded-full border-4 border-black" />
                                    )}
                                </button>
                            )}
                        </div>
                        
                        {/* 提示文字 */}
                        <p className="text-center text-white/60 text-sm mt-4">
                            {previewUrl ? '确认使用这张照片？' : '点击拍照按钮进行拍摄'}
                        </p>
                    </div>
                </div>
            )}
        </>
    )
}
