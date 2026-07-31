import { ChevronLeft, ChevronRight, Eye, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'

import type { TableData } from '../types'

interface DatabaseDataTabProps {
    selectedTable: string | null
    tableData: TableData | null
    isLoadingData: boolean
    dataPage: number
    onFetchPage: (tableName: string, page: number) => void
}

export function DatabaseDataTab({
    selectedTable,
    tableData,
    isLoadingData,
    dataPage,
    onFetchPage,
}: DatabaseDataTabProps) {
    if (!selectedTable || !tableData) {
        return (
            <div className="flex-1 flex flex-col overflow-hidden p-4">
                <div className="flex-1 flex items-center justify-center text-manus-subtle">
                    <div className="text-center">
                        <Eye className="h-12 w-12 mx-auto mb-3 opacity-50" />
                        <p>从左侧选择一张表查看数据</p>
                    </div>
                </div>
            </div>
        )
    }

    return (
        <div className="flex-1 flex flex-col overflow-hidden p-4">
            <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                    <h3 className="text-lg font-medium text-manus-text">{selectedTable}</h3>
                    <span className="text-sm text-manus-muted">
                        共 {tableData.totalCount.toLocaleString()} 行
                    </span>
                    {isLoadingData && <Loader2 className="h-4 w-4 animate-spin text-accent" />}
                </div>
                <div className="flex items-center gap-2">
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={dataPage <= 1 || isLoadingData}
                        onClick={() => onFetchPage(selectedTable, dataPage - 1)}
                        className="bg-manus-tertiary border-manus-border"
                    >
                        <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <span className="text-sm text-manus-muted px-2">第 {dataPage} 页</span>
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={!tableData.hasMore || isLoadingData}
                        onClick={() => onFetchPage(selectedTable, dataPage + 1)}
                        className="bg-manus-tertiary border-manus-border"
                    >
                        <ChevronRight className="h-4 w-4" />
                    </Button>
                </div>
            </div>

            <div className="flex-1 bg-manus-secondary/50 border border-manus-border rounded-lg overflow-auto">
                <table className="min-w-max w-full text-sm">
                    <thead className="sticky top-0 bg-manus-tertiary border-b border-manus-border z-10">
                        <tr>
                            {tableData.columns.map((col, i) => (
                                <th
                                    key={i}
                                    className="px-4 py-3 text-left font-mono font-medium text-manus-text whitespace-nowrap"
                                >
                                    {col}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {tableData.rows.map((row, rowIndex) => (
                            <tr
                                key={rowIndex}
                                className="border-b border-manus-border/50 hover:bg-manus-hover/50 transition-colors"
                            >
                                {row.map((cell, cellIndex) => (
                                    <td
                                        key={cellIndex}
                                        className="px-4 py-2.5 text-manus-text font-mono whitespace-nowrap"
                                        title={String(cell)}
                                    >
                                        {cell === null ? (
                                            <span className="text-manus-subtle italic">NULL</span>
                                        ) : String(cell).length > 50 ? (
                                            `${String(cell).substring(0, 50)}...`
                                        ) : (
                                            String(cell)
                                        )}
                                    </td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}
