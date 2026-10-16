import { useEffect, useRef, useState } from 'react'
import Alert from 'antd/es/alert'
import Button from 'antd/es/button'
import { authenticatedFetch } from './api'
import { CompatibilityEvidence, type Compatibility } from './CompatibilityReport'
type Quote = { part_id: string; name: string; price_usd: string | null; currency?: string; source_name?: string; dataset_updated_at?: string; source_url?: string; source_file?: string; line_number?: number; row_hash?: string; message?: string }
export type ChatResult = { mode: 'mock'; status: string; answer: string; historical_notice: string; persistence: string; compatibility_reports?: Compatibility[]; quotes: Quote[]; candidates: { id: string; name: string }[]; tool_calls: { name: string; arguments: Record<string, unknown>; status: string; result: unknown; duration_ms: number }[] }
export default function CatalogAssistant() {
  const [result, setResult] = useState<ChatResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [question, setQuestion] = useState('查询 RTX 3060 的历史参考价')
  const controller = useRef<AbortController | null>(null)
  const generation = useRef(0)
  useEffect(() => () => { generation.current++; controller.current?.abort() }, [])
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const epoch = ++generation.current
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    setBusy(true); setError(''); setResult(null)
    const timer = window.setTimeout(() => request.abort(), 8000)
    try {
      const response = await authenticatedFetch('/ai/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: question }), signal: request.signal })
      if (!response.ok) {
        const messages: Record<number, string> = { 401: '登录已过期，请重新登录。', 403: '身份校验未通过，请刷新页面后重试。', 413: '问题过长，请缩短后重试。', 422: '问题或工具参数不符合限制，请提供具体型号。', 429: '查询过于频繁或仍有查询运行，请稍后重试。', 502: '模拟模型或工具执行失败，没有生成报价。', 504: '查询超时，没有生成报价，请稍后重试。' }
        throw new Error(messages[response.status] ?? '目录暂不可用，请稍后重试。')
      }
      const data = await response.json() as ChatResult
      if (epoch === generation.current) setResult(data)
    } catch (failure) {
      if (epoch === generation.current) setError(request.signal.aborted ? '查询已取消或超时；没有自动重试。' : failure instanceof Error ? failure.message : '查询失败。')
    } finally {
      window.clearTimeout(timer)
      if (epoch === generation.current) setBusy(false)
    }
  }
  return <aside className="catalog-assistant" aria-label="历史目录助手"><h2>历史目录助手</h2><Alert type="info" title="Mock 模拟模式 · 不连接外部模型" description="模拟模型解析有限型号，实际查询数据库。每次查询独立，本阶段不保存聊天。可查历史目录及本人指定版本方案的兼容规则，不修改方案。" />
    <form onSubmit={submit}><label>配件问题<textarea value={question} onChange={event => setQuestion(event.target.value)} maxLength={1000} required rows={3} /></label><Button type="primary" htmlType="submit" disabled={busy || !question.trim()}>查询历史目录</Button>{busy && <Button onClick={() => controller.current?.abort()}>取消查询</Button>}</form>
    {busy && <p role="status">正在通过模拟模型调用目录工具…</p>}{error && <Alert type="error" title={error} />}
    {result && <QuoteEvidence result={result} />}
  </aside>
}

export function QuoteEvidence({ result }: { result: ChatResult }) { return <div aria-live="polite"><p>{result.answer}</p>{!result.compatibility_reports?.length && <p className="panel-note">{result.historical_notice}</p>}{result.quotes.map((quote, index) => <article key={`${quote.part_id}:${index}`} className="assistant-quote"><h3>{quote.name}</h3>{quote.price_usd === null ? <p>缺少已选报价，金额未知。</p> : <><strong>USD {quote.price_usd}</strong><p>历史参考价 · 数据集最后更新 {quote.dataset_updated_at}</p><p>来源：{quote.source_name}</p><p>{quote.source_file} · 第 {quote.line_number} 行</p>{quote.source_url && <a href={quote.source_url} target="_blank" rel="noreferrer">查看数据集来源</a>}</>}</article>)}
      {result.compatibility_reports?.map((report, index) => <article key={`${report.build_id}:${index}`} className="assistant-quote"><p className="panel-note">方案 {report.build_id} · 保存的检查记录，仅对应版本 {report.build_version}；方案或规格后续变化需重新检查。</p><CompatibilityEvidence report={report} /></article>)}
      {result.status === 'NEEDS_SELECTION' && <ul>{result.candidates.map(part => <li key={part.id}>{part.name}</li>)}</ul>}
      <details><summary>查看实际工具调用（{result.tool_calls.length}）</summary>{result.tool_calls.map((call, index) => <div key={`${index}:${call.name}`}><h4>{index + 1}. {call.name} · {call.status} · {call.duration_ms}ms</h4><pre>{JSON.stringify({ parameters: call.arguments, result: call.result }, null, 2)}</pre></div>)}</details>
    </div> }
