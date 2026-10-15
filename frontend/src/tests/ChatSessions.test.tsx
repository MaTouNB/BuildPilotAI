import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import ChatSessions from '../ChatSessions'
const sid = '00000000-0000-4000-8000-000000000070', rid = '00000000-0000-4000-8000-000000000071'
const session = { id: sid, title: 'Fixture conversation', version: 2, created_at: '2026-10-15T00:00:00Z', updated_at: '2026-10-15T00:00:00Z' }
const result = { mode: 'mock', status: 'GROUNDED', answer: '有证据的历史报价', historical_notice: 'USD 历史参考价', persistence: '已保存', candidates: [], quotes: [{ part_id: 'gpu', name: 'Fixture GPU', price_usd: '20.50', dataset_updated_at: '2025-07-23', source_name: 'synthetic', source_url: 'https://example.test/data', source_file: 'fixture/gpu.csv', line_number: 3 }], tool_calls: [] }
function api(options: { sendFailure?: boolean; streamFailure?: boolean; running?: boolean; createFailure?: boolean; statusFailure?: boolean } = {}) {
  let sessions = [session], messages: unknown[] = [], accepted = false, sendFailed = false, createFailed = false, cancelled = false, statusFailed = false
  const run = () => ({ id: rid, session_id: sid, status: cancelled ? 'CANCELLED' : options.running ? 'RUNNING' : 'COMPLETED', result: options.running ? null : result, error_code: null, last_event_id: 2 })
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const path = url.replace('/api/v1', '')
    if (path === '/ai/sessions' && init?.method === 'POST') {
      if (options.createFailure && !createFailed) { createFailed = true; throw new Error('lost response') }
      return { ok: true, json: async () => session }
    }
    if (path === `/ai/sessions/${sid}/messages` && init?.method === 'POST') {
      accepted = true
      messages = [{ id: 'u', run_id: rid, role: 'user', status: options.running ? 'RUNNING' : 'COMPLETED', content: '查询商品', payload: null }, ...options.running ? [] : [{ id: 'a', run_id: rid, role: 'assistant', status: 'COMPLETED', content: result.answer, payload: result }]]
      if (options.sendFailure && !sendFailed) { sendFailed = true; throw new Error('lost response') }
      return { ok: true, json: async () => ({ run: { ...run(), status: options.running ? 'RUNNING' : sendFailed ? 'COMPLETED' : 'RUNNING' }, session }) }
    }
    if (path.endsWith('/cancel')) { cancelled = true; messages = [{ id: 'u', run_id: rid, role: 'user', status: 'CANCELLED', content: '查询商品', payload: null }]; return { ok: true, json: async () => run() } }
    if (path.endsWith('/events')) {
      if (options.streamFailure) throw new Error('disconnected')
      const terminal = options.running ? '' : `id: 1\nevent: message.delta\ndata: {"run_id":"${rid}","text":"已保存"}\n\nid: 2\nevent: run.completed\ndata: {"run_id":"${rid}"}\n\n`
      return new Response(terminal, { headers: { 'Content-Type': 'text/event-stream' } })
    }
    if (path.endsWith(`/runs/${rid}`)) { if (options.statusFailure && !statusFailed) { statusFailed = true; throw new Error('status offline') }; return { ok: true, json: async () => run() } }
    if (path === `/ai/sessions/${sid}` && init.method === 'DELETE') { sessions = []; return { ok: true, json: async () => ({ status: 'deleted' }) } }
    const data = path.includes('messages?') ? { items: messages, has_older: false, before_cursor: messages.length ? 'u' : null } : path.includes('?page_size') ? { items: sessions, total: sessions.length } : { session, active_run: accepted && options.running && !cancelled ? run() : null, message_count: messages.length }
    return { ok: true, json: async () => data }
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
beforeEach(() => localStorage.clear())
async function open() { await userEvent.click(await screen.findByRole('button', { name: session.title })); await screen.findByRole('button', { name: '发送到当前会话' }) }
it('persists a streamed turn and restores history after remount', async () => {
  const fetch = api()
  let view = render(<ChatSessions storageKey="fixture-a" />)
  await open()
  await userEvent.click(screen.getByRole('button', { name: '发送到当前会话' }))
  expect(await screen.findByText('USD 20.50')).toBeInTheDocument()
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith(`/runs/${rid}`))).toBe(true))
  view.unmount()
  view = render(<ChatSessions storageKey="fixture-a" />)
  expect(await screen.findByText('USD 20.50')).toBeInTheDocument()
  expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
it('retries uncertain sends with the same client request ID', async () => {
  const fetch = api({ sendFailure: true })
  render(<ChatSessions storageKey="fixture-a" />); await open()
  await userEvent.click(screen.getByRole('button', { name: '发送到当前会话' }))
  await screen.findByText(/发送结果不确定/)
  await userEvent.click(await screen.findByRole('button', { name: '发送到当前会话' }))
  const requests = fetch.mock.calls.filter(([url, init]) => url.endsWith('/messages') && init?.method === 'POST')
  expect(requests).toHaveLength(2)
  expect(JSON.parse(String(requests[0][1]!.body)).client_request_id).toBe(JSON.parse(String(requests[1][1]!.body)).client_request_id)
})
it('falls back to saved run status after a disconnected stream without resending', async () => {
  const fetch = api({ streamFailure: true })
  render(<ChatSessions storageKey="fixture-a" />); await open()
  await userEvent.click(screen.getByRole('button', { name: '发送到当前会话' }))
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith(`/runs/${rid}`))).toBe(true))
  expect(await screen.findByText('USD 20.50')).toBeInTheDocument()
  expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
it('previews deletion and changes nothing on cancel', async () => {
  const fetch = api()
  render(<ChatSessions storageKey="fixture-a" />); await open()
  await userEvent.click(screen.getByRole('button', { name: '删除会话' }))
  expect(await screen.findByText(/删除后：立即禁止访问/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '保留会话' }))
  expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
  await userEvent.click(screen.getByRole('button', { name: '删除会话' }))
  await userEvent.click(screen.getByRole('button', { name: '确认删除' }))
  await screen.findByText('当前身份下尚无会话。')
  const request = fetch.mock.calls.find(([, init]) => init?.method === 'DELETE')!
  expect(JSON.parse(String(request[1]!.body))).toEqual({ expected_version: 2 })
})
it('cancels a run without inventing an assistant reply', async () => {
  api({ running: true })
  render(<ChatSessions storageKey="fixture-a" />); await open()
  await userEvent.click(screen.getByRole('button', { name: '发送到当前会话' }))
  await userEvent.click(await screen.findByRole('button', { name: '取消本次运行' }))
  expect(await screen.findByText('你 · 已取消')).toBeInTheDocument()
  expect(screen.queryByText('USD 20.50')).not.toBeInTheDocument()
})
it('does not restore a different identity selection from a late create response', async () => {
  let finish!: (value: unknown) => void
  const fetch = api()
  fetch.mockImplementationOnce(async () => ({ ok: true, json: async () => ({ items: [], total: 0 }) }))
  const view = render(<ChatSessions storageKey="fixture-old" />)
  await screen.findByText('当前身份下尚无会话。')
  fetch.mockImplementationOnce((_url, _options) => new Promise(resolve => { finish = value => resolve(value as Awaited<ReturnType<typeof fetch>>) }))
  await userEvent.click(screen.getByRole('button', { name: '新建私人会话' }))
  view.unmount()
  await act(async () => { finish({ ok: true, json: async () => session }) })
  expect(localStorage.getItem('fixture-old')).toBeNull()
  expect(localStorage.getItem('fixture-new')).toBeNull()
})

it('clears local running state after a failed status lookup and allows refresh recovery', async () => {
  const fetch = api({ streamFailure: true, statusFailure: true })
  render(<ChatSessions storageKey="fixture-a" />); await open()
  await userEvent.click(screen.getByRole('button', { name: '发送到当前会话' }))
  await screen.findByText('USD 20.50')
  await waitFor(() => expect(screen.getByRole('button', { name: '发送到当前会话' })).not.toBeDisabled())
  expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
it('reuses a create request ID after an uncertain response', async () => {
  const fetch = api({ createFailure: true })
  render(<ChatSessions storageKey="fixture-a" />)
  await userEvent.click(screen.getByRole('button', { name: '新建私人会话' }))
  await screen.findByText(/发送结果不确定/)
  await userEvent.click(screen.getByRole('button', { name: '新建私人会话' }))
  await screen.findByRole('button', { name: '发送到当前会话' })
  const requests = fetch.mock.calls.filter(([url, init]) => url.endsWith('/sessions') && init?.method === 'POST')
  expect(requests).toHaveLength(2)
  expect(JSON.parse(String(requests[0][1]!.body)).client_request_id).toBe(JSON.parse(String(requests[1][1]!.body)).client_request_id)
})
