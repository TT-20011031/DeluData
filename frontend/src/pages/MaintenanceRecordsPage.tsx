import { useCallback, useEffect, useMemo, useState } from 'react'
import {
    Camera,
    CheckCircle2,
    FileText,
    ImagePlus,
    Loader2,
    Trash2,
    UploadCloud,
    Wrench,
} from 'lucide-react'

import { API_BASE_URL } from '@/config'
import { VoiceRecorder } from '@/shared/voice'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/components/ui/toast'
import {
    maintenanceRecordService,
    type MaintenanceRecordResponse,
} from '@/services/maintenanceRecordService'
import { cn } from '@/lib/utils'

const MAX_IMAGE_COUNT = 9
const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const ALLOWED_IMAGE_TYPES = new Set(['image/jpeg', 'image/jpg', 'image/png', 'image/webp'])
const PUBLIC_TRANSCRIBE_ENDPOINT = `${API_BASE_URL}/public/maintenance-records/transcribe`

interface SelectedImage {
    file: File
    previewUrl: string
}

function formatBytes(bytes: number) {
    if (bytes <= 0) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB']
    let value = bytes
    let unit = 0
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024
        unit += 1
    }
    return unit === 0 ? `${Math.round(value)} ${units[unit]}` : `${value.toFixed(1)} ${units[unit]}`
}

function mapErrorMessage(raw: string) {
    const labels: Record<string, string> = {
        maintenance_service_user_missing: '维修记录服务账号 test1 不存在，请联系管理员。',
        maintenance_service_user_disabled: '维修记录服务账号已停用，请联系管理员。',
        maintenance_images_required: '请至少上传一张维修图片。',
        maintenance_description_required: '请先录音转写或手动填写维修说明。',
        maintenance_images_too_many: `最多上传 ${MAX_IMAGE_COUNT} 张维修图片。`,
        maintenance_image_type_unsupported: '仅支持 JPG、PNG、WEBP 图片。',
        maintenance_image_too_large: `单张图片不能超过 ${formatBytes(MAX_IMAGE_BYTES)}。`,
        maintenance_image_empty: '图片文件为空，请重新选择。',
        maintenance_image_invalid: '图片解析失败，请重新选择清晰的 JPG、PNG 或 WEBP 图片。',
        maintenance_rate_limited: '提交太频繁，请稍后再试。',
    }
    return labels[raw] || raw
}

export default function MaintenanceRecordsPage() {
    const { toast } = useToast()
    const [images, setImages] = useState<SelectedImage[]>([])
    const [description, setDescription] = useState('')
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [result, setResult] = useState<MaintenanceRecordResponse | null>(null)

    const imageBytes = useMemo(
        () => images.reduce((total, item) => total + item.file.size, 0),
        [images]
    )

    useEffect(() => {
        return () => {
            images.forEach((item) => URL.revokeObjectURL(item.previewUrl))
        }
    }, [images])

    const rejectFiles = useCallback((files: File[]) => {
        if (files.length === 0) return
        toast({
            type: 'warning',
            title: `${files.length} 张图片已跳过`,
            description: files.map((file) => file.name).slice(0, 3).join(', '),
            duration: 5000,
        })
    }, [toast])

    const handleSelectImages = useCallback((files: FileList | null) => {
        if (!files) return

        const incoming = Array.from(files)
        const allowed: File[] = []
        const rejected: File[] = []
        for (const file of incoming) {
            if (!ALLOWED_IMAGE_TYPES.has(file.type) || file.size > MAX_IMAGE_BYTES || file.size <= 0) {
                rejected.push(file)
                continue
            }
            allowed.push(file)
        }

        const remaining = Math.max(MAX_IMAGE_COUNT - images.length, 0)
        const accepted = allowed.slice(0, remaining)
        const overflow = allowed.slice(remaining)
        rejectFiles([...rejected, ...overflow])

        if (accepted.length > 0) {
            setImages((prev) => [
                ...prev,
                ...accepted.map((file) => ({
                    file,
                    previewUrl: URL.createObjectURL(file),
                })),
            ])
            setResult(null)
        }
    }, [images.length, rejectFiles])

    const removeImage = useCallback((index: number) => {
        setImages((prev) => {
            const next = [...prev]
            const [removed] = next.splice(index, 1)
            if (removed) URL.revokeObjectURL(removed.previewUrl)
            return next
        })
    }, [])

    const handleTranscript = useCallback((text: string) => {
        const clean = text.trim()
        if (!clean) return
        setDescription((prev) => {
            const prefix = prev.trim()
            return prefix ? `${prefix}\n${clean}` : clean
        })
        setResult(null)
    }, [])

    const handleSubmit = useCallback(async () => {
        const cleanDescription = description.trim()
        if (images.length === 0) {
            toast({ type: 'warning', title: '请先添加维修图片' })
            return
        }
        if (!cleanDescription) {
            toast({ type: 'warning', title: '请填写维修说明' })
            return
        }

        const formData = new FormData()
        formData.append('description', cleanDescription)
        images.forEach((item) => formData.append('images', item.file))

        setIsSubmitting(true)
        setResult(null)
        try {
            const response = await maintenanceRecordService.submit(formData)
            setResult(response)
            setImages((prev) => {
                prev.forEach((item) => URL.revokeObjectURL(item.previewUrl))
                return []
            })
            setDescription('')
            toast({
                type: 'success',
                title: '维修记录已提交',
                description: response.name,
                duration: 5000,
            })
        } catch (error) {
            toast({
                type: 'error',
                title: '提交失败',
                description: mapErrorMessage(error instanceof Error ? error.message : '未知错误'),
                duration: 7000,
            })
        } finally {
            setIsSubmitting(false)
        }
    }, [description, images, toast])

    const canSubmit = images.length > 0 && description.trim().length > 0 && !isSubmitting

    return (
        <div className="min-h-screen bg-[#f4f7f5] text-[#18211c]">
            <main className="mx-auto flex min-h-screen w-full max-w-[520px] flex-col px-4 pb-24 pt-5">
                <header className="pb-4">
                    <div className="flex items-center gap-2 text-[22px] font-semibold tracking-normal">
                        <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-[#176b4d] text-white">
                            <Wrench className="h-5 w-5" />
                        </span>
                        维修记录采集
                    </div>
                    <p className="mt-2 text-sm leading-6 text-[#526158]">
                        拍摄维修图片，录入语音或文字说明，系统会生成 PDF 并保存到维修记录知识库。
                    </p>
                </header>

                <section className="rounded-lg border border-[#d8e2db] bg-white">
                    <div className="flex items-center justify-between border-b border-[#e5ece7] px-4 py-3">
                        <div>
                            <h2 className="text-base font-semibold">维修图片</h2>
                            <p className="mt-1 text-xs text-[#66756b]">
                                {images.length}/{MAX_IMAGE_COUNT} 张，合计 {formatBytes(imageBytes)}
                            </p>
                        </div>
                        <Label
                            className={cn(
                                'inline-flex h-10 cursor-pointer items-center gap-2 rounded-md bg-[#176b4d] px-3 text-sm font-medium text-white',
                                images.length >= MAX_IMAGE_COUNT && 'pointer-events-none opacity-50'
                            )}
                        >
                            <ImagePlus className="h-4 w-4" />
                            添加
                            <input
                                type="file"
                                accept="image/jpeg,image/png,image/webp"
                                capture="environment"
                                multiple
                                className="hidden"
                                disabled={images.length >= MAX_IMAGE_COUNT || isSubmitting}
                                onChange={(event) => {
                                    handleSelectImages(event.target.files)
                                    event.target.value = ''
                                }}
                            />
                        </Label>
                    </div>

                    <div className="p-4">
                        {images.length === 0 ? (
                            <Label className="flex min-h-[220px] cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-[#bccbc2] bg-[#f8faf8] text-center">
                                <Camera className="h-10 w-10 text-[#176b4d]" />
                                <div>
                                    <p className="text-sm font-semibold">拍摄或选择维修现场图片</p>
                                    <p className="mt-1 text-xs text-[#66756b]">JPG / PNG / WEBP，单张不超过 10MB</p>
                                </div>
                                <input
                                    type="file"
                                    accept="image/jpeg,image/png,image/webp"
                                    capture="environment"
                                    multiple
                                    className="hidden"
                                    disabled={isSubmitting}
                                    onChange={(event) => {
                                        handleSelectImages(event.target.files)
                                        event.target.value = ''
                                    }}
                                />
                            </Label>
                        ) : (
                            <div className="grid grid-cols-3 gap-2">
                                {images.map((item, index) => (
                                    <div
                                        key={`${item.file.name}-${item.previewUrl}`}
                                        className="relative aspect-square overflow-hidden rounded-md border border-[#d8e2db] bg-[#eef3ef]"
                                    >
                                        <img
                                            src={item.previewUrl}
                                            alt={`维修图片 ${index + 1}`}
                                            className="h-full w-full object-cover"
                                        />
                                        <button
                                            type="button"
                                            className="absolute right-1.5 top-1.5 flex h-7 w-7 items-center justify-center rounded-full bg-black/55 text-white"
                                            disabled={isSubmitting}
                                            onClick={() => removeImage(index)}
                                            title="移除图片"
                                        >
                                            <Trash2 className="h-3.5 w-3.5" />
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </section>

                <section className="mt-4 rounded-lg border border-[#d8e2db] bg-white">
                    <div className="border-b border-[#e5ece7] px-4 py-3">
                        <h2 className="text-base font-semibold">语音说明</h2>
                        <p className="mt-1 text-xs text-[#66756b]">录音转文字后，可以继续手动修改。</p>
                    </div>
                    <div className="space-y-5 p-4">
                        <div className="flex min-h-[154px] justify-center rounded-lg border border-[#d8e2db] bg-[#f8faf8] py-8">
                            <VoiceRecorder
                                size="lg"
                                interactionMode="tap-confirm"
                                disabled={isSubmitting}
                                apiEndpoint={PUBLIC_TRANSCRIBE_ENDPOINT}
                                onTranscript={handleTranscript}
                                onError={(error) => {
                                    toast({
                                        type: 'warning',
                                        title: '语音识别失败',
                                        description: mapErrorMessage(error),
                                    })
                                }}
                            />
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="maintenance-description" className="text-sm font-semibold">
                                维修说明
                            </Label>
                            <Textarea
                                id="maintenance-description"
                                value={description}
                                onChange={(event) => {
                                    setDescription(event.target.value)
                                    setResult(null)
                                }}
                                disabled={isSubmitting}
                                placeholder="描述故障现象、维修过程和处理结果..."
                                className="min-h-[180px] resize-none border-[#d8e2db] bg-white text-base text-[#18211c] placeholder:text-[#89968d]"
                            />
                        </div>
                    </div>
                </section>

                {result && (
                    <section className="mt-4 rounded-lg border border-[#b7dfc7] bg-[#eefaf2] p-4">
                        <div className="flex items-start gap-3">
                            <CheckCircle2 className="mt-0.5 h-5 w-5 text-[#176b4d]" />
                            <div className="min-w-0 flex-1">
                                <h3 className="text-sm font-semibold">已生成 PDF 并保存</h3>
                                <p className="mt-1 break-all text-xs leading-5 text-[#526158]">{result.name}</p>
                                <Button
                                    type="button"
                                    variant="outline"
                                    className="mt-3 h-9 border-[#b7dfc7] bg-white text-[#176b4d]"
                                    onClick={() => setResult(null)}
                                >
                                    继续录入
                                </Button>
                            </div>
                        </div>
                    </section>
                )}

                <section className="mt-4 rounded-lg border border-[#d8e2db] bg-white p-4">
                    <div className="flex items-center gap-2 text-sm font-semibold">
                        <FileText className="h-4 w-4 text-[#176b4d]" />
                        保存说明
                    </div>
                    <p className="mt-2 text-xs leading-5 text-[#66756b]">
                        提交后会生成 PDF 维修记录，保存到全局共享 / 维修记录。语音不可用时，也可以直接手动填写维修说明。
                    </p>
                </section>
            </main>

            <div className="fixed inset-x-0 bottom-0 z-40 border-t border-[#d8e2db] bg-white/95 px-4 py-3 backdrop-blur">
                <div className="mx-auto max-w-[520px]">
                    <Button
                        onClick={() => void handleSubmit()}
                        disabled={!canSubmit}
                        className="h-12 w-full gap-2 bg-[#176b4d] text-base font-semibold text-white hover:bg-[#12543d] disabled:bg-[#a6aea9]"
                    >
                        {isSubmitting ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <UploadCloud className="h-4 w-4" />
                        )}
                        生成并保存维修记录
                    </Button>
                </div>
            </div>
        </div>
    )
}
