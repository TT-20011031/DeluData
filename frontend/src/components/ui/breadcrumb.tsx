/**
 * Breadcrumb 面包屑导航组件
 * 
 * 符合 shadcn/ui 设计风格
 */
import { Fragment } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, Home } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface BreadcrumbItem {
    /** 显示文本 */
    label: string
    /** 跳转链接，最后一项通常不传 */
    href?: string
    /** 可选图标 */
    icon?: React.ReactNode
}

interface BreadcrumbProps {
    /** 面包屑项目列表 */
    items: BreadcrumbItem[]
    /** 是否显示首页图标 */
    showHome?: boolean
    /** 自定义类名 */
    className?: string
}

/**
 * 面包屑导航
 * 
 * @example
 * <Breadcrumb 
 *     items={[
 *         { label: '扩展配置', href: '/extend-config' },
 *         { label: '模板管理', href: '/extend-config?tab=templates' },
 *         { label: '编辑模板' }
 *     ]} 
 * />
 */
export function Breadcrumb({ items, showHome = true, className }: BreadcrumbProps) {
    return (
        <nav
            aria-label="面包屑导航"
            className={cn(
                "flex items-center gap-1.5 text-sm",
                className
            )}
        >
            {showHome && (
                <>
                    <Link
                        to="/"
                        className="flex items-center justify-center w-6 h-6 rounded hover:bg-manus-secondary text-manus-muted hover:text-manus-text transition-colors"
                        aria-label="首页"
                    >
                        <Home className="h-4 w-4" />
                    </Link>
                    <ChevronRight className="h-4 w-4 text-manus-muted/50" />
                </>
            )}

            {items.map((item, index) => {
                const isLast = index === items.length - 1

                return (
                    <Fragment key={index}>
                        {index > 0 && (
                            <ChevronRight className="h-4 w-4 text-manus-muted/50" />
                        )}

                        {item.href && !isLast ? (
                            <Link
                                to={item.href}
                                className={cn(
                                    "flex items-center gap-1.5 px-2 py-1 rounded",
                                    "text-manus-muted hover:text-manus-text hover:bg-manus-secondary",
                                    "transition-colors"
                                )}
                            >
                                {item.icon}
                                <span>{item.label}</span>
                            </Link>
                        ) : (
                            <span
                                className={cn(
                                    "flex items-center gap-1.5 px-2 py-1",
                                    isLast
                                        ? "text-manus-text font-medium"
                                        : "text-manus-muted"
                                )}
                                aria-current={isLast ? "page" : undefined}
                            >
                                {item.icon}
                                <span className="max-w-[200px] truncate">{item.label}</span>
                            </span>
                        )}
                    </Fragment>
                )
            })}
        </nav>
    )
}

export default Breadcrumb
