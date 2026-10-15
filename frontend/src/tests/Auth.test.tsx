import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AccountBar, AuthProvider, LoginGate } from '../AuthContext'
import { useAuth, type Identity } from '../auth-state'
import { authenticatedFetch, setCsrfToken } from '../api'
const identity: Identity = { user: { id: 'alice', username: 'alice' }, tenant_id: 'a', tenants: [{ id: 'a', name: 'Tenant A', role: 'member' }, { id: 'b', name: 'Tenant B', role: 'member' }], csrf_token: 'csrf-a' }
function Private() { const auth = useAuth(); return <p>私人工作台 {auth.identity?.tenant_id}</p> }
function App() { return <AuthProvider><AccountBar /><LoginGate><Private /></LoginGate></AuthProvider> }
beforeEach(() => { localStorage.clear(); setCsrfToken(null) })
describe('session and route protection', () => {
  it('requires login and never stores credentials or tokens in localStorage', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/me') ? { ok: false, status: 401 } : { ok: true, json: async () => identity }))
    render(<App />)
    expect(await screen.findByText('登录装机工作台')).toBeInTheDocument()
    expect(screen.queryByText(/私人工作台/)).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('用户名'), 'alice')
    await userEvent.type(screen.getByLabelText('密码'), 'synthetic-password')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('私人工作台 a')).toBeInTheDocument()
    expect(localStorage.length).toBe(0)
  })
  it('unmounts private content and clears selection after logout', async () => {
    localStorage.setItem('buildpilot.last-build-id', 'previous')
    const fetch = vi.fn(async (url: string, _options?: RequestInit) => ({ ok: true, json: async () => url.endsWith('/me') ? identity : { status: 'signed_out' } }))
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    await screen.findByText('私人工作台 a')
    await userEvent.click(screen.getByRole('button', { name: '退出登录' }))
    expect(await screen.findByText('登录装机工作台')).toBeInTheDocument()
    expect(localStorage.getItem('buildpilot.last-build-id')).toBeNull()
    const options = fetch.mock.calls.find(([url]) => url.endsWith('/logout'))![1] as RequestInit
    expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('csrf-a')
  })
  it('clears the selected build and rotates csrf on tenant change', async () => {
    localStorage.setItem('buildpilot.last-build-id', 'previous')
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/select-tenant') ? { ...identity, tenant_id: 'b', csrf_token: 'csrf-b' } : identity })))
    render(<App />)
    await screen.findByText('私人工作台 a')
    await userEvent.selectOptions(screen.getByLabelText('当前租户'), 'b')
    expect(await screen.findByText('私人工作台 b')).toBeInTheDocument()
    expect(localStorage.getItem('buildpilot.last-build-id')).toBeNull()
  })
  it('removes private content on a server 401', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/me') ? { ok: true, json: async () => identity } : { ok: false, status: 401 }))
    render(<App />)
    await screen.findByText('私人工作台 a')
    await authenticatedFetch('/builds')
    expect(await screen.findByText('登录装机工作台')).toBeInTheDocument()
  })
  it('shows invalid login without opening the private workspace', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 401 })))
    render(<App />)
    await screen.findByText('登录装机工作台')
    await userEvent.type(screen.getByLabelText('用户名'), 'alice')
    await userEvent.type(screen.getByLabelText('密码'), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('用户名或密码错误，或账号不可用。')).toBeInTheDocument()
    expect(screen.queryByText(/私人工作台/)).not.toBeInTheDocument()
  })
  it('does not treat an unavailable auth service as signed in', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    render(<App />)
    expect(await screen.findByText('身份服务暂时不可用')).toBeInTheDocument()
    expect(screen.queryByText(/私人工作台/)).not.toBeInTheDocument()
  })
  it('ignores a stale 401 from a previous identity', async () => {
    let resolve!: (response: unknown) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise(done => { resolve = done })))
    setCsrfToken('old')
    const pending = authenticatedFetch('/builds')
    setCsrfToken('new')
    const expire = vi.fn()
    window.addEventListener('buildpilot-auth-expired', expire)
    resolve({ status: 401 })
    await pending
    expect(expire).not.toHaveBeenCalled()
    window.removeEventListener('buildpilot-auth-expired', expire)
  })
})
