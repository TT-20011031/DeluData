import { Settings as SettingsIcon, AlertCircle } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';

export default function SettingsPage() {
    return (
        <div className="animate-fade-in">
            <h1 className="text-2xl font-bold text-manus-text mb-2">系统设置</h1>
            <p className="text-manus-muted mb-8">管理系统配置</p>

            <Card className="bg-manus-elevated border-manus-border shadow-sm">
                <CardContent className="py-12 text-center">
                    <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-manus-secondary flex items-center justify-center">
                        <SettingsIcon className="h-8 w-8 text-manus-subtle" />
                    </div>
                    <h2 className="text-lg font-semibold text-manus-text mb-2">
                        系统设置
                    </h2>
                    <p className="text-manus-muted mb-6">
                        此功能正在开发中，敬请期待...
                    </p>
                    <div className="inline-flex items-center gap-2 px-4 py-2 bg-amber-50 text-amber-700 rounded-lg text-sm">
                        <AlertCircle size={18} />
                        Coming Soon
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
