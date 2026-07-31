import { ArrowLeft, ShieldCheck } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { SemanticModelManager } from '@/components/extendConfig'
import { Button } from '@/components/ui/button'

export default function SemanticGovernancePage() {
    const navigate = useNavigate()
    const [searchParams] = useSearchParams()
    const initialWorkspace = searchParams.get('workspace') === 'access-policies' ? 'access-policies' : null

    return (
        <div className="flex h-full min-h-0 flex-col bg-manus">
            <header className="flex h-14 shrink-0 items-center justify-between border-b border-manus-border bg-manus-secondary px-4">
                <div className="flex items-center gap-3">
                    <Button variant="ghost" size="sm" onClick={() => navigate('/database?tab=semantic')} className="text-manus-muted hover:text-manus-text">
                        <ArrowLeft className="mr-1 h-4 w-4" />数据库配置
                    </Button>
                    <div className="h-5 w-px bg-manus-border" />
                    <div className="flex items-center gap-2 text-sm font-semibold text-manus-text"><ShieldCheck className="h-4 w-4 text-emerald-500" />可信语义治理</div>
                </div>
                <span className="text-xs text-manus-muted">Schema Diff · Readiness · Runtime Modes</span>
            </header>
            <main className="min-h-0 flex-1 overflow-hidden"><SemanticModelManager initialWorkspace={initialWorkspace} /></main>
        </div>
    )
}
