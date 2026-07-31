import { Switch } from '@/components/ui/switch'

interface DeepSearchToggleProps {
    enabled: boolean
    onChange: (enabled: boolean) => void
    disabled?: boolean
}

export function DeepSearchToggle({ enabled, onChange, disabled = false }: DeepSearchToggleProps) {
    return (
        <div className="flex items-center gap-2 px-2 py-1 rounded-md border border-manus-border/60 bg-manus/50">
            <Switch
                checked={enabled}
                onCheckedChange={onChange}
                disabled={disabled}
            />
            <span className="text-xs text-manus-subtle">深度检索</span>
        </div>
    )
}

