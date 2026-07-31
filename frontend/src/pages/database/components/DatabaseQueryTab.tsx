import { AlertCircle, CheckCircle, Clock, Code2, Loader2, Play } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

import type { QueryResult } from '../types'

interface DatabaseQueryTabProps {
    sqlQuery: string
    queryResult: QueryResult | null
    isExecuting: boolean
    onSqlQueryChange: (value: string) => void
    onExecuteQuery: () => void
}

export function DatabaseQueryTab({
    sqlQuery,
    queryResult,
    isExecuting,
    onSqlQueryChange,
    onExecuteQuery,
}: DatabaseQueryTabProps) {
    return (
        <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
            <div className="flex flex-col gap-2">
                <textarea
                    value={sqlQuery}
                    onChange={(e) => onSqlQueryChange(e.target.value)}
                    placeholder="SELECT * FROM table_name LIMIT 100;"
                    className={cn(
                        'w-full h-32 p-4 rounded-lg font-mono text-sm resize-none',
                        'bg-manus-secondary border border-manus-border',
                        'text-manus-text placeholder:text-manus-subtle',
                        'focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent',
                        'transition-all duration-200',
                    )}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                            onExecuteQuery()
                        }
                    }}
                />
                <div className="flex items-center justify-between">
                    <span className="text-xs text-manus-subtle">
                        Ctrl+Enter 执行，仅支持 SELECT 语句
                    </span>
                    <Button
                        onClick={onExecuteQuery}
                        disabled={isExecuting || !sqlQuery.trim()}
                        className="bg-accent hover:bg-accent/90 text-white"
                    >
                        {isExecuting ? (
                            <Loader2 className="h-4 w-4 animate-spin mr-2" />
                        ) : (
                            <Play className="h-4 w-4 mr-2" />
                        )}
                        执行查询
                    </Button>
                </div>
            </div>

            <div className="flex-1 flex flex-col overflow-hidden">
                {queryResult && (
                    <>
                        <div className="flex items-center gap-4 mb-3 text-sm">
                            {queryResult.error ? (
                                <div className="flex items-center gap-2 text-error">
                                    <AlertCircle className="h-4 w-4" />
                                    {queryResult.error}
                                </div>
                            ) : (
                                <>
                                    <div className="flex items-center gap-1.5 text-success">
                                        <CheckCircle className="h-4 w-4" />
                                        {queryResult.rowCount} 行
                                        {queryResult.truncated && (
                                            <span className="text-warning">(已截断)</span>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-1.5 text-manus-muted">
                                        <Clock className="h-4 w-4" />
                                        {queryResult.executionTimeMs.toFixed(1)} ms
                                    </div>
                                </>
                            )}
                        </div>

                        {!queryResult.error && queryResult.rows.length > 0 && (
                            <div className="flex-1 bg-manus-secondary/50 border border-manus-border rounded-lg overflow-hidden">
                                <ScrollArea className="h-full">
                                    <table className="w-full text-sm">
                                        <thead className="sticky top-0 bg-manus-tertiary border-b border-manus-border">
                                            <tr>
                                                {queryResult.columns.map((col, i) => (
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
                                            {queryResult.rows.map((row, rowIndex) => (
                                                <tr
                                                    key={rowIndex}
                                                    className="border-b border-manus-border/50 hover:bg-manus-hover/50 transition-colors"
                                                >
                                                    {row.map((cell, cellIndex) => (
                                                        <td
                                                            key={cellIndex}
                                                            className="px-4 py-2.5 text-manus-text font-mono max-w-[300px] truncate"
                                                            title={String(cell)}
                                                        >
                                                            {cell === null ? (
                                                                <span className="text-manus-subtle italic">NULL</span>
                                                            ) : String(cell)}
                                                        </td>
                                                    ))}
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </ScrollArea>
                            </div>
                        )}
                    </>
                )}

                {!queryResult && (
                    <div className="flex-1 flex items-center justify-center text-manus-subtle">
                        <div className="text-center">
                            <Code2 className="h-12 w-12 mx-auto mb-3 opacity-50" />
                            <p>输入 SQL 语句并执行查看结果</p>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}
