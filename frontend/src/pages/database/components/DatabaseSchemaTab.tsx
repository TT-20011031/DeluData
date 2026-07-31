import { ChevronDown, Plus, Table2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

import type { TableSchema } from '../types'

interface DatabaseSchemaTabProps {
    schema: TableSchema[]
    selectedTable: string | null
    expandedTable: string | null
    tableDescriptions: Record<string, string>
    onExpandedTableChange: (tableName: string | null) => void
    onOpenDescription: (tableName: string) => void
}

export function DatabaseSchemaTab({
    schema,
    selectedTable,
    expandedTable,
    tableDescriptions,
    onExpandedTableChange,
    onOpenDescription,
}: DatabaseSchemaTabProps) {
    const visibleTables = selectedTable
        ? schema.filter((t) => t.name === selectedTable)
        : schema

    return (
        <div className="flex-1 p-4 overflow-auto">
            <Card className="bg-manus-secondary/50 border-manus-border backdrop-blur-sm">
                <CardHeader>
                    <CardTitle className="text-manus-text text-lg flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <Table2 className="h-5 w-5 text-accent" />
                            {selectedTable ? `${selectedTable} 结构` : '数据库结构'}
                        </div>
                        {selectedTable && (
                            <div className="flex items-center gap-2">
                                <div
                                    className={cn(
                                        'px-3 py-1 rounded text-sm cursor-pointer transition-colors max-w-md truncate',
                                        tableDescriptions[selectedTable]
                                            ? 'bg-accent/10 text-manus-text hover:bg-accent/20'
                                            : 'text-manus-subtle hover:text-manus-text hover:bg-manus-tertiary border border-dashed border-manus-border',
                                    )}
                                    onClick={() => onOpenDescription(selectedTable)}
                                    title="点击编辑描述"
                                >
                                    {tableDescriptions[selectedTable] || '点击添加描述...'}
                                </div>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 w-7 p-0"
                                    onClick={() => onOpenDescription(selectedTable)}
                                >
                                    <Plus className="h-4 w-4 text-manus-subtle hover:text-accent" />
                                </Button>
                            </div>
                        )}
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    <ScrollArea className="h-[calc(100vh-280px)]">
                        <div className="space-y-2">
                            {visibleTables.map((table) => (
                                <div
                                    key={table.name}
                                    className="bg-manus-tertiary rounded-lg overflow-hidden transition-all duration-300"
                                >
                                    <button
                                        onClick={() =>
                                            onExpandedTableChange(
                                                expandedTable === table.name ? null : table.name,
                                            )
                                        }
                                        className="w-full flex items-center justify-between p-3 hover:bg-manus-hover transition-colors"
                                    >
                                        <div className="flex items-center gap-2">
                                            <ChevronDown
                                                className={cn(
                                                    'h-4 w-4 text-manus-muted transition-transform duration-200',
                                                    expandedTable === table.name ? 'rotate-0' : '-rotate-90',
                                                )}
                                            />
                                            <span className="font-mono text-manus-text">{table.name}</span>
                                            <span className="text-xs text-manus-subtle">
                                                ({table.columns.length} 列)
                                            </span>
                                        </div>
                                        <span className="text-xs text-manus-muted">
                                            {table.row_count?.toLocaleString()} 行
                                        </span>
                                    </button>

                                    <div
                                        className={cn(
                                            'overflow-hidden transition-all duration-300',
                                            expandedTable === table.name ? 'max-h-[500px]' : 'max-h-0',
                                        )}
                                    >
                                        <div className="border-t border-manus-border p-3 space-y-1">
                                            {table.columns.map((col) => (
                                                <div
                                                    key={col.name}
                                                    className="flex items-center justify-between text-sm py-1.5 px-2 rounded hover:bg-manus-hover transition-colors"
                                                >
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-manus-text font-mono">
                                                            {col.name}
                                                        </span>
                                                        {col.primary_key && (
                                                            <span className="px-1.5 py-0.5 text-xs bg-accent/20 text-accent rounded animate-pulse">
                                                                PK
                                                            </span>
                                                        )}
                                                    </div>
                                                    <span className="text-xs text-manus-muted font-mono">
                                                        {col.type}
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </ScrollArea>
                </CardContent>
            </Card>
        </div>
    )
}
