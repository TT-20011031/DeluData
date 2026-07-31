import { create } from "zustand";
import { persist } from "zustand/middleware";
import { clearPreviewCacheForUser } from "@/lib/previewCache";

interface WorkspaceFeatures {
  museum_enabled?: boolean;
  kiosk_enabled?: boolean;
  knowledge_upload_enabled?: boolean;
  knowledge_delete_enabled?: boolean;
  knowledge_rename_enabled?: boolean;
  knowledge_move_enabled?: boolean;
  knowledge_create_folder_enabled?: boolean;
  knowledge_storage_quota_bytes?: number | null;
  knowledge_max_upload_file_size_bytes?: number | null;
}

interface User {
  id: string;
  username: string;
  email?: string;
  role: string;
  workspace_id?: string;
  permissions?: string[];
  department_id?: number;
  workspace_features?: WorkspaceFeatures;
}

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<boolean>;
  register: (username: string, password: string, email?: string) => Promise<boolean>;
  logout: () => void;
  refreshToken: () => Promise<boolean>;
  syncCurrentUser: (user: Partial<User> & Pick<User, "id" | "username" | "role">) => void;
  clearError: () => void;
  setLoading: (loading: boolean) => void;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

async function parseErrorMessage(response: Response, fallback: string): Promise<string> {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const payload = await response.json().catch(() => ({}));
    return payload?.detail || payload?.message || fallback;
  }
  const text = await response.text().catch(() => "");
  if (text.trim().startsWith("<!DOCTYPE")) {
    return "API endpoint mismatch: received HTML instead of JSON";
  }
  return fallback;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: false,
      error: null,

      login: async (username: string, password: string) => {
        set({ isLoading: true, error: null });
        try {
          const response = await fetch(`${API_BASE_URL}/auth/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
          });
          if (!response.ok) {
            const errorMessage = await parseErrorMessage(response, "登录失败");
            set({ isLoading: false, error: errorMessage });
            return false;
          }
          const data = await response.json();
          set({
            user: data.user,
            token: data.access_token,
            isAuthenticated: true,
            isLoading: false,
            error: null,
          });
          return true;
        } catch (error) {
          set({
            isLoading: false,
            error: error instanceof Error ? error.message : "网络错误",
          });
          return false;
        }
      },

      register: async (username: string, password: string, email?: string) => {
        set({ isLoading: true, error: null });
        try {
          const response = await fetch(`${API_BASE_URL}/auth/register`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password, email }),
          });
          if (!response.ok) {
            const errorMessage = await parseErrorMessage(response, "注册失败");
            set({ isLoading: false, error: errorMessage });
            return false;
          }
          const data = await response.json();
          set({
            user: data.user,
            token: data.access_token,
            isAuthenticated: true,
            isLoading: false,
            error: null,
          });
          return true;
        } catch (error) {
          set({
            isLoading: false,
            error: error instanceof Error ? error.message : "网络错误",
          });
          return false;
        }
      },

      logout: () => {
        const userId = get().user?.id;
        set({
          user: null,
          token: null,
          isAuthenticated: false,
          error: null,
        });
        if (userId) {
          void clearPreviewCacheForUser(userId);
        }
      },

      refreshToken: async () => {
        const { token, user } = get();
        if (!token) return false;
        try {
          const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${token}`,
            },
          });
          if (!response.ok) {
            if (user?.id) {
              void clearPreviewCacheForUser(user.id);
            }
            set({ user: null, token: null, isAuthenticated: false });
            return false;
          }
          const data = await response.json();
          set({
            user: data.user,
            token: data.access_token,
            isAuthenticated: true,
          });
          return true;
        } catch {
          if (user?.id) {
            void clearPreviewCacheForUser(user.id);
          }
          set({ user: null, token: null, isAuthenticated: false });
          return false;
        }
      },

      syncCurrentUser: (user) => {
        set((state) => ({
          user: state.user ? { ...state.user, ...user } : user,
          isAuthenticated: true,
        }));
      },

      clearError: () => set({ error: null }),
      setLoading: (loading: boolean) => set({ isLoading: loading }),
    }),
    {
      name: "deludata-auth",
      partialize: (state) => ({
        user: state.user,
        token: state.token,
        isAuthenticated: state.isAuthenticated,
      }),
    },
  ),
);

export function getAuthHeader(): Record<string, string> {
  const token = useAuthStore.getState().token;
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

export function handleUnauthorized(): void {
  const { isAuthenticated, logout } = useAuthStore.getState();
  if (isAuthenticated) logout();
}

export async function fetchWithAuth(url: string, options: RequestInit = {}): Promise<Response> {
  const headers = {
    ...getAuthHeader(),
    ...options.headers,
  };
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    handleUnauthorized();
  }
  return response;
}
