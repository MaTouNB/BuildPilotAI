import { useEffect, useState } from 'react'
import { get } from './api'
type State<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: T }
export function useResource<T>(path: string, attempt = 0): State<T> {
  const key = `${path}:${attempt}`
  const [result, setResult] = useState<{ key: string; value: State<T> } | null>(null)
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 8000)
    void get<T>(path, controller.signal).then(data => {
      if (active) setResult({ key, value: { status: 'ready', data } })
    }).catch(() => {
      if (active) setResult({ key, value: { status: 'error', message: '数据暂时无法加载，请检查服务或稍后重试。' } })
    }).finally(() => window.clearTimeout(timeout))
    return () => { active = false; controller.abort(); window.clearTimeout(timeout) }
  }, [path, key])
  return result?.key === key ? result.value : { status: 'loading' }
}
