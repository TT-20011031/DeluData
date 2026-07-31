/**
 * 平台管理员类型定义
 */

export interface User {
    id: string;
    username: string;
    email: string;
    is_active: boolean;
    role: "admin" | "user";
}

export interface AuthResponse {
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

export interface PlatformAdmin {
    id: string;
    username: string;
    email: string;
    is_active: boolean;
}
