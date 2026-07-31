import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { handleUnauthorized, useAuthStore } from '@/stores/authStore'

const originalFetch = window.fetch.bind(window)
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const response = await originalFetch(input, init)

  if (response.status === 401) {
    handleUnauthorized()
    return response
  }

  if (response.status === 403) {
    let detail: string | undefined
    try {
      const data = await response.clone().json()
      detail = data?.detail
    } catch {
      detail = undefined
    }

    if (detail === 'workspace_disabled') {
      if (window.location.pathname !== '/disabled') {
        window.location.href = '/disabled'
      }
    } else if (detail === 'user_disabled') {
      const { logout } = useAuthStore.getState()
      logout()
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
  }

  return response
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
