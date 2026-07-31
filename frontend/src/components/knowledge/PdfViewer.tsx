/**
 * PDF 预览组件（单页模式）
 *
 * 使用 react-pdf 实现 PDF 渲染，支持：
 * - 单页显示（只渲染当前页）
 * - 上一页 / 下一页 导航
 * - 页码输入框跳转
 * - 缩放控制
 * - 目标页定位（initialPage）
 */
import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import { Document, Page } from 'react-pdf';
import type { DocumentProps } from 'react-pdf';
import { ChevronLeft, ChevronRight, FileText, Loader2, RotateCw, ZoomIn, ZoomOut } from 'lucide-react';
import { Button } from '@/components/ui/button';

// 导入 pdf.js Worker 和文档资源配置（cMap / 标准字体）
import { pdfDocumentOptions } from '@/config/pdfWorkerConfig';

// 导入 react-pdf 样式（文本层和注释层）
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

interface PdfViewerProps {
    /** PDF 文件 URL（可以是 blob URL 或 http URL） */
    fileUrl: string;
    /** 可选：初始缩放级别，默认 1.0 */
    initialScale?: number;
    /** 可选：认证 token，用于访问受保护的 PDF 文件 */
    authToken?: string;
    /** 可选：初始定位页码（从 1 开始） */
    initialPage?: number;
}

// 缩放级别常量
const DEFAULT_SCALE = 1.0;
const MIN_SCALE = 0.5;
const MAX_SCALE = 2.5;
const SCALE_STEP = 0.25;

export const PdfViewer: React.FC<PdfViewerProps> = ({
    fileUrl,
    initialScale = DEFAULT_SCALE,
    authToken,
    initialPage,
}) => {
    const [numPages, setNumPages] = useState<number>(0);
    const [scale, setScale] = useState<number>(initialScale);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [currentPage, setCurrentPage] = useState<number>(1);
    const [pageInputValue, setPageInputValue] = useState<string>('1');
    const [pageInputFocused, setPageInputFocused] = useState(false);
    const [containerWidth, setContainerWidth] = useState<number>(0);
    const contentRef = useRef<HTMLDivElement>(null);

    // ========== 容器宽度监听 ==========

    useEffect(() => {
        const el = contentRef.current;
        if (!el) return;
        const ro = new ResizeObserver(entries => {
            for (const entry of entries) {
                // 减去 padding (p-4 = 32px 两侧)
                setContainerWidth(Math.floor(entry.contentRect.width));
            }
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    // ========== 文件配置 ==========

    const fileConfig: DocumentProps['file'] = useMemo(() => {
        if (!authToken) {
            return fileUrl;
        }
        return {
            url: fileUrl,
            httpHeaders: {
                Authorization: `Bearer ${authToken}`,
            },
        };
    }, [fileUrl, authToken]);

    const clampPage = useCallback(
        (page: number) => {
            if (numPages <= 0) return 1;
            return Math.min(Math.max(page, 1), numPages);
        },
        [numPages],
    );

    // ========== 页码导航（单页模式：仅切换 currentPage） ==========

    const navigateToPage = useCallback(
        (targetPage: number) => {
            const safePage = clampPage(targetPage);
            setCurrentPage(safePage);
            return safePage;
        },
        [clampPage],
    );

    // ========== 文档加载 ==========

    const onDocumentLoadSuccess = useCallback(
        ({ numPages: total }: { numPages: number }) => {
            setNumPages(total);
            setLoadError(null);

            const requestedPage = typeof initialPage === 'number' && Number.isFinite(initialPage)
                ? initialPage
                : 1;
            const safeInitialPage = Math.min(Math.max(requestedPage || 1, 1), total);
            setCurrentPage(safeInitialPage);
        },
        [initialPage],
    );

    const onDocumentLoadError = useCallback((error: Error) => {
        console.error('PDF 加载失败:', error);
        setLoadError(`PDF 加载失败: ${error.message}`);
    }, []);

    // ========== 缩放 ==========

    const zoomIn = useCallback(() => {
        setScale((prev) => Math.min(MAX_SCALE, prev + SCALE_STEP));
    }, []);

    const zoomOut = useCallback(() => {
        setScale((prev) => Math.max(MIN_SCALE, prev - SCALE_STEP));
    }, []);

    const resetZoom = useCallback(() => {
        setScale(DEFAULT_SCALE);
    }, []);

    // ========== 翻页 ==========

    const goToPrevPage = useCallback(() => {
        navigateToPage(currentPage - 1);
    }, [currentPage, navigateToPage]);

    const goToNextPage = useCallback(() => {
        navigateToPage(currentPage + 1);
    }, [currentPage, navigateToPage]);

    // ========== 页码输入处理 ==========

    /** 输入框位数自适应宽度 (ch) */
    const pageInputWidth = useMemo(() => {
        const digits = Math.max(1, String(numPages || 1).length)
        return `${digits + 1.5}ch`
    }, [numPages])

    /** 同步 currentPage → 输入框（非聚焦状态下） */
    useEffect(() => {
        if (!pageInputFocused) {
            setPageInputValue(String(Math.min(currentPage, Math.max(1, numPages))))
        }
    }, [currentPage, numPages, pageInputFocused])

    /** 确认跳转 */
    const commitPageInput = useCallback(() => {
        const parsed = parseInt(pageInputValue, 10)
        if (!isNaN(parsed) && numPages > 0) {
            const clamped = Math.max(1, Math.min(numPages, parsed))
            navigateToPage(clamped)
            setPageInputValue(String(clamped))
        } else {
            // 输入无效 → 回退到当前页
            setPageInputValue(String(Math.min(currentPage, Math.max(1, numPages))))
        }
        setPageInputFocused(false)
    }, [pageInputValue, numPages, currentPage, navigateToPage])

    /** Enter 确认，Esc 回退 */
    const handlePageInputKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
        e.stopPropagation()  // 防止触发 PdfViewer 全局键盘事件
        if (e.key === 'Enter') {
            commitPageInput()
                ; (e.target as HTMLInputElement).blur()
        } else if (e.key === 'Escape') {
            setPageInputValue(String(Math.min(currentPage, Math.max(1, numPages))))
            setPageInputFocused(false)
                ; (e.target as HTMLInputElement).blur()
        }
    }, [commitPageInput, currentPage, numPages])

    /** 禁用滚轮误触 */
    const handlePageInputWheel = useCallback((e: React.WheelEvent) => {
        e.preventDefault()
            ; (e.target as HTMLInputElement).blur()
    }, [])

    // ========== 键盘事件 ==========

    const handleKeyDown = useCallback(
        (e: React.KeyboardEvent) => {
            if (e.key === '+' || e.key === '=') {
                zoomIn();
                return;
            }
            if (e.key === '-') {
                zoomOut();
                return;
            }
            if (e.key === '0') {
                resetZoom();
                return;
            }
            if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
                e.preventDefault();
                goToPrevPage();
                return;
            }
            if (e.key === 'ArrowRight' || e.key === 'PageDown') {
                e.preventDefault();
                goToNextPage();
            }
        },
        [goToNextPage, goToPrevPage, resetZoom, zoomIn, zoomOut],
    );

    // ========== 文件变化时重置 ==========

    useEffect(() => {
        setNumPages(0);
        setCurrentPage(1);
        setLoadError(null);
    }, [fileUrl]);

    // ========== initialPage 外部变化时跳转 ==========

    useEffect(() => {
        if (typeof initialPage !== 'number' || !Number.isFinite(initialPage) || initialPage <= 0) {
            return;
        }
        if (numPages <= 0) {
            return;
        }
        const safePage = clampPage(initialPage);
        setCurrentPage(safePage);
    }, [initialPage, numPages, clampPage]);

    // ========== 渲染 ==========

    if (loadError) {
        return (
            <div className="h-full flex flex-col items-center justify-center text-manus-subtle p-8">
                <div className="text-amber-500 mb-3">⚠️</div>
                <p className="text-manus-text font-medium mb-1">PDF 加载失败</p>
                <p className="text-sm text-center">{loadError}</p>
            </div>
        );
    }

    return (
        <div
            className="h-full flex flex-col bg-manus-background"
            onKeyDown={handleKeyDown}
            tabIndex={0}
        >
            {/* 工具栏 */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-manus-border bg-manus-tertiary flex-shrink-0">
                <div className="flex items-center gap-2 text-sm text-manus-subtle">
                    <FileText size={14} />
                    <span className="flex items-center gap-1">
                        第
                        <input
                            type="text"
                            inputMode="numeric"
                            value={pageInputFocused ? pageInputValue : String(Math.min(currentPage, Math.max(1, numPages)))}
                            onChange={(e) => setPageInputValue(e.target.value.replace(/[^0-9]/g, ''))}
                            onFocus={() => {
                                setPageInputFocused(true)
                                setPageInputValue(String(Math.min(currentPage, Math.max(1, numPages))))
                            }}
                            onBlur={commitPageInput}
                            onKeyDown={handlePageInputKeyDown}
                            onWheel={handlePageInputWheel}
                            disabled={numPages <= 0}
                            style={{ width: pageInputWidth }}
                            className="text-center bg-manus-background border border-manus-border rounded px-1 py-0 text-sm text-manus-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition-colors"
                            aria-label="页码"
                        />
                        / {numPages || 1} 页
                    </span>
                </div>

                <div className="flex items-center gap-1">
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={goToPrevPage}
                        disabled={currentPage <= 1 || numPages <= 0}
                        className="h-7 w-7 p-0"
                        title="上一页"
                    >
                        <ChevronLeft size={14} />
                    </Button>

                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={goToNextPage}
                        disabled={currentPage >= numPages || numPages <= 0}
                        className="h-7 w-7 p-0"
                        title="下一页"
                    >
                        <ChevronRight size={14} />
                    </Button>

                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={zoomOut}
                        disabled={scale <= MIN_SCALE}
                        className="h-7 w-7 p-0"
                        title="缩小 (-)"
                    >
                        <ZoomOut size={14} />
                    </Button>

                    <span className="text-xs text-manus-subtle min-w-[45px] text-center">
                        {Math.round(scale * 100)}%
                    </span>

                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={zoomIn}
                        disabled={scale >= MAX_SCALE}
                        className="h-7 w-7 p-0"
                        title="放大 (+)"
                    >
                        <ZoomIn size={14} />
                    </Button>

                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={resetZoom}
                        className="h-7 w-7 p-0"
                        title="重置缩放 (0)"
                    >
                        <RotateCw size={14} />
                    </Button>
                </div>
            </div>

            {/* 单页渲染区 */}
            <div ref={contentRef} className="flex-1 overflow-auto flex items-start justify-center p-4">
                <Document
                    file={fileConfig}
                    options={pdfDocumentOptions}
                    onLoadSuccess={onDocumentLoadSuccess}
                    onLoadError={onDocumentLoadError}
                    loading={
                        <div className="flex items-center justify-center py-8">
                            <Loader2 className="h-6 w-6 animate-spin text-accent" />
                        </div>
                    }
                    className="pdf-document flex flex-col items-center"
                >
                    {numPages > 0 && containerWidth > 0 && (
                        <Page
                            key={`page_${currentPage}`}
                            pageNumber={currentPage}
                            width={containerWidth * scale}
                            loading={
                                <div className="flex items-center justify-center py-4 w-full h-[800px]">
                                    <Loader2 className="h-5 w-5 animate-spin text-manus-subtle" />
                                </div>
                            }
                            className="pdf-page shadow-lg rounded-sm bg-white"
                            renderTextLayer={true}
                            renderAnnotationLayer={true}
                        />
                    )}
                </Document>
            </div>
        </div>
    );
};

export default PdfViewer;
