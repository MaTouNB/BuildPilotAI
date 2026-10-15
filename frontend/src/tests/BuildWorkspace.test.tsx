import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import BuildWorkspace, { type Build } from '../BuildWorkspace'
const id = '00000000-0000-4000-8000-000000000010'
const empty: Build = { id, name: 'Test draft', purpose: '', budget_amount: null, budget_currency: 'USD', version: 1, status: 'draft', items: [], total_usd: '0.00', price_is_complete: false, build_is_complete: false, total_is_complete: false, over_budget: false, missing_components: ['CPU', 'MOTHERBOARD', 'MEMORY', 'STORAGE', 'PSU', 'CASE'], unpriced_slots: [], pending_requirements: ['显示输出证据未知'], checked_rules_status: 'UNKNOWN' }
function api(initial?: Build, fail?: 'conflict' | 'network') {
  let build = initial
  let failed = false
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method) {
      if (fail && !failed) { failed = true; if (fail === 'network') throw new Error('network failure'); return { ok: false, status: 409 } }
      const body = JSON.parse(String(options.body)) as { name?: string; quantity?: number }
      if (options.method === 'POST') build = { ...empty, name: body.name! }
      else if (options.method === 'PUT') build = { ...build!, version: build!.version + 1, total_usd: '10.25', price_is_complete: true, items: [{ slot: 'CPU', part_id: 'cpu', part_name: 'Fixture CPU', quantity: body.quantity!, part_price_id: 'price', price_snapshot_usd: '10.25', currency: 'USD', source_name: 'synthetic', dataset_updated_at: '2025-07-23', subtotal_usd: '10.25' }] }
      else if (options.method === 'DELETE') build = { ...build!, status: 'archived' }
      return { ok: true, json: async () => build }
    }
    const data = url.includes('/parts?') ? { items: [{ id: 'cpu', name: 'Fixture CPU', price_usd: '10.25', price_metadata: { id: 'price', dataset_updated_at: '2025-07-23' } }], total: 1 }
      : url.includes('/builds?') ? { items: build && build.status !== 'archived' ? [build] : [], total: build && build.status !== 'archived' ? 1 : 0 }
      : build
    return { ok: true, json: async () => data }
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
beforeEach(() => localStorage.clear())
async function selectBuild() {
  await userEvent.click(await screen.findByRole('button', { name: 'Test draft' }))
  await screen.findByText('已报价小计')
}
describe('manual build workspace', () => {
  it('creates only after explicit confirmation and remembers the saved selection', async () => {
    const fetch = api()
    render(<BuildWorkspace />)
    await userEvent.type(screen.getByLabelText('新方案名称'), 'My draft')
    await userEvent.click(screen.getByRole('button', { name: '新建方案' }))
    expect(screen.getByText('确认新建方案')).toBeInTheDocument()
    expect(fetch.mock.calls.filter(([, options]) => options?.method)).toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: '确认并保存' }))
    expect(await screen.findByRole('heading', { name: 'My draft' })).toBeInTheDocument()
    expect(localStorage.getItem('buildpilot.last-build-id')).toBe(id)
    expect(screen.getByText('条目价格不完整')).toBeInTheDocument()
  })
  it('cancels a diff without writing and then saves an item with a version and quote expectation', async () => {
    const fetch = api(empty)
    render(<BuildWorkspace />)
    await selectBuild()
    await userEvent.click(await screen.findByRole('button', { name: '预览修改' }))
    expect(screen.getByText('修改前')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(fetch.mock.calls.filter(([, options]) => options?.method)).toHaveLength(0)
    await userEvent.click(screen.getByRole('button', { name: '预览修改' }))
    await userEvent.click(screen.getByRole('button', { name: '确认并保存' }))
    expect(await screen.findByText('已选条目价格完整')).toBeInTheDocument()
    expect(screen.getByText('组件不完整')).toBeInTheDocument()
    const request = fetch.mock.calls.find(([, options]) => options?.method === 'PUT')!
    expect(JSON.parse(String(request[1]?.body))).toEqual({ part_id: 'cpu', quantity: 1, expected_price_id: 'price', expected_version: 1 })
    expect(screen.getAllByText(/USD 历史参考价 \/ 数据集最后更新/).length).toBeGreaterThan(0)
  })
  it('handles a conflict by reloading rather than retrying an obsolete mutation', async () => {
    const fetch = api(empty, 'conflict')
    render(<BuildWorkspace />)
    await selectBuild()
    await userEvent.click(await screen.findByRole('button', { name: '预览修改' }))
    await userEvent.click(screen.getByRole('button', { name: '确认并保存' }))
    expect(await screen.findByText(/版本、选价或幂等请求已变化/)).toBeInTheDocument()
    expect(fetch.mock.calls.filter(([, options]) => options?.method)).toHaveLength(1)
  })
  it('retains the exact idempotency key and payload for recovery after a network error', async () => {
    const fetch = api(empty, 'network')
    render(<BuildWorkspace />)
    await selectBuild()
    await userEvent.click(await screen.findByRole('button', { name: '预览修改' }))
    await userEvent.click(screen.getByRole('button', { name: '确认并保存' }))
    await screen.findAllByText('network failure')
    await userEvent.click(screen.getByRole('button', { name: '确认并保存' }))
    await screen.findByText('已选条目价格完整')
    const writes = fetch.mock.calls.filter(([, options]) => options?.method)
    expect(writes).toHaveLength(2)
    expect(writes[0][1]?.body).toBe(writes[1][1]?.body)
    expect(writes[0][1]?.headers).toEqual(writes[1][1]?.headers)
  })
  it('restores a persisted selection and archives after a diff', async () => {
    localStorage.setItem('buildpilot.last-build-id', id)
    const fetch = api(empty)
    render(<BuildWorkspace />)
    await screen.findByText('已报价小计')
    await userEvent.click(screen.getByRole('button', { name: '归档方案' }))
    expect(screen.getByText('确认归档方案')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '确认并保存' }))
    await waitFor(() => expect(localStorage.getItem('buildpilot.last-build-id')).toBeNull())
    expect(await screen.findByText('尚无方案。先创建一个草稿。')).toBeInTheDocument()
    expect(fetch.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(true)
  })
  it('ignores a completed write after the identity workspace unmounts', async () => {
    const base = api(empty)
    let finish!: (value: unknown) => void
    const fetch = vi.fn((url: string, options?: RequestInit) => options?.method === 'PUT'
      ? new Promise(resolve => { finish = resolve }) : base(url, options))
    vi.stubGlobal('fetch', fetch)
    const view = render(<BuildWorkspace />)
    await selectBuild()
    await userEvent.click(await screen.findByRole('button', { name: '预览修改' }))
    await userEvent.click(screen.getByRole('button', { name: '确认并保存' }))
    localStorage.removeItem('buildpilot.last-build-id')
    view.unmount()
    await act(async () => { finish({ ok: true, json: async () => empty }) })
    expect(localStorage.getItem('buildpilot.last-build-id')).toBeNull()
  })

})
