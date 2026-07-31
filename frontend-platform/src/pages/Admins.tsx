import { useEffect, useState } from 'react';
import { Plus, Pencil, Trash2, Check, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import api from '@/lib/api';

interface Admin {
    id: string;
    username: string;
    email: string;
    is_active: boolean;
    created_at: string;
    last_login: string | null;
}

interface AdminFormData {
    username: string;
    email: string;
    password: string;
    is_active: boolean;
}

export default function AdminsPage() {
    const [admins, setAdmins] = useState<Admin[]>([]);
    const [loading, setLoading] = useState(true);
    const [showModal, setShowModal] = useState(false);
    const [editingAdmin, setEditingAdmin] = useState<Admin | null>(null);
    const [formData, setFormData] = useState<AdminFormData>({
        username: '',
        email: '',
        password: '',
        is_active: true,
    });
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState('');

    const fetchAdmins = async () => {
        try {
            const response = await api.get('/admins');
            setAdmins(response.data);
        } catch (err) {
            console.error('Failed to fetch admins:', err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchAdmins();
    }, []);

    const openCreateModal = () => {
        setEditingAdmin(null);
        setFormData({ username: '', email: '', password: '', is_active: true });
        setError('');
        setShowModal(true);
    };

    const openEditModal = (admin: Admin) => {
        setEditingAdmin(admin);
        setFormData({
            username: admin.username,
            email: admin.email,
            password: '',
            is_active: admin.is_active,
        });
        setError('');
        setShowModal(true);
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        setError('');

        try {
            if (editingAdmin) {
                await api.put(`/admins/${editingAdmin.id}`, {
                    username: formData.username,
                    email: formData.email,
                    password: formData.password || undefined,
                    is_active: formData.is_active,
                });
            } else {
                await api.post('/admins', formData);
            }
            setShowModal(false);
            fetchAdmins();
        } catch (err: any) {
            setError(err.response?.data?.detail || '操作失败');
        } finally {
            setSubmitting(false);
        }
    };

    const handleDelete = async (admin: Admin) => {
        if (!confirm(`确定要删除管理员 "${admin.username}" 吗？`)) return;

        try {
            await api.delete(`/admins/${admin.id}`);
            fetchAdmins();
        } catch (err: any) {
            alert(err.response?.data?.detail || '删除失败');
        }
    };

    const formatDate = (dateStr: string | null) => {
        if (!dateStr) return '-';
        return new Date(dateStr).toLocaleString('zh-CN');
    };

    return (
        <div className="animate-fade-in">
            {/* 页面头部 */}
            <div className="flex justify-between items-center mb-6">
                <div>
                    <h1 className="text-2xl font-bold text-manus-text mb-1">管理员管理</h1>
                    <p className="text-manus-muted">管理平台管理员账号</p>
                </div>
                <Button onClick={openCreateModal} className="gap-2">
                    <Plus size={18} />
                    新增管理员
                </Button>
            </div>

            {/* 管理员列表 */}
            <Card className="bg-manus-elevated border-manus-border shadow-sm overflow-hidden">
                <table className="w-full">
                    <thead>
                        <tr className="bg-manus-secondary border-b border-manus-border">
                            <th className="px-5 py-4 text-left font-semibold text-manus-text">用户名</th>
                            <th className="px-5 py-4 text-left font-semibold text-manus-text">邮箱</th>
                            <th className="px-5 py-4 text-center font-semibold text-manus-text">状态</th>
                            <th className="px-5 py-4 text-left font-semibold text-manus-text">创建时间</th>
                            <th className="px-5 py-4 text-left font-semibold text-manus-text">最后登录</th>
                            <th className="px-5 py-4 text-center font-semibold text-manus-text">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            <tr>
                                <td colSpan={6} className="px-5 py-10 text-center text-manus-muted">
                                    加载中...
                                </td>
                            </tr>
                        ) : admins.length === 0 ? (
                            <tr>
                                <td colSpan={6} className="px-5 py-10 text-center text-manus-muted">
                                    暂无管理员
                                </td>
                            </tr>
                        ) : (
                            admins.map((admin) => (
                                <tr key={admin.id} className="border-b border-manus-border hover:bg-manus-secondary/50 transition-colors">
                                    <td className="px-5 py-4 font-medium text-manus-text">{admin.username}</td>
                                    <td className="px-5 py-4 text-manus-muted">{admin.email}</td>
                                    <td className="px-5 py-4 text-center">
                                        <span
                                            className={cn(
                                                "inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium",
                                                admin.is_active
                                                    ? "bg-green-100 text-green-700"
                                                    : "bg-red-100 text-red-700"
                                            )}
                                        >
                                            {admin.is_active ? <Check size={14} /> : <X size={14} />}
                                            {admin.is_active ? '活跃' : '禁用'}
                                        </span>
                                    </td>
                                    <td className="px-5 py-4 text-sm text-manus-muted">
                                        {formatDate(admin.created_at)}
                                    </td>
                                    <td className="px-5 py-4 text-sm text-manus-muted">
                                        {formatDate(admin.last_login)}
                                    </td>
                                    <td className="px-5 py-4 text-center">
                                        <div className="flex gap-2 justify-center">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={() => openEditModal(admin)}
                                                className="h-8 w-8"
                                            >
                                                <Pencil size={16} />
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                onClick={() => handleDelete(admin)}
                                                className="h-8 w-8 text-red-500 hover:text-red-600 hover:bg-red-50"
                                            >
                                                <Trash2 size={16} />
                                            </Button>
                                        </div>
                                    </td>
                                </tr>
                            ))
                        )}
                    </tbody>
                </table>
            </Card>

            {/* 模态框 */}
            {showModal && (
                <div
                    className="fixed inset-0 bg-black/50 flex items-center justify-center z-[1000]"
                    onClick={() => setShowModal(false)}
                >
                    <Card
                        className="w-[420px] max-w-[90vw] bg-manus-elevated border-manus-border shadow-xl animate-slide-up"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <CardHeader>
                            <CardTitle className="text-xl text-manus-text">
                                {editingAdmin ? '编辑管理员' : '新增管理员'}
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            {error && (
                                <div className="p-3 mb-4 bg-red-50 text-red-600 rounded-lg text-sm">
                                    {error}
                                </div>
                            )}

                            <form onSubmit={handleSubmit} className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="username">用户名 *</Label>
                                    <Input
                                        id="username"
                                        value={formData.username}
                                        onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                                        required
                                        className="bg-manus-secondary border-manus-border text-manus-text"
                                    />
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="email">邮箱 *</Label>
                                    <Input
                                        id="email"
                                        type="email"
                                        value={formData.email}
                                        onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                                        required
                                        className="bg-manus-secondary border-manus-border text-manus-text"
                                    />
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="password">
                                        密码 {editingAdmin ? '(留空保持不变)' : '*'}
                                    </Label>
                                    <Input
                                        id="password"
                                        type="password"
                                        value={formData.password}
                                        onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                                        required={!editingAdmin}
                                        className="bg-manus-secondary border-manus-border text-manus-text"
                                    />
                                </div>

                                <div className="flex items-center gap-2">
                                    <input
                                        type="checkbox"
                                        id="is_active"
                                        checked={formData.is_active}
                                        onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                                        className="w-4 h-4 rounded border-manus-border"
                                    />
                                    <Label htmlFor="is_active" className="cursor-pointer">账号启用</Label>
                                </div>

                                <div className="flex gap-3 justify-end pt-4">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        onClick={() => setShowModal(false)}
                                    >
                                        取消
                                    </Button>
                                    <Button type="submit" disabled={submitting}>
                                        {submitting ? '提交中...' : editingAdmin ? '保存' : '创建'}
                                    </Button>
                                </div>
                            </form>
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    );
}
