import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import CatalogAssistant, { type ChatResult } from '../CatalogAssistant'
const quote: ChatResult = { mode: 'mock', status: 'GROUNDED', answer: '真实工具证据', historical_notice: 'USD 历史参考价；数据集日期不是逐商品当天采价。', persistence: '不保存聊天', candidates: [], quotes: [{ part_id: 'gpu', name: 'Fixture GPU', price_usd: '20.50', currency: 'USD', source_name: 'synthetic', dataset_updated_at: '2025-07-23', source_url: 'https://example.test/data', source_file: 'fixture/gpu.csv', line_number: 3 }], tool_calls: [{ name: 'get_reference_price', arguments: { part_id: 'gpu' }, result: { price_usd: '20.50' }, status: 'ok', duration_ms: 2 }] }
it('shows mock mode and real server quote/source only after querying', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => quote }))
  vi.stubGlobal('fetch', fetch)
  render(<CatalogAssistant />)
  expect(screen.getByText('Mock 模拟模式 · 不连接外部模型')).toBeInTheDocument()
  expect(screen.queryByText('USD 20.50')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '查询历史目录' }))
  expect(await screen.findByText('USD 20.50')).toBeInTheDocument()
  expect(screen.getByText(/数据集最后更新 2025-07-23/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '查看数据集来源' })).toHaveAttribute('href', 'https://example.test/data')
  await userEvent.click(screen.getByText('查看实际工具调用（1）'))
  expect(screen.getByText(/get_reference_price/)).toBeInTheDocument()
  expect(fetch.mock.calls).toHaveLength(1)
})
it.each([[403, '身份校验未通过，请刷新页面后重试。'], [429, '查询过于频繁或仍有查询运行，请稍后重试。'], [502, '模拟模型或工具执行失败，没有生成报价。'], [504, '查询超时，没有生成报价，请稍后重试。']])('shows failure %s without retaining a prior quote', async (status, message) => {
  const fetch = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => quote }).mockResolvedValue({ ok: false, status })
  vi.stubGlobal('fetch', fetch)
  render(<CatalogAssistant />)
  await userEvent.click(screen.getByRole('button', { name: '查询历史目录' }))
  await screen.findByText('USD 20.50')
  await userEvent.click(screen.getByRole('button', { name: '查询历史目录' }))
  await screen.findByText(String(message))
  expect(screen.queryByText('USD 20.50')).not.toBeInTheDocument()
})
it('does not invent a zero price for an unquotable part', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ ...quote, status: 'UNQUOTABLE', quotes: [{ part_id: 'cpu', name: 'Missing CPU', price_usd: null }] }) })))
  render(<CatalogAssistant />)
  await userEvent.click(screen.getByRole('button', { name: '查询历史目录' }))
  expect(await screen.findByText('缺少已选报价，金额未知。')).toBeInTheDocument()
})
it('aborts and ignores results after the identity-bound component unmounts', async () => {
  let finish!: (response: unknown) => void
  const fetch = vi.fn((_path: string, _options: RequestInit) => new Promise(resolve => { finish = resolve }))
  vi.stubGlobal('fetch', fetch)
  const view = render(<CatalogAssistant />)
  await userEvent.click(screen.getByRole('button', { name: '查询历史目录' }))
  const signal = fetch.mock.calls[0][1].signal!
  view.unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { finish({ ok: true, json: async () => quote }) })
  expect(screen.queryByText('USD 20.50')).not.toBeInTheDocument()
})
it('cancels without automatically retrying', async () => {
  vi.stubGlobal('fetch', vi.fn((_path, options: RequestInit) => new Promise((_resolve, reject) => { options.signal!.addEventListener('abort', () => reject(new Error('cancelled'))) })))
  render(<CatalogAssistant />)
  await userEvent.click(screen.getByRole('button', { name: '查询历史目录' }))
  await userEvent.click(screen.getByRole('button', { name: '取消查询' }))
  expect(await screen.findByText('查询已取消或超时；没有自动重试。')).toBeInTheDocument()
})
