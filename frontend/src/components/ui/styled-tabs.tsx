/**
 * 样式化的 Tabs 导航组件
 * 
 * 提供现代化的 Tab 切换器，支持：
 * - 圆角胶囊样式
 * - 激活状态的 accent 背景
 * - 可选的图标和徽标
 * - 悬停动画效果
 * 
 * @example
 * ```tsx
 * <Tabs value={activeTab} onValueChange={setActiveTab}>
 *   <StyledTabsNav items={tabItems} onValueChange={setActiveTab} />
 *   <TabsContent value="tab1">...</TabsContent>
 * </Tabs>
 * ```
 */
import type { ReactNode } from 'react'
import { TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

export interface StyledTabItem {
    value: string
    label: string
    icon?: ReactNode
    badge?: ReactNode
    hasError?: boolean
}

interface StyledTabsNavProps {
    items: StyledTabItem[]
    onValueChange: (value: string) => void
    className?: string
}

/**
 * 样式化的 Tab 切换器
 */
export function StyledTabsNav({
    items,
    onValueChange,
    className,
}: StyledTabsNavProps) {
    return (
        <TabsList
            className={cn(
                "inline-flex h-11 p-1.5 gap-1.5 bg-manus-tertiary/80 backdrop-blur rounded-2xl border border-manus-border/50",
                className
            )}
        >
            {items.map((item) => (
                <TabsTrigger
                    key={item.value}
                    value={item.value}
                    onClick={() => onValueChange(item.value)}
                    className={cn(
                        "relative h-8 px-4 rounded-xl text-sm font-medium transition-all duration-200",
                        "data-[state=inactive]:text-manus-muted data-[state=inactive]:hover:text-manus-text data-[state=inactive]:hover:bg-manus-elevated/60",
                        "data-[state=active]:bg-accent data-[state=active]:text-white data-[state=active]:shadow-lg data-[state=active]:shadow-accent/25"
                    )}
                >
                    <span className="flex items-center gap-2">
                        {item.icon}
                        {item.label}
                        {item.badge}
                    </span>

                    {/* 错误提示红点 */}
                    {item.hasError && (
                        <span
                            className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-red-500 rounded-full border-2 border-manus-tertiary animate-pulse"
                            title="有必填项未填写"
                        />
                    )}
                </TabsTrigger>
            ))}
        </TabsList>
    )
}

