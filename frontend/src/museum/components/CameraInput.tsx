/**
 * 相机输入组件
 * 
 * 支持拍照/上传图片进行人物识别
 * VLM 分析时显示模糊预览
 */
import { useState, useRef, useCallback } from 'react'
import { Camera, Upload, X, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface CameraInputProps {
    onImageCaptured: (file: File) => void
    isAnalyzing: boolean
    disabled?: boolean
}

export function CameraInput({ onImageCaptured, isAnalyzing, disabled }: CameraInputProps) {
    const [preview, setPreview] = useState<string | null>(null)
    const fileInputRef = useRef<HTMLInputElement>(null)

    const handleCapture = useCallback((file: File) => {
        // 1. 立即显示预览
        const reader = new FileReader()
        reader.onload = (e) => {
            setPreview(e.target?.result as string)
        }
        reader.readAsDataURL(file)

        // 2. 触发分析
        onImageCaptured(file)
    }, [onImageCaptured])

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0]
        if (file) {
            handleCapture(file)
        }
    }

    const handleClear = () => {
        setPreview(null)
        if (fileInputRef.current) {
            fileInputRef.current.value = ''
        }
    }

    return (
        <div className="relative">
            {/* 隐藏的文件输入 */}
            <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                capture="environment"
                className="hidden"
                onChange={handleFileChange}
                disabled={disabled}
            />

            {/* 预览区域 */}
            {preview ? (
                <div className="relative mb-4 rounded-xl overflow-hidden">
                    <img
                        src={preview}
                        alt="访客照片"
                        className={cn(
                            "w-full max-h-48 object-cover transition-all duration-300",
                            isAnalyzing && "blur-sm scale-105"
                        )}
                    />

                    {/* 分析中遮罩 */}
                    {isAnalyzing && (
                        <div className="absolute inset-0 flex items-center justify-center bg-black/40 backdrop-blur-[2px]">
                            <div className="flex flex-col items-center gap-2 text-white">
                                <Loader2 className="h-8 w-8 animate-spin text-amber-400" />
                                <span className="text-sm font-medium">正在观察您的特征...</span>
                            </div>
                        </div>
                    )}

                    {/* 清除按钮 */}
                    {!isAnalyzing && (
                        <button
                            onClick={handleClear}
                            className="absolute top-2 right-2 p-1.5 bg-black/50 rounded-full text-white hover:bg-black/70 transition-colors"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    )}
                </div>
            ) : (
                /* 上传区域 */
                <div
                    onClick={() => fileInputRef.current?.click()}
                    className={cn(
                        "flex flex-col items-center justify-center p-8 mb-4",
                        "border-2 border-dashed border-slate-600 rounded-xl",
                        "cursor-pointer transition-colors",
                        "hover:border-amber-500 hover:bg-slate-800/50",
                        disabled && "opacity-50 cursor-not-allowed"
                    )}
                >
                    <Camera className="h-12 w-12 text-slate-500 mb-3" />
                    <span className="text-slate-400 text-center">
                        点击拍照或上传照片
                    </span>
                    <span className="text-slate-500 text-sm mt-1">
                        我们将识别您的特征并提供个性化服务
                    </span>
                </div>
            )}

            {/* 快捷按钮 */}
            {!preview && (
                <div className="flex gap-3">
                    <Button
                        variant="outline"
                        className="flex-1 border-slate-600 text-slate-300 hover:bg-slate-800"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={disabled}
                    >
                        <Camera className="h-4 w-4 mr-2" />
                        拍照
                    </Button>
                    <Button
                        variant="outline"
                        className="flex-1 border-slate-600 text-slate-300 hover:bg-slate-800"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={disabled}
                    >
                        <Upload className="h-4 w-4 mr-2" />
                        上传
                    </Button>
                </div>
            )}
        </div>
    )
}

export default CameraInput
