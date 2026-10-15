import { lazy, Suspense, useState } from 'react'
import Button from 'antd/es/button'
import ConfigProvider from 'antd/es/config-provider'
import { AuthProvider, AccountBar, LoginGate } from './AuthContext'
import Catalog from './Catalog'
import { useAuth } from './auth-state'
const BuildWorkspace = lazy(() => import('./BuildWorkspace'))
import EnvironmentPanel from './EnvironmentPanel'
import './App.css'
type View = 'catalog' | 'builds' | 'environment'
function initialView(): View {
  try { const value = localStorage.getItem('buildpilot.view'); return value === 'builds' || value === 'environment' ? value : 'catalog' } catch { return 'catalog' }
}
function WorkspaceApp() {
  const auth = useAuth()
  const [view, setView] = useState<View>(initialView)
  const navigate = (value: View) => { setView(value); try { localStorage.setItem('buildpilot.view', value) } catch { /* Optional UI preference. */ } }
  return <ConfigProvider theme={{ token: { colorPrimary: '#2157b5', borderRadius: 10, fontSize: 16 } }}>
    <div className="page-shell"><header className="topbar"><a className="brand" href="/" aria-label="BuildPilot AI 首页"><span className="brand-mark">B</span>BuildPilot <span>AI</span></a><AccountBar /></header>
      <nav aria-label="工作区"><Button type={view === 'catalog' ? 'primary' : 'default'} onClick={() => navigate('catalog')}>配件目录</Button><Button type={view === 'builds' ? 'primary' : 'default'} onClick={() => navigate('builds')}>装机工作台</Button><Button type={view === 'environment' ? 'primary' : 'default'} onClick={() => navigate('environment')}>环境状态</Button></nav>
      {view === 'catalog' ? <Catalog /> : view === 'builds' ? <LoginGate><Suspense fallback={<p role="status">正在加载装机工作台…</p>}><BuildWorkspace key={auth.epoch} /></Suspense></LoginGate> : <EnvironmentPanel embedded />}
      <footer>BuildPilot AI <span>历史数据 · 可追溯来源</span></footer>
    </div>
  </ConfigProvider>
}

export default function App() { return <AuthProvider><WorkspaceApp /></AuthProvider> }
