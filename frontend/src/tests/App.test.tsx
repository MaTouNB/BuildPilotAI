import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../EnvironmentPanel'

function successfulProbe(path: string) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: path === '/health' ? 'ok' : 'ready' }) })
}

describe('environment dashboard', () => {
  it('shows real probe results and the historical-price boundary', async () => {
    const fetch = vi.fn(successfulProbe)
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    expect(await screen.findByText('API 已连接')).toBeInTheDocument()
    expect(await screen.findByText('数据库已连接')).toBeInTheDocument()
    expect(screen.getByText('USD 历史参考价')).toBeInTheDocument()
    expect(screen.getByText(/单用户 Demo/)).toBeInTheDocument()
    expect(fetch.mock.calls.map(call => call[0])).toEqual(['/health', '/ready'])
  })

  it('distinguishes API liveness from database readiness', async () => {
    vi.stubGlobal('fetch', vi.fn((path: string) => path === '/health'
      ? successfulProbe(path) : Promise.resolve({ ok: false })))
    render(<App />)
    expect(await screen.findByText('API 已连接')).toBeInTheDocument()
    expect(await screen.findByText('数据库未就绪')).toBeInTheDocument()
    expect(screen.getByText('连接尚未完成')).toBeInTheDocument()
  })

  it('allows recovery after a failed connection', async () => {
    const fetch = vi.fn().mockRejectedValue(new Error('offline'))
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    expect(await screen.findByText('API 无法连接')).toBeInTheDocument()
    fetch.mockImplementation(successfulProbe)
    await userEvent.click(screen.getByRole('button', { name: '重新检查' }))
    expect(await screen.findByText('API 已连接')).toBeInTheDocument()
    expect(await screen.findByText('数据库已连接')).toBeInTheDocument()
  })

  it('rejects malformed success responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'wrong' }) }))
    render(<App />)
    expect(await screen.findByText('API 无法连接')).toBeInTheDocument()
  })
})
