import { useCallback, useEffect, useRef, useState } from 'react'
import Alert from 'antd/es/alert'
import Button from 'antd/es/button'
import Modal from 'antd/es/modal'
import { authenticatedFetch, type Page } from './api'
import { QuoteEvidence, type ChatResult } from './CatalogAssistant'
import { readEvents } from './chat-stream'
import { useResource } from './useResource'
export type ChatSession = { id: string; title: string; version: number; created_at: string; updated_at: string }
export type Run = { id: string; session_id: string; status: 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'; result: ChatResult | null; error_code: string | null; last_event_id: number }
type Snapshot = { session: ChatSession; active_run: Run | null; message_count: number }
type History = { items: { id: string; run_id: string; role: string; status: Run['status']; content: string; payload: ChatResult | null }[]; has_older: boolean; before_cursor: string | null }
const base = '/ai/sessions'
const labels = { RUNNING: '处理中', COMPLETED: '已完成', FAILED: '失败，未生成回答', CANCELLED: '已取消' }
function failure(status: number) { return status === 401 ? '登录已过期，请重新登录。' : status === 403 ? '身份校验失败，请刷新页面。' : status === 404 ? '会话不可用，可能已删除或无权访问。' : status === 409 ? '会话已有运行、请求冲突或版本变化，请刷新后核对。' : status === 429 ? '已达到运行或会话上限，请稍后重试。' : '服务暂不可用。发送结果不确定时请刷新；重试相同问题会沿用请求标识。' }
async function rpc<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try { response = await authenticatedFetch(path, options) }
  catch { throw new Error(options.signal?.aborted ? '请求已取消或超时，请刷新查看结果；不会自动重发。' : failure(0)) }
  if (!response.ok) throw new Error(failure(response.status))
  try { return await response.json() as T } catch { throw new Error('服务返回无效结果，请刷新查看已保存状态。') }
}
const json = (method: string, body: unknown, signal?: AbortSignal): RequestInit => ({ method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, signal })
function remembered(key: string) { try { const value = localStorage.getItem(key); return value && /^[0-9a-f-]{36}$/i.test(value) ? value : null } catch { return null } }
function remember(key: string, value: string | null) { try { if (value) localStorage.setItem(key, value); else localStorage.removeItem(key) } catch { /* Only navigation preference. */ } }

function SessionThread({ id, attempt, refresh, remove }: { id: string; attempt: number; refresh: () => void; remove: (snapshot: Snapshot) => void }) {
  const snapshot = useResource<Snapshot>(`${base}/${id}`, attempt)
  const [cursor, setCursor] = useState<string | null>(null), [cursors, setCursors] = useState<(string | null)[]>([])
  const history = useResource<History>(`${base}/${id}/messages?limit=20${cursor ? `&before=${cursor}` : ''}`, attempt)
  const [question, setQuestion] = useState('查询 RTX 3060 的历史参考价')
  const [error, setError] = useState(''), [progress, setProgress] = useState(''), [sending, setSending] = useState(false)
  const [localRun, setLocalRun] = useState<string | null>(null)
  const activeId = localRun ?? (snapshot.status === 'ready' ? snapshot.data.active_run?.id ?? null : null)
  const alive = useRef(true), action = useRef<AbortController | null>(null)
  const pending = useRef<{ message: string; key: string } | null>(null)
  useEffect(() => { alive.current = true; return () => { alive.current = false; action.current?.abort() } }, [])
  useEffect(() => {
    if (!activeId) return
    let active = true
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 70000)
    const path = `${base}/${id}/runs/${activeId}`
    void (async () => {
      try {
        try {
          const response = await authenticatedFetch(`${path}/events`, { signal: controller.signal })
          if (!response.ok) throw new Error(failure(response.status))
          await readEvents(response, activeId, event => {
            if (!active) return
            if (event.kind === 'tool.started') setProgress('正在执行检查工具…')
            else if (event.kind === 'tool.completed') setProgress('已取得工具结果，等待保存回答…')
            else if (event.kind === 'message.delta') setProgress('回答已保存，正在加载历史…')
          })
        } catch { if (controller.signal.aborted) return; if (active) setProgress('事件连接中断，正在查询已保存状态…') }
        while (active && !controller.signal.aborted) {
          const status = await rpc<Run>(path, { signal: controller.signal })
          if (status.status !== 'RUNNING') {
            if (active) { setLocalRun(null); setProgress(''); setError(status.status === 'FAILED' ? '本次运行失败，未生成完整回答。可以重新提问。' : ''); refresh() }
            return
          }
          await new Promise<void>(resolve => { const done = () => { window.clearTimeout(t); controller.signal.removeEventListener('abort', done); resolve() }; const t = window.setTimeout(done, 500); controller.signal.addEventListener('abort', done, { once: true }); if (controller.signal.aborted) done() })
        }
      } catch (err) { if (active && !controller.signal.aborted) { setLocalRun(null); setProgress(''); setError(err instanceof Error ? err.message : '连接中断，请刷新查看状态。'); refresh() } }
      finally { window.clearTimeout(timer); if (active && controller.signal.aborted) { setLocalRun(null); setProgress(''); setError('暂时无法确认运行状态，请刷新；不会自动重发问题。'); refresh() } }
    })()
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [activeId, id, refresh])
  async function send(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault(); if (sending || activeId || !question.trim()) return
    const message = question.trim()
    if (pending.current?.message !== message) pending.current = { message, key: crypto.randomUUID() }
    const controller = new AbortController(); action.current = controller
    const timer = window.setTimeout(() => controller.abort(), 8000)
    setSending(true); setError('')
    try {
      const accepted = await rpc<{ run: Run }>(`${base}/${id}/messages`, json('POST', { message, client_request_id: pending.current.key }, controller.signal))
      if (!alive.current) return
      pending.current = null; setCursor(null); setCursors([])
      if (accepted.run.status === 'RUNNING') setLocalRun(accepted.run.id)
      else setError(accepted.run.status === 'FAILED' ? '这次请求之前已失败；修改问题后可发起新请求。' : '')
      refresh()
    } catch (err) { if (alive.current) { setError(err instanceof Error ? err.message : '发送失败，请刷新查看状态。'); refresh() } }
    finally { window.clearTimeout(timer); if (alive.current) setSending(false) }
  }
  async function cancel() {
    if (!activeId) return
    try { await rpc(`${base}/${id}/runs/${activeId}/cancel`, { method: 'POST' }); if (alive.current) { setLocalRun(null); refresh() } }
    catch (err) { if (alive.current) setError(err instanceof Error ? err.message : '取消失败，请刷新查看状态。') }
  }
  if (snapshot.status === 'error' || history.status === 'error') return <Alert type="error" title="会话不可用" description="可能已删除、无权访问或服务暂不可用。" />
  if (snapshot.status !== 'ready' || history.status !== 'ready') return <p role="status">正在加载私人会话…</p>
  return <section aria-label="当前私人会话"><div className="panel-heading"><h3>{snapshot.data.session.title}</h3><Button danger onClick={() => remove(snapshot.data)}>删除会话</Button></div>
    <p className="panel-note">显示已保存历史；上下文只保留当前会话最近商品和最近六个问题。支持追问「它的历史参考价」。兼容检查请从装机工作台复制带方案与版本的问题。</p>
    <div className="chat-history">{history.data.items.length === 0 ? <p>还没有消息。可先查询一款配件。</p> : history.data.items.map(message => <article className={`chat-message chat-${message.role}`} key={message.id}><h4>{message.role === 'user' ? '你' : '模拟助手'} · {labels[message.status]}</h4>{message.payload ? <QuoteEvidence result={message.payload} /> : <p>{message.content}</p>}</article>)}</div>
    <div className="pagination"><Button disabled={!history.data.has_older} onClick={() => { setCursors(items => [...items, cursor]); setCursor(history.data.before_cursor) }}>更早消息</Button><Button disabled={!cursors.length} onClick={() => { setCursor(cursors.at(-1) ?? null); setCursors(items => items.slice(0, -1)) }}>较新消息</Button></div>
    <form onSubmit={send}><label htmlFor={`question-${id}`}>会话问题</label><textarea id={`question-${id}`} value={question} onChange={event => setQuestion(event.target.value)} maxLength={1000} required rows={3} /><Button type="primary" htmlType="submit" disabled={sending || Boolean(activeId) || !question.trim()}>发送到当前会话</Button>{activeId && <Button onClick={() => { void cancel() }}>取消本次运行</Button>}</form>
    {activeId && <p role="status">{progress || '正在读取运行事件…'}</p>}{error && <Alert type="error" title={error} />}
  </section>
}

export default function ChatSessions({ storageKey }: { storageKey: string }) {
  const [selected, setSelected] = useState<string | null>(() => remembered(storageKey))
  const [attempt, setAttempt] = useState(0), [title, setTitle] = useState('新会话')
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [deletion, setDeletion] = useState<Snapshot | null>(null)
  const sessions = useResource<Page<ChatSession>>(`${base}?page_size=30`, attempt)
  const alive = useRef(true), controller = useRef<AbortController | null>(null)
  const createRequest = useRef<{ title: string; key: string } | null>(null)
  useEffect(() => { alive.current = true; return () => { alive.current = false; controller.current?.abort() } }, [])
  // Stable callback keeps a running event stream attached across ordinary renders.
  const refresh = useCallback(() => setAttempt(value => value + 1), [])
  function select(value: string | null) { setSelected(value); remember(storageKey, value) }
  async function create(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy || !title.trim()) return
    if (createRequest.current?.title !== title.trim()) createRequest.current = { title: title.trim(), key: crypto.randomUUID() }
    controller.current = new AbortController(); const signal = controller.current.signal
    const timer = window.setTimeout(() => controller.current?.abort(), 8000)
    setBusy(true); setError('')
    try {
      const value = await rpc<ChatSession>(base, json('POST', { title: createRequest.current.title, client_request_id: createRequest.current.key }, signal))
      if (alive.current) { createRequest.current = null; select(value.id); setAttempt(value => value + 1) }
    } catch (err) { if (alive.current) setError(err instanceof Error ? err.message : '创建失败。') }
    finally { window.clearTimeout(timer); if (alive.current) setBusy(false) }
  }
  async function confirmDelete() {
    if (!deletion || busy) return
    setBusy(true); setError(''); controller.current = new AbortController()
    const timer = window.setTimeout(() => controller.current?.abort(), 8000)
    try {
      await rpc(`${base}/${deletion.session.id}`, json('DELETE', { expected_version: deletion.session.version }, controller.current.signal))
      if (alive.current) { setDeletion(null); select(null); setAttempt(value => value + 1) }
    } catch (err) { if (alive.current) { setDeletion(null); setError(err instanceof Error ? err.message : '删除失败。'); setAttempt(value => value + 1) } }
    finally { window.clearTimeout(timer); if (alive.current) setBusy(false) }
  }
  return <aside className="catalog-assistant persistent-chat" aria-label="私人会话助手"><h2>私人会话助手</h2><Alert type="info" title="Mock 模拟模式 · 会话已持久化" description="聊天保存到当前账号与租户，仅自己可见。断线不会重发；可刷新查看结果。可查历史目录及本人指定版本方案的兼容规则，不修改方案。" />
    <form onSubmit={create}><label htmlFor="chat-title">新会话名称</label><input id="chat-title" value={title} onChange={event => setTitle(event.target.value)} maxLength={120} required /><Button htmlType="submit" disabled={busy || !title.trim()}>新建私人会话</Button></form>
    <Button onClick={() => setAttempt(value => value + 1)}>刷新会话</Button>
    {sessions.status === 'loading' ? <p role="status">正在加载会话列表…</p> : sessions.status === 'error' ? <Alert type="error" title="会话列表加载失败" /> : sessions.data.items.length === 0 ? <p>当前身份下尚无会话。</p> : <ul className="chat-session-list">{sessions.data.items.map(value => <li key={value.id}><Button type={selected === value.id ? 'primary' : 'default'} onClick={() => select(value.id)}>{value.title}</Button></li>)}</ul>}
    {error && <Alert type="error" title={error} />}
    {selected ? <SessionThread key={selected} id={selected} attempt={attempt} refresh={refresh} remove={setDeletion} /> : <p>选择或新建一个会话，历史与上下文按会话隔离。</p>}
    <Modal title="确认删除私人会话" open={deletion !== null} onCancel={() => { if (!busy) setDeletion(null) }} onOk={() => { void confirmDelete() }} confirmLoading={busy} okText="确认删除" cancelText="保留会话" mask={{ closable: false }}><p>删除前：{deletion?.session.title}，{deletion?.message_count} 条消息，版本 {deletion?.session.version}。</p><p>删除后：立即禁止访问，取消未完成运行；后台清除消息、运行和检查点。仅保留最小删除记录，删除满七天且内容清理完成后，随清理任务移除。此操作无法恢复。</p></Modal>
  </aside>
}
