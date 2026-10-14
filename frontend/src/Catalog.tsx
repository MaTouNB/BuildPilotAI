import { useEffect, useState } from 'react'
import Alert from 'antd/es/alert'
import Button from 'antd/es/button'
import Drawer from 'antd/es/drawer'
import Tag from 'antd/es/tag'
import { get, type Category, type Detail, type Page, type Part, type Price, type Sources } from './api'

type State<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: T }
function useResource<T>(path: string, attempt = 0): State<T> {
  const key = `${path}:${attempt}`
  const [result, setResult] = useState<{ key: string; value: State<T> } | null>(null)
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 8000)
    void get<T>(path, controller.signal).then(data => {
      if (active) setResult({ key, value: { status: 'ready', data } })
    }).catch(() => {
      if (active) setResult({ key, value: { status: 'error', message: '目录暂时无法加载，请检查服务或稍后重试。' } })
    }).finally(() => window.clearTimeout(timeout))
    return () => { active = false; controller.abort(); window.clearTimeout(timeout) }
  }, [path, key])
  return result?.key === key ? result.value : { status: 'loading' }
}
const labels: Record<string, string> = { CPU: '处理器', GPU: '显卡', MOTHERBOARD: '主板', MEMORY: '内存', STORAGE: '存储', PSU: '电源', CASE: '机箱', COOLER: '散热器' }
const statusLabels: Record<string, string> = { SELECTED: '已选历史价', MISSING: '价格缺失', UNSELECTED: '历史价未选定', CONFLICT_REVIEW: '价格冲突待审' }
function PriceView({ part }: { part: Part }) {
  return <div className="price-block"><strong>{part.price_usd === null ? statusLabels[part.quote_status] : `USD ${part.price_usd}`}</strong>
    <small>USD 历史参考价 / 数据集最后更新 {part.price_metadata?.dataset_updated_at ?? '2025-07-23'}</small>
    {!part.is_quotable && <small>不参与严格预算报价</small>}</div>
}
function Fields({ values }: { values: Record<string, unknown> }) {
  return <dl className="spec-grid">{Object.entries(values).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value === null || value === '' ? '未知 / 未提供' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
}
function PartDetail({ id }: { id: string }) {
  const [attempt, setAttempt] = useState(0)
  const detail = useResource<Detail>(`/parts/${id}`, attempt)
  const [pricePage, setPricePage] = useState(1)
  const prices = useResource<Page<Price>>(`/parts/${id}/prices?page=${pricePage}&page_size=10`, attempt)
  if (detail.status === 'loading') return <p role="status">正在加载商品详情…</p>
  if (detail.status === 'error') return <Alert type="error" title="详情加载失败" description={detail.message} action={<Button onClick={() => setAttempt(x => x + 1)}>重试详情</Button>} />
  const part = detail.data
  const normalized = Object.fromEntries(Object.entries(part.specifications).filter(([key]) => key !== 'source_fields'))
  return <div className="detail-content"><Tag>{labels[part.category]}</Tag><h2>{part.name}</h2><PriceView part={part} />
    <p>未提供的规格保持未知。已审核目录身份不等于完整兼容性认证。</p>
    <h3>规范化规格</h3><Fields values={normalized} />
    <h3>数据质量提示</h3>{part.issues.length ? <ul>{part.issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.field}：{issue.message}</li>)}</ul> : <p>当前展示的源行没有未解决质量提示；这不证明全部规格完整。</p>}
    <h3>原始记录与来源</h3><p>共 {part.source_count} 条映射；最多展示 {part.evidence_limit} 条及其质量提示。</p>
    {part.sources.map((source, index) => <section className="source-panel" key={`${source.row_hash}-${index}`}><p>{source.source_name} · {source.source_file}<br />逻辑记录 {source.row_number} / 原文件行 {source.line_number}–{source.end_line_number}<br />数据集最后更新 {source.dataset_updated_at}</p><Fields values={source.raw_data} /><details><summary>原行 SHA-256</summary><code>{source.row_hash}</code></details></section>)}
    <h3>历史价格记录</h3>{prices.status === 'loading' ? <p role="status">正在加载价格…</p> : prices.status === 'error' ? <Alert type="error" title="历史价格加载失败" action={<Button onClick={() => setAttempt(x => x + 1)}>重试详情</Button>} /> : <><ul className="history">{prices.data.items.map(price => <li key={price.id}>{price.price_usd === null ? '价格缺失' : `USD ${price.price_usd}`} · {price.is_quotable ? '当前选定' : '未选定'}<small>USD 历史参考价 / 数据集最后更新 {price.dataset_updated_at} · {price.source_name}</small></li>)}</ul><div className="pagination"><Button disabled={pricePage === 1} onClick={() => setPricePage(x => x - 1)}>价格上一页</Button><span>第 {pricePage} 页 / {prices.data.total} 条</span><Button disabled={pricePage * 10 >= prices.data.total} onClick={() => setPricePage(x => x + 1)}>价格下一页</Button></div></>}
  </div>
}
export default function Catalog() {
  const [attempt, setAttempt] = useState(0)
  const [filters, setFilters] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const [validation, setValidation] = useState('')
  const categories = useResource<{ items: Category[] }>('/catalog/categories', attempt)
  const sources = useResource<Sources>('/data-sources', attempt)
  const parts = useResource<Page<Part>>(`/parts?page=${page}&page_size=12${filters}`, attempt)
  const retry = () => setAttempt(x => x + 1)
  return <main className="catalog-main"><section className="catalog-intro"><p className="eyebrow">M03 / 硬件目录</p><h1>先看来源，再选配件。</h1><p>检索已审核的硬件记录，核对规格与历史美元价格。</p><Alert type="info" title="USD 历史参考价 / 数据集最后更新 2025-07-23" description="不是实时价或人民币价；该日期是数据集更新时间，不是逐件采价时间。" showIcon /></section>
    <div className="catalog-workspace"><aside className="filter-panel"><h2>筛选目录</h2><form aria-label="目录筛选" onSubmit={event => {
      event.preventDefault()
      const data = new FormData(event.currentTarget)
      const min = String(data.get('min_price_usd') ?? '')
      const max = String(data.get('max_price_usd') ?? '')
      if (min && max && Number(min) > Number(max)) { setValidation('价格下限不能大于上限'); return }
      setValidation('')
      const params = new URLSearchParams()
      for (const [key, value] of data.entries()) if (String(value).trim()) params.set(key, String(value).trim())
      setPage(1); setFilters('&' + params.toString())
    }}>
      <label>名称搜索<input name="q" placeholder="输入型号或名称" maxLength={120} /></label>
      <label>类别<select name="category" defaultValue=""><option value="">全部类别</option>{(categories.status === 'ready' ? categories.data.items : Object.entries(labels).map(([id, name]) => ({ id, name, count: 0 }))).map(item => <option key={item.id} value={item.id}>{item.name}{categories.status === 'ready' ? ` (${item.count})` : ''}</option>)}</select></label>
      <label>历史价格<select name="has_price" defaultValue=""><option value="">不限</option><option value="true">存在历史价</option><option value="false">价格缺失</option></select></label>
      <label>报价状态<select name="quotable" defaultValue=""><option value="">不限</option><option value="true">已选定可报价</option><option value="false">不可报价</option></select></label>
      <fieldset><legend>已选历史价范围（USD）</legend><div className="range"><label>最低<input name="min_price_usd" type="number" min="0.01" max="9999999999.99" step="0.01" /></label><label>最高<input name="max_price_usd" type="number" min="0.01" max="9999999999.99" step="0.01" /></label></div></fieldset>
      <label>排序<select name="sort" defaultValue="name"><option value="name">名称</option><option value="price_asc">已选价格从低到高</option><option value="price_desc">已选价格从高到低</option></select></label>
      {validation && <p role="alert">{validation}</p>}<Button type="primary" htmlType="submit">应用筛选</Button><Button htmlType="reset" onClick={() => { setFilters(''); setPage(1); setValidation('') }}>清除筛选</Button>
    </form><p className="panel-note">有价不等于可报价。未审核源记录不会自动进入目录。</p></aside>
    <section className="results-panel" aria-label="配件结果"><div className="panel-heading"><h2>配件目录</h2><Button onClick={retry}>刷新目录</Button></div>
      {categories.status === 'error' || sources.status === 'error' ? <Alert type="warning" title="部分来源或分类信息加载失败" action={<Button onClick={retry}>重试元信息</Button>} /> : null}
      {sources.status === 'ready' && <p className="result-meta">已审核 {sources.data.reviewed_parts} 个商品 · 已导入 {sources.data.items.reduce((sum, batch) => sum + batch.row_count, 0)} 条源记录（最近最多100批）</p>}
      <div aria-live="polite" aria-busy={parts.status === 'loading'}>{parts.status === 'loading' ? <div className="empty-state" role="status">正在加载配件目录…</div> : parts.status === 'error' ? <Alert type="error" title="目录加载失败" description={parts.message} action={<Button onClick={retry}>重试目录</Button>} /> : <>
        <p className="result-meta">符合条件 {parts.data.total} 个商品 · 第 {page} 页</p>
        {!parts.data.items.length ? <div className="empty-state"><h3>{sources.status === 'ready' && sources.data.reviewed_parts === 0 ? '尚无已审核商品' : '没有符合条件的配件'}</h3><p>{sources.status === 'ready' && sources.data.reviewed_parts === 0 ? '原始数据已保留在 staging。请先显式审核身份和价格来源。' : '调整名称、类别或价格范围后重试。'}</p></div> : <ul className="part-list">{parts.data.items.map(part => <li key={part.id}><div className="part-top"><Tag>{labels[part.category]}</Tag><Tag color={part.is_quotable ? 'success' : 'default'}>{statusLabels[part.quote_status]}</Tag></div><h3>{part.name}</h3><PriceView part={part} /><Button onClick={() => setSelected(part.id)} aria-label={`查看 ${part.name} 详情`}>查看详情</Button></li>)}</ul>}
        <div className="pagination"><Button disabled={page === 1} onClick={() => setPage(x => x - 1)}>上一页</Button><span>第 {page} 页</span><Button disabled={page * 12 >= parts.data.total} onClick={() => setPage(x => x + 1)}>下一页</Button></div>
      </>}</div></section></div>
    <Drawer title="配件详情" open={selected !== null} onClose={() => setSelected(null)} size="large" destroyOnHidden>{selected && <PartDetail id={selected} key={selected} />}</Drawer>
  </main>
}
