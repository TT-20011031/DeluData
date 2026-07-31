import { RouterProvider } from 'react-router-dom'
import { router } from '@/router'
import { ToastProvider } from '@/components/ui/toast'

export default function App() {
  return (
    <ToastProvider>
      <RouterProvider router={router} />
    </ToastProvider>
  )
}
