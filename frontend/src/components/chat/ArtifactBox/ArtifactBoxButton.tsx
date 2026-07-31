/**
 * 暂存箱入口按钮（仅负责开关侧边栏）
 */
import { Archive } from 'lucide-react'
import { useArtifactBoxStore } from '@/stores/artifactBoxStore'

export function ArtifactBoxButton() {
    const { toggle, artifacts, isOpen } = useArtifactBoxStore()
    const count = artifacts.length

    return (
        <button
            onClick={toggle}
            className={`
                relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs
                transition-colors duration-150
                ${isOpen
                    ? 'text-accent bg-accent/10'
                    : 'text-manus-muted hover:text-manus-text hover:bg-accent/8'
                }
            `}
            title="暂存箱"
        >
            <Archive className="h-3.5 w-3.5 flex-shrink-0" />
            <span className="hidden sm:inline font-medium">暂存箱</span>
            {count > 0 && (
                <span className="ml-0.5 flex h-4 min-w-4 px-0.5 items-center justify-center rounded-full bg-accent text-white text-[9px] font-bold leading-none">
                    {count > 99 ? '99+' : count}
                </span>
            )}
        </button>
    )
}
