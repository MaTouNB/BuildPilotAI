import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import CompatibilityReport, { type Compatibility } from '../CompatibilityReport'
const report: Compatibility = { build_id: 'a', build_version: 1, checked_at: '2026-01-01T00:00:00Z', ruleset_version: 'm08.1', checked_rules_status: 'UNKNOWN', rules: [{ rule_id: 'CPU_SOCKET', title: 'CPU 与主板插槽', status: 'UNKNOWN', evidence: [{ id: 'e', part_id: 'cpu', field_name: 'socket', value: 'AM5', unit: null, source_url: 'https://example.test/spec', source_locator: 'Synthetic locator', retrieved_at: '2026-01-01T00:00:00Z', version: 2 }], compared_values: ['AM5', null], missing_fields: ['MOTHERBOARD.socket'], issues: ['CONFLICT_REVIEW'], explanation: '需审核' }], uncovered_checks: ['BIOS 版本与 CPU 支持列表'] }
it('shows limits, conflict, missing fields and expandable evidence', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => report })))
  render(<CompatibilityReport id="a" version={1} />)
  expect(await screen.findByText('已检查规则：UNKNOWN')).toBeInTheDocument()
  expect(screen.getByText(/缺少可用证据：主板插槽/)).toBeInTheDocument()
  expect(screen.getByText('规格证据冲突，需人工审核')).toBeInTheDocument()
  expect(screen.getByText(/PASS 不代表整机兼容/)).toBeInTheDocument()
  await userEvent.click(screen.getByText(/审核版本 2/))
  expect(screen.getByRole('link', { name: '查看规格来源' })).toHaveAttribute('href', 'https://example.test/spec')
  expect(screen.getByText('BIOS 版本与 CPU 支持列表')).toBeInTheDocument()
})
it('refreshes after a failed request without claiming UNKNOWN or PASS', async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ ok: true, json: async () => ({ ...report, checked_rules_status: 'FAIL' }) })
  vi.stubGlobal('fetch', fetch)
  render(<CompatibilityReport id="a" version={1} />)
  expect(await screen.findByText('兼容报告不可用')).toBeInTheDocument()
  expect(screen.queryByText('已检查规则：UNKNOWN')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '重新检查' }))
  expect(await screen.findByText('已检查规则：FAIL')).toBeInTheDocument()
})
it('ignores late reports after the build version changes', async () => {
  let finish!: (value: unknown) => void
  vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve })).mockResolvedValue({ ok: true, json: async () => ({ ...report, build_version: 2 }) }))
  const view = render(<CompatibilityReport id="a" version={1} />)
  view.rerender(<CompatibilityReport id="a" version={2} />)
  await screen.findByText(/方案版本 2/)
  await act(async () => { finish({ ok: true, json: async () => ({ ...report, checked_rules_status: 'PASS' }) }) })
  expect(screen.queryByText('已检查规则：PASS')).not.toBeInTheDocument()
  expect(screen.getByText(/方案版本 2/)).toBeInTheDocument()
})
