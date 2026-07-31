import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Button } from "@/components/ui/button";
import { FileText, X, Loader2 } from 'lucide-react';
import { FilePreview } from './FilePreview';
import { fetchPreviewFileInfo, type PreviewFileInfo } from '@/services/knowledgePreviewService';

interface FilePreviewSheetProps {
    isOpen: boolean;
    onClose: () => void;
    fileId: string | null;
    fileName: string | null;
    fileType?: string | null;
    initialPage?: number | null;
    initialAnchor?: string | null;
    /** [v2.6] 每次引用点击的唯一标识，用于强制 FilePreview 重新挂载 */
    requestKey?: number;
    sessionId?: string | null;
    panelWidth: number;
    onPanelWidthChange: (width: number) => void;
    minPanelWidth?: number;
    maxPanelWidth?: number;
}

export const FilePreviewSheet: React.FC<FilePreviewSheetProps> = ({
    isOpen,
    onClose,
    fileId,
    fileName,
    fileType: propFileType,
    initialPage,
    initialAnchor,
    requestKey,
    sessionId,
    panelWidth,
    onPanelWidthChange,
    minPanelWidth = 420,
    maxPanelWidth = 720,
}) => {
    const [resolvedInfo, setResolvedInfo] = useState<PreviewFileInfo | null>(null);
    const [resolving, setResolving] = useState(false);
    const isResizingRef = useRef(false);
    const startXRef = useRef(0);
    const startWidthRef = useRef(0);

    const effectiveMinWidth = Math.min(minPanelWidth, maxPanelWidth);
    const clampWidth = useMemo(() => {
        return (width: number) => Math.min(Math.max(width, effectiveMinWidth), maxPanelWidth);
    }, [effectiveMinWidth, maxPanelWidth]);

    const getFileType = (name: string) => {
        if (!name) return 'unknown';
        const parts = name.split('.');
        if (parts.length <= 1) return 'unknown';
        const ext = parts.pop()?.toLowerCase();
        return ext || 'unknown';
    };

    const rawFileType = (propFileType || (fileName ? getFileType(fileName) : 'unknown')).toLowerCase();

    useEffect(() => {
        if (!isOpen || !fileId) {
            setResolvedInfo(null);
            return;
        }

        const needsResolve = rawFileType === 'unknown' || fileName === '来源';
        if (!needsResolve) {
            setResolvedInfo(null);
            return;
        }

        const fetchInfo = async () => {
            setResolving(true);
            try {
                const info = await fetchPreviewFileInfo(fileId);
                setResolvedInfo(info);
            } catch {
                // Keep fallback metadata.
            } finally {
                setResolving(false);
            }
        };

        fetchInfo();
    }, [isOpen, fileId, rawFileType, fileName]);

    useEffect(() => {
        if (!isOpen) return;
        onPanelWidthChange(clampWidth(panelWidth));
    }, [isOpen, panelWidth, clampWidth, onPanelWidthChange]);

    useEffect(() => {
        if (!isOpen) return;

        const handleMouseMove = (e: MouseEvent) => {
            if (!isResizingRef.current) return;
            e.preventDefault();
            const delta = startXRef.current - e.clientX;
            const nextWidth = clampWidth(startWidthRef.current + delta);
            onPanelWidthChange(nextWidth);
        };

        const handleMouseUp = () => {
            isResizingRef.current = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        };

        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('mouseup', handleMouseUp);
        return () => {
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        };
    }, [isOpen, clampWidth, onPanelWidthChange]);

    const handleResizeStart = (e: React.MouseEvent<HTMLDivElement>) => {
        e.preventDefault();
        isResizingRef.current = true;
        startXRef.current = e.clientX;
        startWidthRef.current = panelWidth;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    };

    if (!isOpen) return null;

    const displayName = resolvedInfo?.name || fileName || '文件预览';
    const fileType = (resolvedInfo?.file_type || rawFileType || 'unknown').toLowerCase();

    return (
        <aside
            className="absolute top-0 right-0 bottom-0 z-50 border-l border-manus-border bg-manus-secondary shadow-2xl flex flex-col transition-[width] duration-300 ease-out"
            style={{ width: `${panelWidth}px`, maxWidth: '90vw' }}
        >
            <div
                className="absolute left-0 top-0 h-full w-2 -translate-x-1/2 cursor-col-resize bg-manus-border/40 hover:bg-accent/70 active:bg-accent transition-colors z-10"
                onMouseDown={handleResizeStart}
                title="拖拽调整宽度"
            />

            <div className="flex items-center justify-between px-4 py-3 border-b border-manus-border bg-manus-tertiary flex-shrink-0">
                <div className="flex items-center gap-2 min-w-0">
                    <FileText className="h-5 w-5 text-accent shrink-0" />
                    <span className="text-manus-text text-base font-medium truncate">
                        {displayName}
                    </span>
                    <span className="text-xs text-manus-subtle px-2 py-0.5 bg-manus rounded shrink-0">
                        {fileType.toUpperCase()}
                    </span>
                </div>
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={onClose}
                    className="h-7 w-7 text-manus-subtle hover:text-manus-text"
                >
                    <X className="h-4 w-4" />
                </Button>
            </div>

            <div className="flex-1 overflow-hidden p-4 bg-manus-background">
                {resolving ? (
                    <div className="h-full flex items-center justify-center">
                        <Loader2 className="animate-spin text-accent" />
                    </div>
                ) : fileId && displayName ? (
                    <FilePreview
                        key={`${fileId}-${requestKey ?? 0}`}
                        fileId={fileId}
                        fileName={displayName}
                        fileType={fileType}
                        initialPage={initialPage ?? undefined}
                        initialAnchor={initialAnchor ?? undefined}
                        sessionId={sessionId || undefined}
                        previewFileInfo={resolvedInfo}
                    />
                ) : null}
            </div>
        </aside>
    );
};
