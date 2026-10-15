import { expect, it } from 'vitest'
import { readEvents, type StreamEvent } from '../chat-stream'
function response(chunks: Uint8Array[]) { return new Response(new ReadableStream({ start(controller) { for (const chunk of chunks) controller.enqueue(chunk); controller.close() } })) }
it('decodes split UTF-8 and CRLF frames with monotonic event IDs', async () => {
  const text = ': heartbeat\r\n\r\nid: 1\r\nevent: message.delta\r\ndata: {"run_id":"r","text":"中文"}\r\n\r\nid: 1\r\nevent: message.delta\r\ndata: {"run_id":"r"}\r\n\r\nid: 2\r\nevent: run.completed\r\ndata: {"run_id":"r"}\r\n\r\n'
  const bytes = new TextEncoder().encode(text), chunks = [...bytes].map(value => Uint8Array.of(value)), events: StreamEvent[] = []
  await readEvents(response(chunks), 'r', event => events.push(event))
  expect(events.map(event => event.id)).toEqual([1, 2])
  expect(events[0].data.text).toBe('中文')
})
it('rejects a mismatched run ID instead of painting another conversation', async () => {
  const bytes = new TextEncoder().encode('id: 1\nevent: run.completed\ndata: {"run_id":"other"}\n\n')
  await expect(readEvents(response([bytes]), 'r', () => undefined)).rejects.toThrow('运行标识不匹配')
})
it('bounds incomplete event buffering', async () => {
  await expect(readEvents(response([new TextEncoder().encode('x'.repeat(65537))]), 'r', () => undefined)).rejects.toThrow('事件过大')
})
