import { useEffect, useState } from 'react';
import { Users, Shield, Activity } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import api from '@/lib/api';

interface Stats {
    totalAdmins: number;
    activeAdmins: number;
}

export default function DashboardPage() {
    const [stats, setStats] = useState<Stats>({ totalAdmins: 0, activeAdmins: 0 });
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchStats = async () => {
            try {
                const response = await api.get('/admins');
                const admins = response.data;
                setStats({
                    totalAdmins: admins.length,
                    activeAdmins: admins.filter((a: any) => a.is_active).length,
                });
            } catch (error) {
                console.error('Failed to fetch stats:', error);
            } finally {
                setLoading(false);
            }
        };
        fetchStats();
    }, []);

    const statCards = [
        {
            title: '管理员总数',
            value: stats.totalAdmins,
            icon: <Users className="h-6 w-6" />,
            color: 'text-blue-500',
            bg: 'bg-blue-50',
        },
        {
            title: '活跃管理员',
            value: stats.activeAdmins,
            icon: <Shield className="h-6 w-6" />,
            color: 'text-green-500',
            bg: 'bg-green-50',
        },
        {
            title: '系统状态',
            value: '正常',
            icon: <Activity className="h-6 w-6" />,
            color: 'text-purple-500',
            bg: 'bg-purple-50',
        },
    ];

    return (
        <div className="animate-fade-in">
            <h1 className="text-2xl font-bold text-manus-text mb-2">仪表盘</h1>
            <p className="text-manus-muted mb-8">欢迎使用平台管理后台</p>

            {/* 统计卡片 */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
                {statCards.map((card, index) => (
                    <Card key={index} className="bg-manus-elevated border-manus-border shadow-sm">
                        <CardContent className="p-6">
                            <div className="flex items-center gap-4">
                                <div className={`w-14 h-14 rounded-xl ${card.bg} ${card.color} flex items-center justify-center`}>
                                    {card.icon}
                                </div>
                                <div>
                                    <p className="text-sm text-manus-muted mb-1">{card.title}</p>
                                    <p className="text-3xl font-bold text-manus-text">
                                        {loading ? '...' : card.value}
                                    </p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>

            {/* 快速操作 */}
            <Card className="bg-manus-elevated border-manus-border shadow-sm">
                <CardHeader>
                    <CardTitle className="text-lg text-manus-text">快速操作</CardTitle>
                </CardHeader>
                <CardContent className="flex gap-3">
                    <Button asChild>
                        <a href="/admins">管理管理员</a>
                    </Button>
                    <Button variant="outline" asChild>
                        <a href="/settings">系统设置</a>
                    </Button>
                </CardContent>
            </Card>
        </div>
    );
}
