const STORAGE_TOKEN_KEY = "kiosk_device_token";

export const API_CONFIG = {
  baseUrl: import.meta.env.VITE_API_BASE_URL || "/api/experience",
};

export function getDeviceToken(): string | null {
  return localStorage.getItem(STORAGE_TOKEN_KEY);
}

export function setDeviceToken(token: string): void {
  localStorage.setItem(STORAGE_TOKEN_KEY, token);
}

export function clearDeviceToken(): void {
  localStorage.removeItem(STORAGE_TOKEN_KEY);
}
