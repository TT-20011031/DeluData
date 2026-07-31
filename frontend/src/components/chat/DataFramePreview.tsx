import React, { useState, useEffect } from 'react';
import {
    Dialog,
    DialogContent,
    DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Loader2, Download, Table2, AlertCircle, Maximize2, Minimize2, X } from 'lucide-react';
import { API_BASE_URL } from '@/config';
import { getAuthHeader } from '@/stores/authStore';

interface DataFramePreviewProps {
    isOpen: boolean;
    onClose: () => void;
    sessionId: string;
    dfKey: string;
}

interface DataFrameData {
    key: string;
    columns: string[];
    data: Record<string, any>[];
    total_rows: number;
    displayed_rows: number;
}

export const DataFramePreview: React.FC<DataFramePreviewProps> = ({
    isOpen,
    onClose,
    sessionId,
    dfKey
}) => {
    const [data, setData] = useState<DataFrameData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [isMaximized, setIsMaximized] = useState(false);

    useEffect(() => {
        if (!isOpen || !sessionId || !dfKey) return;

        const loadData = async () => {
            setLoading(true);
            setError(null);
            try {
                const res = await fetch(
                    `${API_BASE_URL}/chat/dataframe/${sessionId}/${dfKey}`,
                    { headers: getAuthHeader() }
                );
                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.detail || '加载数据失败');
                }
                const result = await res.json();
                setData(result);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };

        loadData();
    }, [isOpen, sessionId, dfKey]);

    const handleExport = (format: 'csv' | 'excel') => {
        const url = `${API_BASE_URL}/chat/dataframe/${sessionId}/${dfKey}/export?format=${format}`;
        const link = document.createElement('a');
        link.href = url;
        link.download = `${dfKey}.${format === 'excel' ? 'xlsx' : 'csv'}`;
        fetch(url, { headers: getAuthHeader() })
            .then(res => res.blob())
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                link.href = url;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                window.URL.revokeObjectURL(url);
            });
    };

    // 根据最大化状态计算尺寸
    const dialogSize = isMaximized
        ? "w-[95vw] h-[90vh] max-w-none"
        : "w-[800px] h-[600px] max-w-[90vw] max-h-[80vh]";

    return (
        <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
            <DialogContent
                className={`${dialogSize} p-0 flex flex-col bg-manus-secondary border border-manus-border rounded-lg shadow-2xl transition-all duration-200 overflow-hidden`}
                hideCloseButton
            >
                {/* 自定义标题栏 */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-manus-border bg-manus-tertiary flex-shrink-0">
                    <div className="flex items-center gap-2">
                        <Table2 className="h-5 w-5 text-accent" />
                        <DialogTitle className="text-manus-text text-base font-medium">
                            查询结果: {dfKey}
                        </DialogTitle>
                        {data && (
                            <span className="text-xs text-manus-subtle ml-2">
                                显示 {data.displayed_rows} / {data.total_rows} 条记录
                            </span>
                        )}
                    </div>
                    <div className="flex items-center gap-2">
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleExport('csv')}
                            className="text-xs h-7"
                        >
                            <Download className="h-3 w-3 mr-1" />
                            CSV
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleExport('excel')}
                            className="text-xs h-7"
                        >
                            <Download className="h-3 w-3 mr-1" />
                            Excel
                        </Button>
                        <div className="w-px h-4 bg-manus-border mx-1" />
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setIsMaximized(!isMaximized)}
                            className="h-7 w-7 text-manus-subtle hover:text-manus-text"
                            title={isMaximized ? "还原" : "最大化"}
                        >
                            {isMaximized ? (
                                <Minimize2 className="h-4 w-4" />
                            ) : (
                                <Maximize2 className="h-4 w-4" />
                            )}
                        </Button>
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={onClose}
                            className="h-7 w-7 text-manus-subtle hover:text-manus-text"
                        >
                            <X className="h-4 w-4" />
                        </Button>
                    </div>
                </div>

                {/* 表格内容 */}
                <div className="flex-1 overflow-hidden bg-manus-background">
                    {loading ? (
                        <div className="h-full flex items-center justify-center">
                            <Loader2 className="h-8 w-8 animate-spin text-accent" />
                        </div>
                    ) : error ? (
                        <div className="h-full flex flex-col items-center justify-center text-manus-subtle p-8">
                            <AlertCircle className="h-10 w-10 mb-3 text-amber-500" />
                            <p className="text-manus-text font-medium mb-1">加载失败</p>
                            <p className="text-sm text-center">{error}</p>
                        </div>
                    ) : data ? (
                        <div className="h-full overflow-auto">
                            {/* Navicat 风格表格 */}
                            <table className="w-full border-collapse text-sm">
                                {/* 固定表头 */}
                                <thead className="sticky top-0 z-10">
                                    <tr className="bg-manus-tertiary border-b border-manus-border">
                                        {data.columns.map((col) => (
                                            <th
                                                key={col}
                                                className="px-3 py-2 text-left font-medium text-manus-text border-r border-manus-border last:border-r-0 whitespace-nowrap"
                                            >
                                                {col}
                                            </th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.data.map((row, rowIdx) => (
                                        <tr
                                            key={rowIdx}
                                            className={`
                                                border-b border-manus-border/50
                                                ${rowIdx % 2 === 0 ? 'bg-manus-secondary' : 'bg-manus-background'}
                                                hover:bg-accent/5 transition-colors
                                            `}
                                        >
                                            {data.columns.map((col) => (
                                                <td
                                                    key={col}
                                                    className="px-3 py-2 text-manus-text border-r border-manus-border/30 last:border-r-0 whitespace-nowrap"
                                                >
                                                    {row[col] !== null && row[col] !== undefined
                                                        ? String(row[col])
                                                        : <span className="text-manus-subtle italic">NULL</span>
                                                    }
                                                </td>
                                            ))}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : null}
                </div>
            </DialogContent>
        </Dialog>
    );
};
