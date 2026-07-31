/**
 * 通用分组筛选器组件
 * 
 * 用于 SQL 示例和模板的分组筛选标签栏
 */
import { cn } from '@/lib/utils'
import type { GroupBase } from '@/types/extendConfig'

interface GroupFilterProps<T extends GroupBase> {
    /** 分组列表 */
    groups: T[]
    /** 当前选中的分组 ID */
    selectedId: number | 'all'
    /** 选中变化回调 */
    onSelect: (id: number | 'all') => void
    /** 总数（用于"全部"标签显示） */
    totalCount: number
}

export function GroupFilter<T extends GroupBase>({
    groups,
    selectedId,
    onSelect,
    totalCount,
}: GroupFilterProps<T>) {
    return (
        <div className="flex flex-wrap gap-2 mb-4">
            {/* 全部标签 */}
            <button
                onClick={() => onSelect('all')}
                className={cn(
                    "px-3 py-1.5 rounded-full text-sm border transition-all",
                    selectedId === 'all'
                        ? "bg-accent text-white border-accent"
                        : "border-manus-border text-manus-muted hover:text-manus-text"
                )}
            >
                全部 ({totalCount})
            </button>

            {/* 分组标签 */}
            {groups.map(group => (
                <button
                    key={group.id}
                    onClick={() => onSelect(group.id)}
                    className={cn(
                        "flex items-center gap-2 px-3 py-1.5 rounded-full text-sm border transition-all",
                        selectedId === group.id
                            ? "scale-105 shadow-md"
                            : "opacity-80 hover:opacity-100"
                    )}
                    style={{
                        borderColor: group.color,
                        color: selectedId === group.id ? 'white' : group.color,
                        backgroundColor: selectedId === group.id ? group.color : 'transparent'
                    }}
                >
                    <span
                        className="w-2 h-2 rounded-full"
                        style={{ backgroundColor: selectedId === group.id ? 'white' : group.color }}
                    />
                    {group.name} ({group.example_count})
                </button>
            ))}
        </div>
    )
}
