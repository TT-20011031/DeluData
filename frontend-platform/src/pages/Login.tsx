import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import api from "@/lib/api";

interface AuthResponse {
    access_token: string;
    token_type: string;
    admin: {
        id: string;
        username: string;
        email: string;
        is_active: boolean;
        created_at: string;
        last_login: string | null;
    };
}

export default function LoginPage() {
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const navigate = useNavigate();

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setError("");

        try {
            const formData = new URLSearchParams();
            formData.append("username", username);
            formData.append("password", password);

            const response = await api.post<AuthResponse>("/auth/login", formData, {
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            });

            localStorage.setItem("platform_token", response.data.access_token);
            localStorage.setItem("admin_user", JSON.stringify(response.data.admin));

            navigate("/dashboard");
        } catch (err: any) {
            console.error("Login failed", err);
            setError(err.response?.data?.detail || "登录失败，请检查用户名或密码");
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex flex-col items-center justify-center bg-zinc-50/50">
            {/* 顶部装饰条 */}
            <div className="fixed top-0 left-0 w-full h-1 bg-zinc-900" />

            <div className="w-full max-w-sm px-4 animate-fade-in">
                {/* 标题区域 */}
                <div className="text-center mb-8">
                    <div className="mx-auto w-12 h-12 bg-zinc-900 rounded-xl flex items-center justify-center mb-4 shadow-lg shadow-zinc-900/10">
                        <span className="text-white font-bold text-xl">D</span>
                    </div>
                    <h1 className="text-2xl font-bold tracking-tight text-zinc-900">
                        平台管理
                    </h1>
                    <p className="text-sm text-zinc-500 mt-2">
                        DeluData Platform Admin
                    </p>
                </div>

                {/* 登录卡片 */}
                <Card className="border-zinc-200 shadow-sm bg-white">
                    <CardHeader className="space-y-1 pb-2">
                        {/* 这里留空或放一些副标题 */}
                    </CardHeader>
                    <CardContent className="pt-4">
                        <form onSubmit={handleLogin} className="space-y-4">
                            {error && (
                                <div className="p-3 text-sm text-red-600 bg-red-50 border border-red-100 rounded-md">
                                    {error}
                                </div>
                            )}

                            <div className="space-y-2">
                                <Label htmlFor="username" className="text-zinc-700 font-medium">
                                    账号
                                </Label>
                                <Input
                                    id="username"
                                    type="text"
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    required
                                    className="h-10 border-zinc-200 focus:border-zinc-500 focus:ring-zinc-200 bg-zinc-50/50"
                                    placeholder="admin"
                                />
                            </div>

                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="password" className="text-zinc-700 font-medium">
                                        密码
                                    </Label>
                                </div>
                                <Input
                                    id="password"
                                    type="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    required
                                    className="h-10 border-zinc-200 focus:border-zinc-500 focus:ring-zinc-200 bg-zinc-50/50"
                                    placeholder="••••••••"
                                />
                            </div>

                            <Button
                                type="submit"
                                disabled={loading}
                                className={cn(
                                    "w-full h-10 mt-2 font-medium transition-all",
                                    "bg-zinc-900 hover:bg-zinc-800 text-white",
                                    "shadow-sm hover:shadow-md"
                                )}
                            >
                                {loading ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        登录中...
                                    </>
                                ) : (
                                    "登 录"
                                )}
                            </Button>
                        </form>
                    </CardContent>
                </Card>

                {/* 底部版权 */}
                <p className="text-center text-xs text-zinc-400 mt-8">
                    © 2024 DeluData Inc.
                </p>
            </div>
        </div>
    );
}
