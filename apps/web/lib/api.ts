export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'

export interface BOMNode {
  id: string
  parent_id: string | null
  level: number
  part_number: string | null
  part_name: string
  description: string | null
  quantity: number
  uom: string
  material: string | null
  weight: number | null
  supplier: string | null
  unit_cost: number | null
  notes: string | null
  style: Record<string, unknown>
  source_ref: Record<string, unknown> | null
  confidence: number
  sort_order: number
  // Non-std component classification (set by agent's bom_classify_* tools)
  category_id: string | null
  category_name: string | null
  spec: Record<string, unknown>
  // MBOM scaffolding (all nullable; not populated until the MBOM module
  // ships in a future release — see business analysis docs).
  operation_seq: number | null
  operation_desc: string | null
  fixture_ref: string | null
  consumed_by_op: number | null
  standard_time_min: number | null
}

export interface BOM {
  id: string
  name: string
  source_file_id: string | null
  nodes: BOMNode[]
}

export interface BOMListItem {
  id: string
  name: string
  node_count: number
}

export async function uploadSpreadsheet(
  file: File,
): Promise<{ bom_id: string; name: string; node_count: number }> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${API_BASE}/upload/spreadsheet`, { method: 'POST', body: fd })
  if (!res.ok) throw new Error(`Upload failed: ${res.status} ${await res.text()}`)
  return res.json()
}

export async function uploadCad(
  file: File,
): Promise<{ bom_id: string; name: string; node_count: number }> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${API_BASE}/upload/cad`, { method: 'POST', body: fd })
  if (!res.ok) throw new Error(`Upload failed: ${res.status} ${await res.text()}`)
  return res.json()
}

export async function listBOMs(): Promise<BOMListItem[]> {
  const res = await fetch(`${API_BASE}/boms`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`List failed: ${res.status}`)
  return res.json()
}

export async function getBOM(id: string): Promise<BOM> {
  const res = await fetch(`${API_BASE}/boms/${id}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`Get failed: ${res.status}`)
  return res.json()
}

export interface BrandRecommendation {
  id: string
  name: string
  url: string | null
  region: string | null
  categories: string[]
  price_tier: string | null
  typical_lead_time: string | null
  notes: string | null
  visibility: 'private' | 'shared'
  upvotes: number
  /** "private" | "shared-by-you" | "community" */
  trust: string
}

export interface BrandRecommendResult {
  category_id: string
  category_name: string
  recommendations: BrandRecommendation[]
  fallback_brands: string[]
}

export async function recommendBrands(
  categoryId: string,
  signal?: AbortSignal,
): Promise<BrandRecommendResult> {
  const url = `${API_BASE}/brands/recommend?category_id=${encodeURIComponent(categoryId)}`
  const res = await fetch(url, { cache: 'no-store', signal })
  if (!res.ok) throw new Error(`brands/recommend failed: ${res.status}`)
  return res.json()
}

// ─── Models (chat sidebar picker) ────────────────────────────────────────

export interface ModelOption {
  id: string
  label: string
  provider: string
}

export interface ModelsResult {
  models: ModelOption[]
  default: string
}

export async function listModels(signal?: AbortSignal): Promise<ModelsResult> {
  const res = await fetch(`${API_BASE}/agent/models`, {
    cache: 'no-store',
    signal,
  })
  if (!res.ok) throw new Error(`agent/models failed: ${res.status}`)
  return res.json()
}

// ─── Component categories (non-std taxonomy) ─────────────────────────────

export interface ParameterDef {
  name: string
  label_zh: string
  unit?: string
  type: 'enum' | 'number' | 'integer' | 'string'
  values?: (string | number)[]
  required?: boolean
  default?: string | number
}

export interface ComponentCategory {
  id: string
  parent_id: string | null
  name_zh: string
  name_en: string
  description: string | null
  parameters: ParameterDef[]
  common_brands: string[]
  typical_use: string | null
  related_gb: string | null
  sort_order: number
}

export async function listCategories(signal?: AbortSignal): Promise<ComponentCategory[]> {
  const res = await fetch(`${API_BASE}/component-categories`, {
    cache: 'no-store',
    signal,
  })
  if (!res.ok) throw new Error(`component-categories failed: ${res.status}`)
  const d = await res.json()
  return d.categories || []
}

// ─── Node classification ─────────────────────────────────────────────────

export async function classifyNode(
  bomId: string,
  nodeId: string,
  patch: { category_id?: string | null; spec?: Record<string, unknown> },
  userName?: string,
): Promise<BOMNode> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (userName) headers['X-User-Name'] = encodeURIComponent(userName)
  const res = await fetch(
    `${API_BASE}/boms/${bomId}/nodes/${nodeId}/classification`,
    {
      method: 'PATCH',
      headers,
      body: JSON.stringify(patch),
    },
  )
  if (!res.ok) {
    let detail: string | undefined
    try {
      const body = await res.json()
      detail = body?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `classify failed: ${res.status}`)
  }
  return res.json()
}

export function exportUrl(bomId: string): string {
  return `${API_BASE}/export/${bomId}.xlsx`
}

const USER_NAME_KEY = 'pebs.user.name'

export function getUserName(): string {
  if (typeof window === 'undefined') return 'anonymous'
  return localStorage.getItem(USER_NAME_KEY) || 'anonymous'
}

export function setUserName(name: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(USER_NAME_KEY, name)
}

export async function patchNode(
  bomId: string,
  nodeId: string,
  patch: Partial<Record<string, unknown>>,
): Promise<BOMNode> {
  const res = await fetch(`${API_BASE}/boms/${bomId}/nodes/${nodeId}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      // URL-encode so non-ASCII names (中文) survive the HTTP header layer.
      'X-User-Name': encodeURIComponent(getUserName()),
    },
    body: JSON.stringify(patch),
  })
  if (!res.ok) throw new Error(`Patch failed: ${res.status} ${await res.text()}`)
  return res.json()
}

export interface BOMEdit {
  id: string
  node_id: string
  node_label: string | null
  field: string
  field_label: string
  old_value: string | null
  new_value: string | null
  user_name: string
  source: string
  created_at: string
}

export async function listEdits(bomId: string, limit = 200): Promise<BOMEdit[]> {
  const res = await fetch(`${API_BASE}/boms/${bomId}/edits?limit=${limit}`, {
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(`List edits failed: ${res.status}`)
  return res.json()
}

export interface HierarchyRule {
  separator: string
  confidence: number
  sample_chains: string[][]
  orphan_count: number
  candidate_separators: string[]
}

export interface ApplySummary {
  top_level: number
  linked: number
  orphans: number
  no_partnumber: number
}

export async function detectHierarchy(bomId: string): Promise<HierarchyRule> {
  const res = await fetch(`${API_BASE}/boms/${bomId}/hierarchy`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`Detect failed: ${res.status}`)
  return res.json()
}

export async function applyHierarchy(bomId: string, separator: string): Promise<ApplySummary> {
  const res = await fetch(`${API_BASE}/boms/${bomId}/hierarchy/apply`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Name': encodeURIComponent(getUserName()),
    },
    body: JSON.stringify({ separator }),
  })
  if (!res.ok) throw new Error(`Apply failed: ${res.status} ${await res.text()}`)
  return res.json()
}

export async function undoEdit(bomId: string, editId: string): Promise<BOMNode> {
  const res = await fetch(`${API_BASE}/boms/${bomId}/edits/${editId}/undo`, {
    method: 'POST',
    headers: {
      'X-User-Name': encodeURIComponent(getUserName()),
    },
  })
  if (!res.ok) throw new Error(`Undo failed: ${res.status} ${await res.text()}`)
  return res.json()
}

export type AgentEvent =
  | { type: 'delta'; text: string }
  | { type: 'tool_call'; name: string; args: Record<string, unknown>; ok: boolean; summary: string; mutated: boolean }
  | { type: 'bom_updated' }
  | { type: 'status'; phase: string }
  | { type: 'done'; reason?: string }
  | { type: 'error'; message: string }

export async function* chatStream(
  bomId: string,
  message: string,
  history: { role: string; content: string }[] = [],
  model?: string | null,
): AsyncGenerator<AgentEvent> {
  const res = await fetch(`${API_BASE}/agent/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      bom_id: bomId,
      message,
      history,
      user_name: getUserName(),
      ...(model ? { model } : {}),
    }),
  })
  if (!res.ok || !res.body) throw new Error(`Chat failed: ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE event delimiter can be \n\n or \r\n\r\n (sse_starlette emits CRLF).
    const chunks = buffer.split(/\r?\n\r?\n/)
    buffer = chunks.pop() || ''
    for (const chunk of chunks) {
      let eventName = 'message'
      let data = ''
      for (const line of chunk.split(/\r?\n/)) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (!data) continue
      try {
        const parsed = JSON.parse(data)
        if (eventName === 'delta') yield { type: 'delta', text: parsed.text }
        else if (eventName === 'tool_call')
          yield {
            type: 'tool_call',
            name: parsed.name,
            args: parsed.args,
            ok: parsed.ok,
            summary: parsed.summary,
            mutated: parsed.mutated,
          }
        else if (eventName === 'bom_updated') yield { type: 'bom_updated' }
        else if (eventName === 'status') yield { type: 'status', phase: parsed.phase }
        else if (eventName === 'done') yield { type: 'done', reason: parsed.reason }
        else if (eventName === 'error') yield { type: 'error', message: parsed.message }
      } catch {
        // ignore malformed chunk
      }
    }
  }
}
