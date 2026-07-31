import axios from "axios";

const appBase = (import.meta.env.BASE_URL || "/").replace(/\/$/, "");
const loginPath = appBase ? `${appBase}/login` : "/login";

const api = axios.create({
  baseURL: "/api/platform",
  headers: {
    "Content-Type": "application/json",
  },
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("platform_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("platform_token");
      if (!window.location.pathname.includes("/login")) {
        window.location.href = loginPath;
      }
    }
    return Promise.reject(error);
  }
);

export default api;
