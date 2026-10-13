import { useEffect, useState } from 'react'
import Alert from 'antd/es/alert'
import Button from 'antd/es/button'
import ConfigProvider from 'antd/es/config-provider'
import Tag from 'antd/es/tag'
import './App.css'

type Probe = { state: 'loading' | 'ok' | 'error'; label: string }
type Status = { api: Probe; database: Probe }
const loading: Status = {
  api: { state: 'loading', label: '正在检查' },
  database: { state: 'loading', label: '正在检查' },
}

async function probe(path: string, kind: 'api' | 'database', signal: AbortSignal): Promise<Probe> {
  try {
    const response = await fetch(path, { signal })
    if (!response.ok) throw new Error('Service unavailable')
    const body: unknown = await response.json()
    if (typeof body !== 'object' || body === null || !('status' in body)) {
      throw new Error('Invalid response')
    }
    if (kind === 'api' ? body.status !== 'ok' : body.status !== 'ready') {
      throw new Error('Unexpected status')
    }
    return { state: 'ok', label: kind === 'api' ? 'API 已连接' : '数据库已连接' }
  } catch {
    return { state: 'error', label: kind === 'api' ? 'API 无法连接' : '数据库未就绪' }
  }
}

function StatusRow({ title, value }: { title: string; value: Probe }) {
  return <div className="status-row">
    <span>{title}</span>
    <Tag color={value.state === 'ok' ? 'success' : value.state === 'error' ? 'error' : 'default'}>
      {value.label}
    </Tag>
  </div>
}

export default function App() {
  const [status, setStatus] = useState<Status>(loading)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 6000)
    let active = true
    void Promise.all([
      probe('/health', 'api', controller.signal),
      probe('/ready', 'database', controller.signal),
    ]).then(([api, database]) => {
      if (active) setStatus({ api, database })
    }).finally(() => window.clearTimeout(timeout))
    return () => { active = false; controller.abort(); window.clearTimeout(timeout) }
  }, [attempt])

  const checking = status.api.state === 'loading'
  return <ConfigProvider theme={{ token: { colorPrimary: '#2157b5', borderRadius: 10, fontSize: 16 } }}>
    <div className="page-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="BuildPilot AI 首页"><span className="brand-mark">B</span>BuildPilot <span>AI</span></a>
        <Tag>单用户 Demo · 未启用登录</Tag>
      </header>
      <main>
        <section className="intro">
          <p className="eyebrow">M00 / 工程基座</p>
          <h1>从可靠的数据，<br />开始装机。</h1>
          <p className="intro-copy">先连接应用与数据库，再逐步构建硬件目录、装机工作台和 AI 助手。</p>
        </section>
        <section className="workspace" aria-labelledby="status-title">
          <div className="panel-heading"><div><p className="eyebrow">开发环境</p><h2 id="status-title">连接状态</h2></div>
            <Button aria-label="重新检查" disabled={checking} loading={checking} onClick={() => { setStatus(loading); setAttempt(value => value + 1) }}>重新检查</Button>
          </div>
          <div aria-live="polite" aria-busy={checking}>
            <StatusRow title="FastAPI 服务" value={status.api} />
            <StatusRow title="PostgreSQL 数据库" value={status.database} />
          </div>
          {status.api.state === 'error' || status.database.state === 'error'
            ? <Alert type="warning" showIcon title="连接尚未完成" description="检查后端和数据库是否启动，然后重新检查。" />
            : null}
          <p className="panel-note">当前阶段仅提供环境检查。配件搜索、装机编辑和 AI 功能将在后续阶段加入。</p>
        </section>
        <section className="data-note" aria-label="数据说明"><span className="note-label">数据边界</span>
          <p>后续报价使用 <strong>USD 历史参考价</strong>。数据集最后更新于 2025-07-23，不代表每件商品的采价日期，也不代表当前市场价格。</p>
        </section>
      </main>
      <footer>BuildPilot AI <span>React · FastAPI · PostgreSQL</span></footer>
    </div>
  </ConfigProvider>
}
