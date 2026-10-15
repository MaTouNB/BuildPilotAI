export type StreamEvent = { id: number; kind: string; data: { run_id: string; [key: string]: unknown } }
export async function readEvents(response: Response, runId: string, receive: (event: StreamEvent) => void) {
  if (!response.body) throw new Error('没有事件流')
  const reader = response.body.getReader(), decoder = new TextDecoder()
  let pending = '', lastId = 0
  try {
    while (true) {
      const chunk = await reader.read()
      pending = (pending + decoder.decode(chunk.value, { stream: !chunk.done })).replaceAll('\r\n', '\n')
      if (pending.length > 65536) throw new Error('事件过大')
      let boundary: number
      while ((boundary = pending.indexOf('\n\n')) >= 0) {
        const frame = pending.slice(0, boundary); pending = pending.slice(boundary + 2)
        const lines = frame.split('\n')
        const rawId = lines.find(line => line.startsWith('id:'))?.slice(3).trim()
        if (!rawId) continue
        const id = Number(rawId), kind = lines.find(line => line.startsWith('event:'))?.slice(6).trim()
        const raw = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (!Number.isSafeInteger(id) || id < 1 || !kind) throw new Error('事件格式无效')
        if (id <= lastId) continue
        const data = JSON.parse(raw) as StreamEvent['data']
        if (data.run_id !== runId) throw new Error('运行标识不匹配')
        lastId = id
        receive({ id, kind, data })
        if (kind === 'run.completed' || kind === 'run.failed') return
      }
      if (chunk.done) return
    }
  } finally { await reader.cancel().catch(() => undefined) }
}
