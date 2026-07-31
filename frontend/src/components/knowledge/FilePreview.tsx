import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, AlertCircle, FileText, Download, Pencil, Save, X, ChevronLeft, ChevronRight } from 'lucide-react';
import { API_BASE_URL } from '@/config';
import { getAuthHeader, useAuthStore } from '@/stores/authStore';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { PdfViewer } from './PdfViewer';
import { renderAsync } from 'docx-preview';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CodeBlock } from '@/components/markdown/CodeBlock';
import { Hr, Table, Thead, Th, Td, H1, H2, H3, P, Ul, Ol, Li, Blockquote, A } from '@/components/markdown/MarkdownElements';
import { collectDocxPageElements, extractDocxSourcePageMap, findDocxPageBySnippet } from './docxPageMapper';
import {
    fetchFileAccessUrl,
    fetchPreviewFileInfo,
    type PreviewFileInfo,
    recoverDocxPreviewBuffer,
    resolveDocxPreviewBuffer,
    resolvePdfPreviewAccess,
} from '@/services/knowledgePreviewService';

interface FilePreviewProps {
    fileId: string;
    fileName: string;
    fileType: string;
    initialPage?: number;
    initialAnchor?: string;
    sessionId?: string;
    onRefresh?: () => void;
    previewFileInfo?: PreviewFileInfo | null;
}

export const FilePreview: React.FC<FilePreviewProps> = ({ fileId, fileName, fileType, initialPage, sessionId, onRefresh, previewFileInfo }) => {
    const [content, setContent] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [rawAccessUrl, setRawAccessUrl] = useState<string | null>(null);
    const [rawAccessRequiresAuth, setRawAccessRequiresAuth] = useState(true);
    const userId = useAuthStore((state) => state.user?.id ?? null);
    const authToken = useAuthStore((state) => state.token ?? null);

    // DOCX 预览容器
    const docxContainerRef = useRef<HTMLDivElement>(null);
    const contentScrollRef = useRef<HTMLDivElement>(null);
    const docxPagesRef = useRef<HTMLElement[]>([]);
    const docxScrollRafRef = useRef<number | null>(null);
    const docxSourceToRenderedMapRef = useRef<Map<number, number>>(new Map());
    const [docxLoading, setDocxLoading] = useState(false);
    const [docxCurrentPage, setDocxCurrentPage] = useState(1); // 显示给用户的源页码
    const [docxCurrentRenderedPage, setDocxCurrentRenderedPage] = useState(1); // docx-preview 实际渲染页
    const [docxPageCount, setDocxPageCount] = useState(0); // docx-preview 实际渲染页总数
    const [docxSourcePageCount, setDocxSourcePageCount] = useState<number | null>(null);
    const docxSourceSnippetsRef = useRef<Map<number, string>>(new Map());

    // 编辑模式状态
    const [isEditing, setIsEditing] = useState(false);
    const [editContent, setEditContent] = useState('');
    const [isSaving, setIsSaving] = useState(false);
    const [saveMessage, setSaveMessage] = useState<string | null>(null);

    const normalizedFileType = (fileType || '').toLowerCase();
    const isPdfFile = normalizedFileType === 'pdf';
    const isWordFile = normalizedFileType === 'docx' || normalizedFileType === 'doc';
    const previewInfoUpdatedAt = previewFileInfo?.updated_at ?? null;
    const previewInfoType = previewFileInfo?.file_type ?? null;
    const previewInfoSize = previewFileInfo?.file_size ?? 0;
    const stablePreviewFileInfo = useMemo(() => {
        if (!previewFileInfo) {
            return null;
        }
        return {
            updated_at: previewInfoUpdatedAt,
            file_type: previewInfoType || normalizedFileType || 'unknown',
            file_size: previewInfoSize,
        };
    }, [normalizedFileType, previewInfoSize, previewInfoType, previewInfoUpdatedAt, !!previewFileInfo]);
    const pdfAuthToken = useMemo(() => {
        if (!rawAccessRequiresAuth || !authToken) {
            return undefined;
        }
        return authToken;
    }, [authToken, rawAccessRequiresAuth]);

    // 判断是否为可编辑文件类型（只有 txt/md 可编辑，Word/PDF 只读）
    const isEditable = normalizedFileType === 'txt' || normalizedFileType === 'md';

    const getDocxDisplayPageCount = () => {
        if (docxSourcePageCount && docxSourcePageCount > 0) {
            return docxSourcePageCount;
        }
        return Math.max(1, docxPageCount);
    };

    const clampDocxSourcePage = (page: number) => {
        const total = getDocxDisplayPageCount();
        return Math.min(Math.max(page, 1), total);
    };

    const resolveRenderedPageBySource = (sourcePage: number): number => {
        const renderedTotal = Math.max(1, docxPageCount);
        const sourceTotal = Math.max(1, getDocxDisplayPageCount());
        const explicit = docxSourceToRenderedMapRef.current.get(sourcePage);
        if (typeof explicit === 'number' && explicit > 0) {
            return Math.min(Math.max(explicit, 1), renderedTotal);
        }
        const approx = Math.round((sourcePage / sourceTotal) * renderedTotal);
        return Math.min(Math.max(approx, 1), renderedTotal);
    };

    const resolveSourcePageByRendered = (renderedPage: number): number => {
        const sourceTotal = Math.max(1, getDocxDisplayPageCount());
        const mapping = docxSourceToRenderedMapRef.current;
        if (!mapping || mapping.size === 0) {
            return Math.min(Math.max(renderedPage, 1), sourceTotal);
        }

        let bestSource = 1;
        let bestDistance = Number.POSITIVE_INFINITY;
        for (const [sourcePage, mappedRendered] of mapping.entries()) {
            const distance = Math.abs(mappedRendered - renderedPage);
            if (distance < bestDistance) {
                bestDistance = distance;
                bestSource = sourcePage;
            }
        }
        return Math.min(Math.max(bestSource, 1), sourceTotal);
    };

    const scrollToDocxSourcePage = (targetSourcePage: number, behavior: ScrollBehavior = 'smooth') => {
        const safeSourcePage = clampDocxSourcePage(targetSourcePage);
        const safeRenderedPage = resolveRenderedPageBySource(safeSourcePage);
        const target = docxPagesRef.current[safeRenderedPage - 1];
        if (!target) return;
        setDocxCurrentPage(safeSourcePage);
        setDocxCurrentRenderedPage(safeRenderedPage);
        target.scrollIntoView({ behavior, block: 'start' });
    };

    const renderDocxPreview = async (arrayBuffer: ArrayBuffer) => {
        if (!docxContainerRef.current) {
            return;
        }

        const sourcePageMap = await extractDocxSourcePageMap(arrayBuffer);
        docxSourceSnippetsRef.current = sourcePageMap.snippets;

        docxContainerRef.current.innerHTML = '';
        await renderAsync(arrayBuffer, docxContainerRef.current, undefined, {
            inWrapper: true,
            ignoreWidth: false,
            ignoreHeight: false,
            ignoreFonts: false,
            breakPages: true,
            // Word 常见分页锚点，打开后才能正确识别大多数文档的页级定位
            ignoreLastRenderedPageBreak: false,
            experimental: false,
            trimXmlDeclaration: true,
            useBase64URL: true
        });

        const pages = collectDocxPageElements(docxContainerRef.current);
        docxPagesRef.current = pages;
        setDocxPageCount(pages.length);

        if (pages.length <= 0) {
            return;
        }

        const sourceTotal = Math.max(1, sourcePageMap.pageCount);
        setDocxSourcePageCount(sourceTotal);

        const sourceToRendered = new Map<number, number>();
        for (let sourcePage = 1; sourcePage <= sourceTotal; sourcePage += 1) {
            const sourceSnippet = docxSourceSnippetsRef.current.get(sourcePage) || '';
            const mappedBySnippet = sourceSnippet
                ? findDocxPageBySnippet(pages, sourceSnippet)
                : null;
            const mappedByRatio = Math.round((sourcePage / sourceTotal) * pages.length);
            const safeRendered = Math.min(
                Math.max(mappedBySnippet ?? mappedByRatio, 1),
                pages.length,
            );
            sourceToRendered.set(sourcePage, safeRendered);
        }
        docxSourceToRenderedMapRef.current = sourceToRendered;

        const hasRequestedPage = typeof initialPage === 'number' && initialPage > 0;
        const requestedSource = hasRequestedPage ? Number(initialPage) : 1;
        const safeSource = Math.min(
            Math.max(requestedSource, 1),
            sourceTotal,
        );
        const safeRendered = sourceToRendered.get(safeSource) || 1;

        setDocxCurrentPage(safeSource);
        setDocxCurrentRenderedPage(safeRendered);

        // 等 DOM 完整排版后再滚动，避免首次定位丢失
        requestAnimationFrame(() => {
            pages[safeRendered - 1]?.scrollIntoView({ behavior: 'auto', block: 'start' });
        });
    };

    // 加载 Word 原生预览（docx/doc）
    useEffect(() => {
        if (!fileId || !isWordFile) return;

        let cancelled = false;
        const loadWord = async () => {
            setDocxLoading(true);
            setError(null);
            setDocxCurrentPage(1);
            setDocxCurrentRenderedPage(1);
            setDocxPageCount(0);
            setDocxSourcePageCount(null);
            docxPagesRef.current = [];
            docxSourceToRenderedMapRef.current = new Map();
            try {
                const fileInfo = stablePreviewFileInfo ?? await fetchPreviewFileInfo(fileId);
                if (cancelled) return;

                let previewBuffer = await resolveDocxPreviewBuffer({
                    userId,
                    fileId,
                    fileInfo,
                });
                if (cancelled) return;
                try {
                    await renderDocxPreview(previewBuffer.arrayBuffer);
                } catch (renderError) {
                    if (!previewBuffer.fromCache || cancelled) {
                        throw renderError;
                    }
                    previewBuffer = await recoverDocxPreviewBuffer({
                        userId,
                        fileId,
                        fileInfo,
                    });
                    if (cancelled) return;
                    await renderDocxPreview(previewBuffer.arrayBuffer);
                }
            } catch (err: any) {
                if (cancelled) return;
                const message = err?.message || 'Word 文件预览失败';
                if (normalizedFileType === 'doc') {
                    setError(`${message}（DOC 兼容性较差，建议转为 DOCX）`);
                } else {
                    setError(message);
                }
            } finally {
                if (!cancelled) {
                    setDocxLoading(false);
                }
            }
        };

        loadWord();
        return () => {
            cancelled = true;
        };
    }, [fileId, isWordFile, normalizedFileType, previewInfoSize, previewInfoType, previewInfoUpdatedAt, stablePreviewFileInfo, userId]);

    useEffect(() => {
        if (!fileId || !isPdfFile) {
            setRawAccessUrl(null);
            setRawAccessRequiresAuth(true);
            return;
        }

        let cancelled = false;
        const loadPdfAccessUrl = async () => {
            try {
                setError(null);
                setRawAccessUrl(null);
                const fileInfo = stablePreviewFileInfo ?? await fetchPreviewFileInfo(fileId);
                if (cancelled) return;
                const access = await resolvePdfPreviewAccess({
                    userId,
                    fileId,
                    fileInfo,
                });
                if (cancelled) return;
                setRawAccessUrl(access.url);
                setRawAccessRequiresAuth(!!access.requires_auth);
            } catch (err: any) {
                if (cancelled) return;
                setError(err?.message || '无法获取 PDF 访问地址');
                setRawAccessUrl(null);
            }
        };

        loadPdfAccessUrl();
        return () => {
            cancelled = true;
        };
    }, [fileId, isPdfFile, previewInfoUpdatedAt, stablePreviewFileInfo, userId]);

    useEffect(() => {
        if (!isWordFile) return;
        if (typeof initialPage !== 'number' || initialPage <= 0) return;
        if (docxPagesRef.current.length <= 0) return;
        scrollToDocxSourcePage(initialPage, 'auto');
    }, [isWordFile, initialPage, docxSourcePageCount, docxPageCount, fileId]);

    useEffect(() => {
        if (!isWordFile) return;

        const container = contentScrollRef.current;
        if (!container) return;

        const onScroll = () => {
            if (docxScrollRafRef.current !== null) return;
            docxScrollRafRef.current = window.requestAnimationFrame(() => {
                docxScrollRafRef.current = null;

                const pages = docxPagesRef.current;
                if (pages.length <= 0) return;

                const scrollTop = container.scrollTop;
                let bestPage = docxCurrentPage;
                let bestDistance = Number.POSITIVE_INFINITY;

                for (let idx = 0; idx < pages.length; idx += 1) {
                    const pageEl = pages[idx];
                    const distance = Math.abs(pageEl.offsetTop - scrollTop);
                    if (distance < bestDistance) {
                        bestDistance = distance;
                        bestPage = idx + 1;
                    }
                }

                if (bestPage !== docxCurrentRenderedPage) {
                    setDocxCurrentRenderedPage(bestPage);
                }
                const sourcePage = resolveSourcePageByRendered(bestPage);
                if (sourcePage !== docxCurrentPage) {
                    setDocxCurrentPage(sourcePage);
                }
            });
        };

        container.addEventListener('scroll', onScroll, { passive: true });
        return () => {
            container.removeEventListener('scroll', onScroll);
            if (docxScrollRafRef.current !== null) {
                window.cancelAnimationFrame(docxScrollRafRef.current);
                docxScrollRafRef.current = null;
            }
        };
    }, [isWordFile, docxCurrentPage, docxCurrentRenderedPage, docxSourcePageCount, fileId]);

    useEffect(() => {
        if (isWordFile) return;
        docxPagesRef.current = [];
        setDocxCurrentPage(1);
        setDocxCurrentRenderedPage(1);
        setDocxPageCount(0);
        setDocxSourcePageCount(null);
        docxSourceToRenderedMapRef.current = new Map();
    }, [isWordFile, fileId]);

    // 加载文本内容（txt/md）
    useEffect(() => {
        if (!fileId) return;
        if (isPdfFile || isWordFile) {
            setLoading(false);
            setContent(normalizedFileType.toUpperCase());
            return;
        }

        const loadContent = async () => {
            setLoading(true);
            setError(null);
            setIsEditing(false);
            try {
                let res = await fetch(`${API_BASE_URL}/knowledge/files/${fileId}/content`, {
                    headers: getAuthHeader()
                });

                if (res.status === 404 && sessionId && fileName) {
                    res = await fetch(`${API_BASE_URL}/files/download/${sessionId}/${encodeURIComponent(fileName)}`, {
                        headers: getAuthHeader()
                    });
                }

                if (!res.ok) {
                    // 透传后端 detail（如 "DOCX 文件请使用 /raw 端点"、"文件不存在" 等）
                    const data = await res.json().catch(() => ({} as any));
                    throw new Error(data?.detail || `无法加载文件内容 (HTTP ${res.status})`);
                }

                const text = await res.text();
                setContent(text);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };

        loadContent();
    }, [fileId, isPdfFile, isWordFile, normalizedFileType, sessionId, fileName]);

    // 下载文件（使用 fetch 携带认证 token）
    const handleDownload = async () => {
        try {
            const access = await fetchFileAccessUrl(fileId, 'download', false);
            if (!access.requires_auth) {
                const a = document.createElement('a');
                a.href = access.url;
                a.target = '_blank';
                a.rel = 'noopener noreferrer';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                return;
            }

            const res = await fetch(access.url, {
                headers: getAuthHeader()
            });

            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || '下载失败');
            }

            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            // 确保文件名包含扩展名
            let downloadName = fileName;
            if (normalizedFileType && !fileName.toLowerCase().endsWith(`.${normalizedFileType}`)) {
                downloadName = `${fileName}.${normalizedFileType}`;
            }
            a.download = downloadName;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        } catch (err: any) {
            console.error('下载失败:', err);
            alert(`下载失败: ${err.message}`);
        }
    };

    // 进入编辑模式
    const handleStartEdit = () => {
        setEditContent(content || '');
        setIsEditing(true);
        setSaveMessage(null);
    };

    // 取消编辑
    const handleCancelEdit = () => {
        setIsEditing(false);
        setEditContent('');
        setSaveMessage(null);
    };

    // 保存编辑
    const handleSaveEdit = async () => {
        setIsSaving(true);
        setSaveMessage(null);
        try {
            const res = await fetch(`${API_BASE_URL}/knowledge/files/${fileId}/edit`, {
                method: 'PUT',
                headers: {
                    ...getAuthHeader(),
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ content: editContent })
            });

            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || '保存失败');
            }

            const data = await res.json();
            setSaveMessage(data.message || '保存成功');
            setContent(editContent);
            setIsEditing(false);
            onRefresh?.();
        } catch (err: any) {
            setSaveMessage(`错误: ${err.message}`);
        } finally {
            setIsSaving(false);
        }
    };

    const handleDocxPrevPage = () => {
        scrollToDocxSourcePage(docxCurrentPage - 1);
    };

    const handleDocxNextPage = () => {
        scrollToDocxSourcePage(docxCurrentPage + 1);
    };

    if (!fileId) {
        return (
            <div className="h-full flex flex-col items-center justify-center text-manus-subtle p-8 bg-manus-secondary/20 rounded-lg">
                <FileText className="h-12 w-12 mb-4 opacity-50" />
                <p>选择文件进行预览</p>
            </div>
        );
    }

    return (
        <div className="h-full flex flex-col border border-manus-border rounded-lg bg-manus-secondary overflow-hidden">
            {/* 标题栏 + 操作按钮 */}
            <div className="h-12 px-4 flex items-center justify-between border-b border-manus-border bg-manus-tertiary">
                <span className="font-medium text-sm truncate flex-1">{fileName}</span>

                <div className="flex items-center gap-2">
                    {/* DOCX 页码导航 */}
                    {isWordFile && docxPageCount > 0 && (
                        <div className="flex items-center gap-1 px-2 py-1 rounded-md border border-manus-border bg-manus-background/70">
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={handleDocxPrevPage}
                                disabled={docxCurrentPage <= 1}
                                className="h-6 w-6 p-0"
                                title="上一页"
                            >
                                <ChevronLeft size={14} />
                            </Button>
                            <span className="text-xs text-manus-subtle min-w-[70px] text-center">
                                第 {docxCurrentPage} / {getDocxDisplayPageCount()} 页
                            </span>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={handleDocxNextPage}
                                disabled={docxCurrentPage >= getDocxDisplayPageCount()}
                                className="h-6 w-6 p-0"
                                title="下一页"
                            >
                                <ChevronRight size={14} />
                            </Button>
                        </div>
                    )}

                    {/* 保存状态提示 */}
                    {saveMessage && (
                        <span className={`text-xs ${saveMessage.startsWith('错误') ? 'text-red-400' : 'text-green-400'}`}>
                            {saveMessage}
                        </span>
                    )}

                    {/* 下载按钮 */}
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={handleDownload}
                        className="h-8 px-2 text-manus-subtle hover:text-manus-text"
                        title="下载文件"
                    >
                        <Download size={16} />
                    </Button>

                    {/* 编辑按钮（仅 txt/md 可用） */}
                    {isEditable && !isEditing && (
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={handleStartEdit}
                            className="h-8 px-2 text-manus-subtle hover:text-manus-text"
                            title="编辑文件"
                            disabled={loading || !!error}
                        >
                            <Pencil size={16} />
                        </Button>
                    )}

                    {/* 编辑模式：保存/取消 */}
                    {isEditing && (
                        <>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={handleSaveEdit}
                                disabled={isSaving}
                                className="h-8 px-2 text-green-400 hover:text-green-300"
                                title="保存"
                            >
                                {isSaving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                            </Button>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={handleCancelEdit}
                                disabled={isSaving}
                                className="h-8 px-2 text-red-400 hover:text-red-300"
                                title="取消"
                            >
                                <X size={16} />
                            </Button>
                        </>
                    )}
                </div>
            </div>

            {/* 内容区域 */}
            <div ref={contentScrollRef} className="flex-1 overflow-auto p-0 relative">
                {(loading || docxLoading) && (
                    <div className="absolute inset-0 flex items-center justify-center bg-manus-secondary/50 backdrop-blur-sm z-10">
                        <Loader2 className="animate-spin text-accent" />
                    </div>
                )}

                {error ? (
                    <div className="h-full flex flex-col items-center justify-center text-manus-subtle p-8">
                        <AlertCircle className="h-10 w-10 mb-3 text-amber-500" />
                        <p className="text-manus-text font-medium mb-1">文件不可用</p>
                        <p className="text-sm text-center mb-3 break-words max-w-md whitespace-pre-wrap">
                            {error?.trim() || '该引用可能是 AI 生成的，实际文件不存在'}
                        </p>
                        <div className="text-xs bg-manus-tertiary px-3 py-1.5 rounded-md border border-manus-border">
                            ID: {fileId}
                        </div>
                    </div>
                ) : isEditing ? (
                    // 编辑模式（仅 txt/md）
                    <Textarea
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        className="w-full h-full min-h-full resize-none border-none rounded-none bg-manus-primary text-manus-text font-mono text-xs p-4 focus:ring-0 focus:outline-none"
                        placeholder="编辑内容..."
                        disabled={isSaving}
                    />
                ) : (
                    // 预览模式
                    <div className="h-full w-full">
                        {isPdfFile ? (
                            rawAccessUrl ? (
                                <PdfViewer
                                    fileUrl={rawAccessUrl}
                                    authToken={pdfAuthToken}
                                    initialPage={initialPage}
                                />
                            ) : (
                                <div className="h-full flex items-center justify-center">
                                    <Loader2 className="animate-spin text-accent" />
                                </div>
                            )
                        ) : isWordFile ? (
                            // Word 原生预览（docx/doc）
                            <div
                                ref={docxContainerRef}
                                className="docx-preview-container p-4 bg-white min-h-full"
                            />
                        ) : normalizedFileType === 'md' ? (
                            // Markdown 渲染预览
                            <div className="p-6 overflow-auto h-full bg-manus-primary">
                                <ReactMarkdown
                                    remarkPlugins={[remarkGfm]}
                                    components={{
                                        code: CodeBlock,
                                        hr: Hr,
                                        table: Table,
                                        thead: Thead,
                                        th: Th,
                                        td: Td,
                                        h1: H1,
                                        h2: H2,
                                        h3: H3,
                                        p: P,
                                        ul: Ul,
                                        ol: Ol,
                                        li: Li,
                                        blockquote: Blockquote,
                                        a: A
                                    }}
                                >
                                    {content || ''}
                                </ReactMarkdown>
                            </div>
                        ) : (
                            // 纯文本预览 (txt)
                            <pre className="p-4 text-xs font-mono whitespace-pre-wrap overflow-auto h-full text-manus-text">
                                {content || '正在加载...'}
                            </pre>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};
