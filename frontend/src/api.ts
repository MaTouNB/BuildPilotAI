export type Price = { id: string; price_usd: string | null; dataset_updated_at: string; source_name: string; quote_status: string; is_quotable: boolean }
export type Part = { id: string; name: string; category: string; quality_status: string; has_price: boolean; is_quotable: boolean; quote_status: string; price_usd: string | null; price_metadata: Price | null }
export type Page<T> = { items: T[]; total: number; page: number; page_size: number }
export type Source = { source_file: string; line_number: number; end_line_number: number; row_number: number; source_name: string; dataset_updated_at: string; row_hash: string; raw_data: Record<string, unknown> }
export type Detail = Part & { specifications: Record<string, unknown>; sources: Source[]; source_count: number; evidence_limit: number; issues: { code: string; field: string; message: string }[] }
export type Category = { id: string; name: string; count: number }
export type Sources = { reviewed_parts: number; items: { name: string; dataset_updated_at: string; row_count: number }[] }
export async function get<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch('/api/v1' + path, { signal })
  if (!response.ok) throw new Error(`请求失败 (${response.status})`)
  return response.json() as Promise<T>
}
