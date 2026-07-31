/**
 * Toast 通知组件
 * 
 * 用于显示非阻塞的用户提示信息，替代 alert()
 */
import { useState, useEffect, createContext, useContext, useCallback } from 'react'
import type { ReactNode } from 'react'
import { X, CheckCircle, AlertCircle, AlertTriangle, Info } from 'lucide-react'
import { cn } from '@/lib/utils'

// Toast 类型
type ToastType = 'success' | 'error' | 'warning' | 'info'

// Toast 数据结构
interface ToastData {
    id: string
    type: ToastType
    title: string
    description?: string
    duration?: number
}

// Context 类型
interface ToastContextType {
    toast: (options: Omit<ToastData, 'id'>) => void
    dismiss: (id: string) => void
}

const ToastContext = createContext<ToastContextType | null>(null)

// Hook: 使用 Toast
export function useToast() {
    const context = useContext(ToastContext)
    if (!context) {
        throw new Error('useToast must be used within a ToastProvider')
    }
    return context
}

// Toast 单项组件
function ToastItem({ toast, onDismiss }: { toast: ToastData; onDismiss: () => void }) {
    useEffect(() => {
        const timer = setTimeout(onDismiss, toast.duration || 5000)
        return () => clearTimeout(timer)
    }, [toast.duration, onDismiss])

    const icons = {
        success: <CheckCircle className="h-5 w-5 text-green-400" />,
        error: <AlertCircle className="h-5 w-5 text-red-400" />,
        warning: <AlertTriangle className="h-5 w-5 text-yellow-400" />,
        info: <Info className="h-5 w-5 text-blue-400" />
    }

    const bgColors = {
        success: 'border-green-500/30 bg-green-500/10',
        error: 'border-red-500/30 bg-red-500/10',
        warning: 'border-yellow-500/30 bg-yellow-500/10',
        info: 'border-blue-500/30 bg-blue-500/10'
    }

    return (
        <div className={cn(
            "flex items-start gap-3 p-4 rounded-lg border shadow-lg backdrop-blur-sm",
            "animate-in slide-in-from-right-full duration-300",
            bgColors[toast.type]
        )}>
            {icons[toast.type]}
            <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-manus-text">{toast.title}</p>
                {toast.description && (
                    <p className="mt-1 text-xs text-manus-subtle whitespace-pre-line">{toast.description}</p>
                )}
            </div>
            <button
                onClick={onDismiss}
                className="text-manus-subtle hover:text-manus-text transition-colors"
            >
                <X className="h-4 w-4" />
            </button>
        </div>
    )
}

// Toast Provider 组件
export function ToastProvider({ children }: { children: ReactNode }) {
    const [toasts, setToasts] = useState<ToastData[]>([])

    const toast = useCallback((options: Omit<ToastData, 'id'>) => {
        const id = Math.random().toString(36).substring(7)
        setToasts(prev => [...prev, { ...options, id }])
    }, [])

    const dismiss = useCallback((id: string) => {
        setToasts(prev => prev.filter(t => t.id !== id))
    }, [])

    return (
        <ToastContext.Provider value={{ toast, dismiss }}>
            {children}
            {/* Toast 容器 - 固定在右上角 */}
            <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 max-w-sm w-full pointer-events-none">
                {toasts.map(t => (
                    <div key={t.id} className="pointer-events-auto">
                        <ToastItem toast={t} onDismiss={() => dismiss(t.id)} />
                    </div>
                ))}
            </div>
        </ToastContext.Provider>
    )
}
