/**
 * HTML 报告面板组件
 * 
 * 用于展示 ChartWorker 生成的 HTML 可视化报告
 */
import { useState, useRef, useEffect } from 'react'
import { X, Maximize2, Minimize2, GripHorizontal, Download, FileText, Image as ImageIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useChartExport } from '@/hooks/chat/useChartExport'

interface HtmlReportPanelProps {
    report: {
        id: string
        title: string
        content: string
    }
    isOpen: boolean
    onClose: () => void
}

export function HtmlReportPanel({ report, isOpen, onClose }: HtmlReportPanelProps) {
    const [isMaximized, setIsMaximized] = useState(false)
    const [size, setSize] = useState({ width: 600, height: 450 })
    const [isDragging, setIsDragging] = useState(false)
    const panelRef = useRef<HTMLDivElement>(null)
    const iframeRef = useRef<HTMLIFrameElement>(null)
    const startPosRef = useRef({ x: 0, y: 0, width: 0, height: 0 })

    const { exportPdf, exportImage } = useChartExport()

    // 下载 HTML 文件
    const handleDownloadHtml = () => {
        const blob = new Blob([report.content], { type: 'text/html;charset=utf-8' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `${report.id.replace('.html', '')}_${Date.now()}.html`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
    }

    // [异步流式响应] 导出为 PDF
    const handleExportPdf = async () => {
        const iframe = iframeRef.current
        if (iframe?.contentDocument?.body) {
            await exportPdf(iframe.contentDocument.body, report.id.replace('.html', ''))
        }
    }

    // [异步流式响应] 导出为图片
    const handleExportImage = async () => {
        const iframe = iframeRef.current
        if (iframe?.contentDocument?.body) {
            await exportImage(iframe.contentDocument.body, report.id.replace('.html', ''))
        }
    }

    // 拖拽调整大小
    const handleMouseDown = (e: React.MouseEvent) => {
        e.preventDefault()
        setIsDragging(true)
        startPosRef.current = {
            x: e.clientX,
            y: e.clientY,
            width: size.width,
            height: size.height
        }
    }

    useEffect(() => {
        if (!isDragging) return

        const handleMouseMove = (e: MouseEvent) => {
            const deltaX = startPosRef.current.x - e.clientX
            const deltaY = startPosRef.current.y - e.clientY
            setSize({
                width: Math.max(400, startPosRef.current.width + deltaX),
                height: Math.max(300, startPosRef.current.height + deltaY)
            })
        }

        const handleMouseUp = () => {
            setIsDragging(false)
        }

        document.addEventListener('mousemove', handleMouseMove)
        document.addEventListener('mouseup', handleMouseUp)
        return () => {
            document.removeEventListener('mousemove', handleMouseMove)
            document.removeEventListener('mouseup', handleMouseUp)
        }
    }, [isDragging])

    if (!isOpen) return null

    const panelStyles = isMaximized
        ? { width: '90vw', height: '90vh', right: '5vw', bottom: '5vh' }
        : { width: size.width, height: size.height }

    return (
        <div
            ref={panelRef}
            className="fixed z-50 bg-manus-bg/95 backdrop-blur-xl rounded-xl shadow-2xl border border-manus-border/50 overflow-hidden flex flex-col"
            style={{
                ...panelStyles,
                right: isMaximized ? '5vw' : '24px',
                bottom: isMaximized ? '5vh' : '120px',
            }}
        >
            {/* 标题栏 */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-manus-border/30 bg-gradient-to-r from-accent/5 to-transparent">
                <div className="flex items-center gap-2">
                    <span className="text-accent font-medium text-sm">{report.title}</span>
                </div>
                <div className="flex items-center gap-1">
                    {/* [异步流式响应] 导出下拉菜单 */}
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-manus-muted hover:text-accent"
                                title="导出"
                            >
                                <Download className="h-4 w-4" />
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={handleDownloadHtml}>
                                <FileText className="mr-2 h-4 w-4" />
                                下载 HTML
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={handleExportPdf}>
                                <FileText className="mr-2 h-4 w-4" />
                                导出为 PDF
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={handleExportImage}>
                                <ImageIcon className="mr-2 h-4 w-4" />
                                导出为图片
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-manus-muted hover:text-manus-text"
                        onClick={() => setIsMaximized(!isMaximized)}
                    >
                        {isMaximized ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
                    </Button>
                    <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-manus-muted hover:text-error"
                        onClick={onClose}
                    >
                        <X className="h-4 w-4" />
                    </Button>
                </div>
            </div>

            {/* 
                内容区域 - iframe
                
                [安全说明] sandbox 属性设置:
                - allow-scripts: 允许执行 JS（图表库如 ECharts 需要）
                - allow-same-origin: html2canvas 导出功能需要访问 contentDocument
                
                风险控制：HTML 内容由后端 LLM 生成，非用户直接输入的外部 URL。
                如需进一步加固，可在后端对生成的 HTML 进行 CSP 过滤。
            */}
            <div className="flex-1 overflow-hidden">
                <iframe
                    ref={iframeRef}
                    srcDoc={report.content}
                    className="w-full h-full border-0"
                    title={report.title}
                    sandbox="allow-scripts allow-same-origin"
                />
            </div>

            {/* 拖拽调整大小手柄 */}
            {!isMaximized && (
                <div
                    className="absolute top-0 left-0 w-6 h-6 cursor-nw-resize flex items-center justify-center text-manus-muted hover:text-accent transition-colors"
                    onMouseDown={handleMouseDown}
                >
                    <GripHorizontal className="h-4 w-4 rotate-45" />
                </div>
            )}
        </div>
    )
}
