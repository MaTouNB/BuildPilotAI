import { useState } from 'react'
import Button from 'antd/es/button'
import ConfigProvider from 'antd/es/config-provider'
import Tag from 'antd/es/tag'
import Catalog from './Catalog'
import EnvironmentPanel from './EnvironmentPanel'
import './App.css'
export default function App() {
  const [view, setView] = useState<'catalog' | 'environment'>('catalog')
  return <ConfigProvider theme={{ token: { colorPrimary: '#2157b5', borderRadius: 10, fontSize: 16 } }}>
    <div className="page-shell"><header className="topbar"><a className="brand" href="/" aria-label="BuildPilot AI 首页"><span className="brand-mark">B</span>BuildPilot <span>AI</span></a><Tag>单用户 Demo · 未启用登录</Tag></header>
      <nav aria-label="工作区"><Button type={view === 'catalog' ? 'primary' : 'default'} onClick={() => setView('catalog')}>配件目录</Button><Button type={view === 'environment' ? 'primary' : 'default'} onClick={() => setView('environment')}>环境状态</Button></nav>
      {view === 'catalog' ? <Catalog /> : <EnvironmentPanel embedded />}
      <footer>BuildPilot AI <span>历史数据 · 可追溯来源</span></footer>
    </div>
  </ConfigProvider>
}
