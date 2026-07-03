import { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from './lib/api'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import FeedPage from './pages/FeedPage'
import PropertyPage from './pages/PropertyPage'
import ListsPage from './pages/ListsPage'
import AreasPage from './pages/AreasPage'
import SettingsPage from './pages/SettingsPage'
import SystemPage from './pages/SystemPage'
import ConfigPage from './pages/ConfigPage'

export default function App() {
  const [loggedOut, setLoggedOut] = useState(false)

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ username: string }>('/api/auth/me'),
    retry: false,
  })

  useEffect(() => {
    const handler = () => setLoggedOut(true)
    window.addEventListener('hs:unauthorized', handler)
    return () => window.removeEventListener('hs:unauthorized', handler)
  }, [])

  if (me.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
      </div>
    )
  }

  const authed = me.isSuccess && !loggedOut

  if (!authed) {
    return (
      <LoginPage
        onLogin={() => {
          setLoggedOut(false)
          me.refetch()
        }}
      />
    )
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<FeedPage />} />
        <Route path="/property/:id" element={<PropertyPage />} />
        <Route path="/lists" element={<ListsPage />} />
        <Route path="/areas" element={<AreasPage />} />
        <Route path="/chat" element={<Navigate to="/settings?agent=1" replace />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/system" element={<SystemPage />} />
        <Route path="/config" element={<ConfigPage />} />
        <Route path="/account" element={<Navigate to="/config" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}
