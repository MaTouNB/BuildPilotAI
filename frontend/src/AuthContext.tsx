import { useEffect, useRef, useState, type ReactNode } from 'react'
import Alert from 'antd/es/alert'
import Button from 'antd/es/button'
import { authenticatedFetch, setCsrfToken } from './api'
import { AuthContext, useAuth, type AuthState, type Identity } from './auth-state'
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading', identity: null })
  const [attempt, setAttempt] = useState(0)
  const [epoch, setEpoch] = useState(0)
  const generation = useRef(0)
  function update(identity: Identity | null) {
    generation.current += 1
    setCsrfToken(identity?.csrf_token ?? null)
    setEpoch(value => value + 1)
    setState({ status: identity ? 'ready' : 'anonymous', identity })
    // Selection from another identity must never remain displayed or submitted.
    try { localStorage.removeItem('buildpilot.last-build-id') } catch { /* Optional navigation preference. */ }
  }
  useEffect(() => {
    const expire = () => update(null)
    window.addEventListener('buildpilot-auth-expired', expire)
    const version = generation.current
    let active = true
    const controller = new AbortController(), timer = window.setTimeout(() => controller.abort(), 8000)
    void authenticatedFetch('/auth/me', { signal: controller.signal }).then(async response => {
      if (response.status === 401) { if (active && generation.current === version) { setCsrfToken(null); setState({ status: 'anonymous', identity: null }) }; return }
      if (!response.ok) throw new Error('unavailable')
      const identity = await response.json() as Identity
      if (active && generation.current === version) { setCsrfToken(identity.csrf_token); setState({ status: 'ready', identity }) }
    }).catch(() => { if (active && generation.current === version) setState({ status: 'error', identity: null }) }).finally(() => window.clearTimeout(timer))
    return () => { active = false; controller.abort(); window.clearTimeout(timer); window.removeEventListener('buildpilot-auth-expired', expire) }
  }, [attempt])
  async function logout() {
    const response = await authenticatedFetch('/auth/logout', { method: 'POST' })
    if (!response.ok) throw new Error('退出失败，请重试；会话尚未确认撤销。')
    update(null)
  }
  async function switchTenant(id: string) {
    const response = await authenticatedFetch('/auth/select-tenant', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tenant_id: id }) })
    if (!response.ok) throw new Error('无法切换租户，请重新确认成员资格。')
    update(await response.json() as Identity)
  }
  return <AuthContext.Provider value={{ ...state, epoch, update, reload: () => { generation.current += 1; setState({ status: 'loading', identity: null }); setAttempt(value => value + 1) }, logout, switchTenant }}>{children}</AuthContext.Provider>
}
export function AccountBar() {
  const auth = useAuth()
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function run(action: () => Promise<void>) { setBusy(true); setError(''); try { await action() } catch (err) { setError(err instanceof Error ? err.message : '请求失败') } finally { setBusy(false) } }
  if (auth.status !== 'ready' || !auth.identity) return <span>{auth.status === 'loading' ? '正在检查登录状态' : auth.status === 'error' ? '暂时无法确认身份' : '未登录 · 公共目录可浏览'}</span>
  return <div className="account-bar"><span>已登录：{auth.identity.user.username}</span><label>当前租户<select disabled={busy} value={auth.identity.tenant_id} onChange={event => { const id = event.target.value; void run(() => auth.switchTenant(id)) }}>{auth.identity.tenants.map(tenant => <option key={tenant.id} value={tenant.id}>{tenant.name}</option>)}</select></label><Button disabled={busy} onClick={() => { void run(auth.logout) }}>退出登录</Button>{error && <p role="alert">{error}</p>}</div>
}
export function LoginGate({ children }: { children: ReactNode }) {
  const auth = useAuth()
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  if (auth.status === 'loading') return <p role="status">正在确认身份…</p>
  if (auth.status === 'error') return <Alert type="error" title="身份服务暂时不可用" action={<Button onClick={auth.reload}>重新检查身份</Button>} />
  if (auth.status === 'ready') return children
  return <section className="login-panel"><h1>登录装机工作台</h1><p>方案仅对当前租户中的创建者可见。原有 Demo 方案保留原归属。</p><form aria-label="账号登录" onSubmit={async event => {
    event.preventDefault(); if (busy) return
    const form = event.currentTarget, data = new FormData(form)
    setBusy(true); setError('')
    try {
      const response = await authenticatedFetch('/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: String(data.get('username') ?? '').trim().toLowerCase(), password: String(data.get('password') ?? '') }) })
      if (!response.ok) throw new Error(response.status === 429 ? '登录尝试过多，请稍后重试。' : response.status === 401 ? '用户名或密码错误，或账号不可用。' : '登录服务暂时不可用。')
      form.reset(); auth.update(await response.json() as Identity)
    } catch (err) { setError(err instanceof Error ? err.message : '登录失败') } finally { setBusy(false) }
  }}><label>用户名<input name="username" required autoComplete="username" minLength={3} maxLength={64} /></label><label>密码<input name="password" type="password" required autoComplete="current-password" maxLength={128} /></label>{error && <Alert type="error" title={error} />}<Button type="primary" htmlType="submit" aria-label="登录" disabled={busy} loading={busy}>登录</Button></form><p className="panel-note">开发账号由本地初始化命令生成；密码不会写入代码或浏览器持久存储。</p></section>
}
