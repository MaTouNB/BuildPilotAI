import { useState } from 'react'
import Alert from 'antd/es/alert'
import Button from 'antd/es/button'
import Modal from 'antd/es/modal'
import Tag from 'antd/es/tag'
import { type Page, type Part } from './api'
import { useResource } from './useResource'
export type BuildItem = { slot: string; part_id: string; part_name: string; quantity: number; part_price_id: string | null; price_snapshot_usd: string | null; currency: string | null; source_name: string | null; dataset_updated_at: string | null; subtotal_usd: string | null }
export type Build = { id: string; name: string; purpose: string; budget_amount: string | null; budget_currency: string; version: number; status: string; items: BuildItem[]; total_usd: string; price_is_complete: boolean; build_is_complete: boolean; total_is_complete: boolean; over_budget: boolean; missing_components: string[]; unpriced_slots: string[]; pending_requirements: string[]; checked_rules_status: string }
type Pending = { title: string; before: string; after: string; method: string; path: string; body: Record<string, unknown>; key: string }
const labels: Record<string, string> = { CPU: '处理器', MOTHERBOARD: '主板', MEMORY: '内存', STORAGE: '存储', PSU: '电源', CASE: '机箱', GPU: '显卡', COOLER: '散热器' }
const lastKey = 'buildpilot.last-build-id'
function remembered() { try { const id = localStorage.getItem(lastKey); return id && /^[0-9a-f-]{36}$/i.test(id) ? id : null } catch { return null } }
function remember(id: string | null) { try { if (id) localStorage.setItem(lastKey, id); else localStorage.removeItem(lastKey) } catch { /* Optional navigation memory; server data stays authoritative. */ } }
function itemText(item?: BuildItem) { return item ? `${item.part_name} × ${item.quantity}；${item.price_snapshot_usd === null ? '缺少已选报价' : `USD ${item.price_snapshot_usd} / 数据集最后更新 ${item.dataset_updated_at}`}` : '空槽位' }
function SlotEditor({ build, stage }: { build: Build; stage: (input: Omit<Pending, 'key'>) => void }) {
  const [slot, setSlot] = useState('CPU')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [attempt, setAttempt] = useState(0)
  const parts = useResource<Page<Part>>(`/parts?category=${slot}&q=${encodeURIComponent(q)}&page=${page}&page_size=10`, attempt)
  const old = build.items.find(item => item.slot === slot)
  return <section className="slot-editor"><h3>添加或替换配件</h3><label>装机槽位<select value={slot} onChange={event => { setSlot(event.target.value); setPage(1); setQ('') }}>{Object.entries(labels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
    <form aria-label="槽位配件搜索" key={slot} onSubmit={event => { event.preventDefault(); setQ(String(new FormData(event.currentTarget).get('q') ?? '').trim()); setPage(1) }}><label>配件名称<input name="q" maxLength={120} /></label><Button htmlType="submit">搜索配件</Button></form>
    {parts.status === 'loading' ? <p role="status">正在查询槽位配件…</p> : parts.status === 'error' ? <Alert type="error" title="配件加载失败" action={<Button onClick={() => setAttempt(x => x + 1)}>重试配件</Button>} /> : <>
      {!parts.data.items.length ? <p>该条件下没有已审核配件。可保留空槽位并保存草稿。</p> : <ul className="picker-list">{parts.data.items.map(part => {
        const retained = old?.part_id === part.id
        const priceId = retained ? old.part_price_id : part.price_metadata?.id ?? null
        const price = retained ? old.price_snapshot_usd : part.price_usd
        const date = retained ? old.dataset_updated_at : part.price_metadata?.dataset_updated_at ?? null
        return <li key={part.id}><strong>{part.name}</strong><small>{price === null ? '缺少已选报价，可保存未报价条目' : `USD ${price} 历史参考价 / 数据集最后更新 ${date}`}</small><form aria-label={`选择 ${part.name}`} onSubmit={event => {
          event.preventDefault()
          const quantity = Number(new FormData(event.currentTarget).get('quantity'))
          stage({ title: `确认修改${labels[slot]}`, before: itemText(old), after: `${part.name} × ${quantity}；${price === null ? '缺少已选报价' : `USD ${price} / 数据集最后更新 ${date}`}${retained ? '（保留原快照）' : ''}`, method: 'PUT', path: `/builds/${build.id}/items/${slot}`, body: { part_id: part.id, quantity, expected_price_id: priceId, expected_version: build.version } })
        }}><label>{slot === 'MEMORY' ? '套装数量' : '数量'}<input name="quantity" type="number" min={1} max={99} step={1} defaultValue={retained ? old.quantity : 1} required /></label><Button htmlType="submit">预览修改</Button></form></li>
      })}</ul>}
      <div className="pagination"><Button disabled={page === 1} onClick={() => setPage(x => x - 1)}>配件上一页</Button><span>第 {page} 页</span><Button disabled={page * 10 >= parts.data.total} onClick={() => setPage(x => x + 1)}>配件下一页</Button></div>
    </>}<p className="panel-note">内存按套装计价。只改数量保留原价格快照；替换成不同商品才取新的已选报价。</p>
  </section>
}
function BuildEditor({ id, attempt, stage }: { id: string; attempt: number; stage: (input: Omit<Pending, 'key'>) => void }) {
  const resource = useResource<Build>(`/builds/${id}`, attempt)
  if (resource.status === 'loading') return <p role="status">正在加载装机方案…</p>
  if (resource.status === 'error') return <Alert type="error" title="方案不可用" description="可能已归档、Demo 已关闭或服务暂时不可用。重新加载列表后选择方案。" />
  const build = resource.data
  return <section className="build-editor"><div className="panel-heading"><div><h2>{build.name}</h2><p className="result-meta">版本 {build.version} · 本地 Demo 共享方案</p></div><Button danger onClick={() => stage({ title: '确认归档方案', before: `${build.name}（可编辑）`, after: '归档，退出活动方案列表；数据保留', method: 'DELETE', path: `/builds/${id}`, body: { expected_version: build.version } })}>归档方案</Button></div>
    <div className="build-summary"><h3>{build.total_is_complete ? '完整整机历史参考总价' : '已报价小计'}</h3><strong>USD {build.total_usd}</strong><p>USD 历史参考价 / 数据集最后更新：{[...new Set(build.items.map(item => item.dataset_updated_at).filter(Boolean))].sort().join('、') || '尚无报价快照'}</p><p>按保存的历史价快照计算，不是当前市价。</p><Tag color={build.price_is_complete ? 'success' : 'warning'}>{build.price_is_complete ? '已选条目价格完整' : '条目价格不完整'}</Tag><Tag color={build.build_is_complete ? 'success' : 'warning'}>{build.build_is_complete ? '组件完整' : '组件不完整'}</Tag><p>预算：{build.budget_amount === null ? '未设置' : `USD ${build.budget_amount}`}</p>{build.over_budget && <Alert type="warning" title="已报价小计超出预算；仍可保存草稿" />}
      {build.missing_components.length > 0 && <p>缺少必需组件：{build.missing_components.map(slot => labels[slot]).join('、')}</p>}{build.unpriced_slots.length > 0 && <p>缺价条目：{build.unpriced_slots.map(slot => labels[slot]).join('、')}</p>}{build.pending_requirements.map(message => <p key={message}>{message}</p>)}<p>兼容性：UNKNOWN，尚未执行兼容规则。组件与价格完整也不代表兼容。</p>
    </div>
    <form key={`${build.id}-${build.version}`} className="build-meta" aria-label="方案信息" onSubmit={event => {
      event.preventDefault(); const form = new FormData(event.currentTarget)
      const name = String(form.get('name') ?? '').trim(), purpose = String(form.get('purpose') ?? ''), budget = String(form.get('budget') ?? '') || null
      stage({ title: '确认修改方案信息', before: `${build.name}；用途：${build.purpose || '未填写'}；预算：${build.budget_amount ?? '未设置'} USD`, after: `${name}；用途：${purpose || '未填写'}；预算：${budget ?? '未设置'} USD`, method: 'PATCH', path: `/builds/${id}`, body: { name, purpose, budget_amount: budget, expected_version: build.version } })
    }}><label>方案名称<input name="name" defaultValue={build.name} maxLength={120} required /></label><label>用途<input name="purpose" defaultValue={build.purpose} maxLength={500} /></label><label>预算（USD）<input name="budget" type="number" min="0.01" max="9999999999.99" step="0.01" defaultValue={build.budget_amount ?? ''} /></label><Button htmlType="submit">预览信息修改</Button></form>
    <h3>配件与价格快照</h3><ul className="build-slots">{Object.entries(labels).map(([slot, label]) => {
      const item = build.items.find(item => item.slot === slot)
      return <li key={slot}><h4>{label}</h4><p>{item ? `${item.part_name} × ${item.quantity}` : '尚未选择'}</p>{item && <><p>{item.price_snapshot_usd === null ? '缺少已选报价' : `USD ${item.price_snapshot_usd} × ${item.quantity} = USD ${item.subtotal_usd}`}</p>{item.price_snapshot_usd !== null && <small>USD 历史参考价 / 数据集最后更新 {item.dataset_updated_at}<br />来源：{item.source_name}<br />报价 ID：{item.part_price_id}</small>}<Button danger onClick={() => stage({ title: `确认移除${label}`, before: itemText(item), after: '空槽位', method: 'DELETE', path: `/builds/${id}/items/${slot}`, body: { expected_version: build.version } })}>移除{label}</Button></>}</li>
    })}</ul><SlotEditor key={`${id}:${build.version}`} build={build} stage={stage} />
  </section>
}
export default function BuildWorkspace() {
  const [selected, setSelected] = useState<string | null>(remembered)
  const [attempt, setAttempt] = useState(0)
  const [page, setPage] = useState(1)
  const [pending, setPending] = useState<Pending | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const list = useResource<Page<Build>>(`/builds?page=${page}&page_size=10`, attempt)
  const select = (id: string | null) => { setSelected(id); remember(id) }
  const stage = (input: Omit<Pending, 'key'>) => { setError(''); setPending({ ...input, key: crypto.randomUUID() }) }
  async function confirm() {
    if (!pending || busy) return
    setBusy(true); setError('')
    const controller = new AbortController(), timeout = window.setTimeout(() => controller.abort(), 8000)
    try {
      const response = await fetch('/api/v1' + pending.path, { method: pending.method, headers: { 'Content-Type': 'application/json', 'Idempotency-Key': pending.key }, body: JSON.stringify(pending.body), signal: controller.signal })
      if (!response.ok) {
        if (response.status === 409) { setPending(null); setAttempt(x => x + 1); throw new Error('版本、选价或幂等请求已变化。已重新加载，请重新预览并确认修改。') }
        throw new Error(`保存失败 (${response.status})，请检查 Demo 状态和请求内容。`)
      }
      const build = await response.json() as Build
      select(build.status === 'archived' ? null : build.id)
      setAttempt(x => x + 1); setPending(null)
    } catch (err) { setError(err instanceof Error && err.name !== 'AbortError' ? err.message : '请求超时，结果尚未确认。可重试同一修改，幂等键保持不变。') }
    finally { window.clearTimeout(timeout); setBusy(false) }
  }
  return <main className="catalog-main"><section className="catalog-intro"><p className="eyebrow">M04 / 手动装机</p><h1>保存方案，核对每笔来源。</h1><Alert type="warning" title="本地单用户 Demo · 未启用登录" description="所有本地操作者共享这些方案。仅供本地使用，不代表多用户权限隔离。" /></section>
    {error && <Alert type="error" title={error} />}
    <div className="build-layout"><aside className="filter-panel"><h2>装机方案</h2><Button onClick={() => setAttempt(x => x + 1)}>刷新方案</Button>
      {list.status === 'loading' ? <p role="status">正在加载方案列表…</p> : list.status === 'error' ? <Alert type="error" title="方案列表加载失败" description="检查服务和 DEMO_ENABLED。关闭 Demo 时不能访问方案。" /> : <><ul className="build-list">{list.data.items.map(build => <li key={build.id}><Button type={selected === build.id ? 'primary' : 'default'} onClick={() => select(build.id)}>{build.name}</Button></li>)}</ul>{list.data.total === 0 && <p>尚无方案。先创建一个草稿。</p>}<div className="pagination"><Button disabled={page === 1} onClick={() => setPage(x => x - 1)}>方案上一页</Button><Button disabled={page * 10 >= list.data.total} onClick={() => setPage(x => x + 1)}>方案下一页</Button></div></>}
      <h3>新建草稿</h3><form aria-label="新建方案" className="build-create" onSubmit={event => { event.preventDefault(); const data = new FormData(event.currentTarget); const name = String(data.get('name') ?? '').trim(); stage({ title: '确认新建方案', before: '尚未创建', after: `${name}；空方案，预算待设置`, method: 'POST', path: '/builds', body: { name } }) }}><label>新方案名称<input name="name" maxLength={120} required placeholder="例如：日常办公" /></label><Button htmlType="submit">新建方案</Button></form>
    </aside>{selected ? <BuildEditor id={selected} key={selected} attempt={attempt} stage={stage} /> : <section className="results-panel empty-state"><h2>选择或新建装机方案</h2><p>草稿可缺组件或缺价。保存后刷新仍可继续编辑。</p></section>}</div>
    <Modal title={pending?.title} open={pending !== null} onOk={() => { void confirm() }} onCancel={() => { if (!busy) { setPending(null); setError('') } }} okText="确认并保存" cancelText="取消" confirmLoading={busy} closable={!busy} mask={{ closable: !busy }} okButtonProps={{ 'aria-label': '确认并保存' }} cancelButtonProps={{ disabled: busy, 'aria-label': '取消' }}>
      <div className="diff"><h3>修改前</h3><p>{pending?.before}</p><h3>修改后</h3><p>{pending?.after}</p><p>金额由服务器解析已选报价。确认后仍会校验版本和报价；取消不会写入。</p>{error && <Alert type="error" title={error} />}</div>
    </Modal>
  </main>
}
