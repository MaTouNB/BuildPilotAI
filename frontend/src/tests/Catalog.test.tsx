import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import Catalog from '../Catalog'
import type { Part } from '../api'
const part: Part = { id: 'part1', name: 'Fixture CPU', category: 'CPU', quality_status: 'REVIEW', has_price: true, is_quotable: true, quote_status: 'SELECTED', price_usd: '10.25', price_metadata: { id: 'price1', price_usd: '10.25', dataset_updated_at: '2025-07-23', source_name: 'synthetic', quote_status: 'REVIEWED_SELECTION', is_quotable: true } }
function mockApi(overrides?: (url: string) => unknown) {
  const fetch = vi.fn(async (url: string) => {
    const custom = overrides?.(url)
    if (custom instanceof Error) throw custom
    const data = custom ?? (url.includes('/catalog/categories') ? { items: [{ id: 'CPU', name: '处理器', count: 1 }] }
      : url.includes('/data-sources') ? { reviewed_parts: 1, items: [{ name: 'synthetic', row_count: 3, dataset_updated_at: '2025-07-23' }] }
      : url.endsWith('/prices?page=1&page_size=10') ? { items: [part.price_metadata], total: 1, page: 1, page_size: 10 }
      : url.endsWith('/parts/part1') ? { ...part, specifications: { socket: null, source_fields: {} }, sources: [{ source_file: 'cpu.csv', line_number: 2, end_line_number: 2, row_number: 1, source_name: 'synthetic', dataset_updated_at: '2025-07-23', row_hash: 'abc', raw_data: { name: 'original' } }], source_count: 1, evidence_limit: 100, issues: [] }
      : { items: [part], total: 1, page: 1, page_size: 12 })
    return { ok: true, json: async () => data }
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
describe('reviewed catalog', () => {
  it('loads then shows decimal source prices and detail evidence', async () => {
    mockApi()
    render(<Catalog />)
    expect(screen.getByText('正在加载配件目录…')).toBeInTheDocument()
    expect(await screen.findByText('Fixture CPU')).toBeInTheDocument()
    expect(screen.getByText('USD 10.25')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '查看 Fixture CPU 详情' }))
    expect(await screen.findByText('规范化规格')).toBeInTheDocument()
    expect(screen.getByText('未知 / 未提供')).toBeInTheDocument()
    expect(screen.getByText('original')).toBeInTheDocument()
    expect(screen.getAllByText(/USD 历史参考价 \/ 数据集最后更新 2025-07-23/).length).toBeGreaterThan(1)
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByText('规范化规格')).not.toBeInTheDocument())
  })
  it('applies encoded filters and clears them', async () => {
    const fetch = mockApi()
    render(<Catalog />)
    await screen.findByText('Fixture CPU')
    await userEvent.type(screen.getByLabelText('名称搜索'), "%' OR")
    await userEvent.selectOptions(screen.getByLabelText('类别'), 'CPU')
    await userEvent.selectOptions(screen.getByLabelText('报价状态'), 'true')
    await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes('q=%25%27+OR') && url.includes('category=CPU') && url.includes('quotable=true'))).toBe(true))
    await userEvent.click(screen.getByRole('button', { name: '清除筛选' }))
    expect(screen.getByLabelText('名称搜索')).toHaveValue('')
    await waitFor(() => expect(fetch.mock.calls.at(-1)?.[0]).toBe('/api/v1/parts?page=1&page_size=12'))
  })
  it('explains an empty reviewed catalog', async () => {
    mockApi(url => url.includes('/data-sources') ? { reviewed_parts: 0, items: [] } : url.includes('/parts?') ? { items: [], total: 0, page: 1, page_size: 12 } : undefined)
    render(<Catalog />)
    expect(await screen.findByText('尚无已审核商品')).toBeInTheDocument()
    expect(screen.getByText(/原始数据已保留在 staging/)).toBeInTheDocument()
  })
  it('recovers list errors', async () => {
    let failure = true
    mockApi(url => url.includes('/parts?') && failure ? new Error('offline') : undefined)
    render(<Catalog />)
    expect(await screen.findByText('目录加载失败')).toBeInTheDocument()
    failure = false
    await userEvent.click(screen.getByRole('button', { name: '重试目录' }))
    expect(await screen.findByText('Fixture CPU')).toBeInTheDocument()
  })
  it('validates price bounds before issuing a query', async () => {
    const fetch = mockApi()
    render(<Catalog />)
    await screen.findByText('Fixture CPU')
    await userEvent.type(screen.getByLabelText('最低'), '20')
    await userEvent.type(screen.getByLabelText('最高'), '10')
    await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    expect(screen.getByText('价格下限不能大于上限')).toHaveAttribute('role', 'alert')
    expect(fetch.mock.calls.filter(([url]) => url.includes('/parts?'))).toHaveLength(1)
  })
  it('changes result pages and disables paging past the end', async () => {
    const fetch = mockApi(url => url.includes('/parts?') ? { items: [part], total: 13, page: 1, page_size: 12 } : undefined)
    render(<Catalog />)
    await screen.findByText('Fixture CPU')
    await userEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes('page=2&'))).toBe(true))
    await waitFor(() => expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled())
    expect(screen.getByRole('button', { name: '上一页' })).toBeEnabled()
  })
})
