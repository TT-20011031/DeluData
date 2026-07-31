/**
 * 工作空间禁用提示页
 */
import { useState } from 'react'
import { AlertTriangle, RefreshCw, Mail } from 'lucide-react'
import { Button } from '@/components/ui/button'
import FlickeringGrid from '@/components/ui/flickering-grid'
import { getAuthHeader } from '@/stores/authStore'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

export default function WorkspaceDisabledPage() {
  const [isChecking, setIsChecking] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const handleRefresh = async () => {
    setIsChecking(true)
    setMessage(null)

    try {
      const response = await fetch(`${API_BASE_URL}/auth/status`, {
        headers: getAuthHeader(),
      })

      if (response.ok) {
        window.location.href = '/'
        return
      }

      const data = await response.json().catch(() => ({}))
      if (response.status === 403 && data?.detail === 'workspace_disabled') {
        setMessage('账户仍处于禁用状态，请联系管理员续费')
      } else if (response.status === 403 && data?.detail === 'user_disabled') {
        setMessage('当前账号已被禁用，请联系管理员')
      } else {
        setMessage(data?.detail || '状态检查失败，请稍后重试')
      }
    } catch {
      setMessage('网络错误，请稍后重试')
    } finally {
      setIsChecking(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0a0a0f] flex items-center justify-center p-4 relative overflow-hidden w-full">
      <div className="absolute inset-0 z-0">
        <FlickeringGrid
          squareSize={4}
          gridGap={6}
          flickerChance={0.12}
          color="rgb(255, 122, 0)"
          maxOpacity={0.2}
          className="w-full h-full"
        />
      </div>
      <div className="absolute inset-0 bg-gradient-to-t from-[#0a0a0f] via-transparent to-[#0a0a0f] z-10 pointer-events-none" />

      <div className="relative z-20 w-full max-w-lg">
        <div className="relative backdrop-blur-xl bg-white/[0.05] border border-white/[0.1] rounded-2xl p-8">
          <div className="text-center mb-6">
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-orange-500/10 border border-orange-500/30 flex items-center justify-center">
              <AlertTriangle className="h-8 w-8 text-orange-400" />
            </div>
            <h1 className="text-2xl font-semibold text-white mb-2">账户已被禁用</h1>
            <p className="text-white/60 text-sm">
              该租户当前处于禁用状态，系统已暂停服务。
            </p>
          </div>

          <div className="space-y-4">
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-4 text-white/70 text-sm leading-relaxed">
              请联系管理员续费或恢复租户状态。恢复后无需重新登录，刷新即可继续使用。
            </div>

            {message && (
              <div className="rounded-lg border border-orange-500/30 bg-orange-500/10 px-4 py-2 text-sm text-orange-200">
                {message}
              </div>
            )}

            <div className="flex flex-col sm:flex-row gap-3">
              <Button
                onClick={handleRefresh}
                disabled={isChecking}
                className="bg-orange-500 hover:bg-orange-500/90 text-white flex-1"
              >
                <RefreshCw className={`h-4 w-4 mr-2 ${isChecking ? 'animate-spin' : ''}`} />
                刷新状态
              </Button>
              <Button
                variant="outline"
                onClick={() => (window.location.href = '/login')}
                className="border-white/20 text-white/80 hover:bg-white/10 flex-1"
              >
                <Mail className="h-4 w-4 mr-2" />
                返回登录页
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
