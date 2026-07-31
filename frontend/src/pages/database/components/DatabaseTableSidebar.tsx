import type { MouseEvent, RefObject } from 'react'

import { Rows, Search, Table2 } from 'lucide-react'

import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

import type { TableSchema } from '../types'

interface DatabaseTableSidebarProps {
    schema: TableSchema[]
    tableNames: string[]
    tablesCount: number
    searchQuery: string
    selectedTable: string | null
    tableDescriptions: Record<string, string>
    sidebarRef: RefObject<HTMLDivElement | null>
    onSearchChange: (value: string) => void
    onSelectTable: (tableName: string) => void
    onResizeMouseDown: (e: MouseEvent) => void
}

export function DatabaseTableSidebar({
    schema,
    tableNames,
    tablesCount,
    searchQuery,
    selectedTable,
    tableDescriptions,
    sidebarRef,
    onSearchChange,
    onSelectTable,
    onResizeMouseDown,
}: DatabaseTableSidebarProps) {
    const schemaByName = new Map(schema.map((table) => [table.name, table]))
    const allTables = (tableNames.length > 0 ? tableNames : schema.map((table) => table.name))
        .map((name) => schemaByName.get(name) || { name, columns: [] })
    const visibleTables = allTables.filter(
        (table) => !searchQuery || table.name.toLowerCase().includes(searchQuery.toLowerCase()),
    )

    return (
        <div
            ref={sidebarRef}
            className="bg-manus-secondary/30 flex flex-col relative shrink-0"
            style={{ width: sidebarRef.current?.getBoundingClientRect().width || 288 }}
        >
            <div className="p-3 border-b border-manus-border space-y-2">
                <h3 className="text-sm font-medium text-manus-text flex items-center gap-2">
                    <Rows className="h-4 w-4 text-accent" />
                    表 ({visibleTables.length}/{tablesCount})
                </h3>
                <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-manus-subtle" />
                    <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => onSearchChange(e.target.value)}
                        placeholder="搜索表名..."
                        className="w-full pl-8 pr-3 py-1.5 text-sm bg-manus-tertiary border border-manus-border rounded-lg text-manus-text placeholder:text-manus-subtle focus:outline-none focus:ring-1 focus:ring-accent/50 transition-all"
                    />
                </div>
            </div>
            <ScrollArea className="flex-1">
                <div className="p-2 space-y-1">
                    {visibleTables.map((table, index) => (
                        <div
                            key={table.name}
                            className={cn(
                                'rounded-lg transition-all duration-200',
                                selectedTable === table.name
                                    ? 'bg-accent/20 border border-accent/30'
                                    : 'border border-transparent hover:bg-manus-hover',
                            )}
                        >
                            <button
                                onClick={() => onSelectTable(table.name)}
                                className="w-full flex items-center justify-between p-2.5 text-left group"
                                style={{ animationDelay: `${index * 30}ms` }}
                            >
                                <div className="flex items-center gap-2 min-w-0">
                                    <Table2
                                        className={cn(
                                            'h-4 w-4 shrink-0 transition-colors',
                                            selectedTable === table.name
                                                ? 'text-accent'
                                                : 'text-manus-subtle group-hover:text-manus-muted',
                                        )}
                                    />
                                    <span
                                        className={cn(
                                            'font-mono text-sm truncate',
                                            selectedTable === table.name
                                                ? 'text-manus-text'
                                                : 'text-manus-muted',
                                        )}
                                    >
                                        {table.name}
                                    </span>
                                    {tableDescriptions[table.name] && (
                                        <div
                                            className="w-1.5 h-1.5 rounded-full bg-success/50 shrink-0"
                                            title="已添加描述"
                                        />
                                    )}
                                </div>
                                <span className="text-xs text-manus-subtle shrink-0">
                                    {table.row_count?.toLocaleString()}
                                </span>
                            </button>
                        </div>
                    ))}
                </div>
            </ScrollArea>

            <div
                className="absolute top-0 right-0 w-2 h-full cursor-col-resize bg-manus-border/30 hover:bg-accent/60 active:bg-accent transition-colors z-10 flex items-center justify-center group"
                onMouseDown={onResizeMouseDown}
            >
                <div className="w-0.5 h-8 bg-manus-subtle/50 rounded-full group-hover:bg-white/80 transition-colors" />
            </div>
        </div>
    )
}
