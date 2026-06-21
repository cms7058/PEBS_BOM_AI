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
  part_id: string | null
  mapping_status: 'unmapped' | 'suggested' | 'confirmed' | 'rejected'
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
  const res = await fetch(`${API_BASE}/upload/spreadsheet`, {
    method: 'POST',
    headers: authHeaders(),
    body: fd,
  })
  if (!res.ok) throw new Error(`Upload failed: ${res.status} ${await res.text()}`)
  return res.json()
}

export async function uploadCad(
  file: File,
): Promise<{ bom_id: string; name: string; node_count: number }> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${API_BASE}/upload/cad`, {
    method: 'POST',
    headers: authHeaders(),
    body: fd,
  })
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

export interface Part {
  id: string
  sku_internal: string | null
  name_standard: string
  part_number: string | null
  category_id: string | null
  category_name: string | null
  brand: string | null
  supplier: string | null
  uom: string
  unit_cost: number | null
  typical_lead_time: string | null
  status: 'active' | 'inactive' | 'pending'
  usage_count: number
  last_used_at: string | null
  spec: Record<string, unknown>
  notes: string | null
}

export interface SuggestionReference {
  bom_id: string
  bom_name: string | null
  node_id: string
  node_label: string
}

export interface PartSuggestion {
  part: Part
  score: number
  reason: string
  // Set when the score was lifted by a cross-BOM match — points at the
  // historical node we recognised. UI renders it as a deep link.
  reference?: SuggestionReference | null
}

export interface MappingStatus {
  node_id: string
  status: 'unmapped' | 'suggested' | 'confirmed' | 'rejected'
  mapped_part: Part | null
  suggestions: PartSuggestion[]
}

export interface MappingScanItem {
  node_id: string
  node_label: string
  status: 'unmapped' | 'suggested' | 'confirmed' | 'rejected'
  mapped_part: Part | null
  suggestions: PartSuggestion[]
}

export interface SubscriptionPlan {
  id: 'personal' | 'team' | 'enterprise' | string
  name: string
  tenant_type: 'personal' | 'team' | 'enterprise' | string
  description: string | null
  price_label: string | null
  price_cents: number
  currency: string
  duration_days: number
  seat_limit: number | null
  bom_limit: number | null
  enabled: boolean
  sort_order: number
}

export interface Tenant {
  id: string
  name: string
  tenant_type: string
  subscription_plan_id: string
  status: string
  owner_name: string | null
}

export interface AppUser {
  id: string
  tenant_id: string
  username: string
  display_name: string
  role: string
  email: string | null
  phone: string | null
  status: string
  trial_expires_at?: string | null
  bom_import_limit?: number | null
  bom_export_limit?: number | null
  bom_import_count?: number
  bom_export_count?: number
}

export interface FeatureFlag {
  id: string
  plan_id: string
  feature_key: string
  feature_name: string
  description: string | null
  enabled: boolean
  config: Record<string, unknown>
}

export interface AdminOverview {
  plans: SubscriptionPlan[]
  tenants: Tenant[]
  features: FeatureFlag[]
  users: AppUser[]
}

export interface PaymentOrder {
  id: string
  plan_id: string
  email: string
  amount_cents: number
  currency: string
  duration_days: number
  provider: string
  status: 'pending' | 'paid' | 'failed' | string
  checkout_url: string | null
}

const ADMIN_TOKEN_KEY = 'pebs.admin.token'
const CURRENT_USER_KEY = 'pebs.current.user'

export class ApiError extends Error {
  action?: string
  statusCode?: number

  constructor(message: string, action?: string, statusCode?: number) {
    super(message)
    this.name = 'ApiError'
    this.action = action
    this.statusCode = statusCode
  }
}

// ---- 阿米巴接入（PEBS BOM 作为子工具）----

export interface AmibaStatus {
  connected: boolean
  enterprise_id?: string
  source?: string
  amiba_endpoint?: string
  label?: string | null
  capabilities?: string[]
  connected_at?: string | null
  last_hello_at?: string | null
  hello_ok?: boolean
  hello_error?: string | null
  last_sync_at?: string | null
  last_sync_summary?: string | null
}

export interface AmibaSyncResult {
  ok: boolean
  bom_acc?: number
  mapping_rate?: number
  node_count?: number
  applied?: number
  summary?: string
  error?: string
}

export interface AmibaConnectInput {
  amiba_endpoint: string
  amiba_token: string
  enterprise_id: string
  source?: string
  label?: string
}

export async function amibaConnect(
  input: AmibaConnectInput,
): Promise<AmibaStatus & { ok: boolean }> {
  const res = await fetch(`${API_BASE}/amiba/connect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(data.detail || '接入失败', undefined, res.status)
  return data
}

export async function amibaStatus(): Promise<AmibaStatus> {
  const res = await fetch(`${API_BASE}/amiba/status`, { cache: 'no-store' })
  if (!res.ok) throw new ApiError('获取接入状态失败', undefined, res.status)
  return res.json()
}

export async function amibaSync(): Promise<AmibaSyncResult> {
  const res = await fetch(`${API_BASE}/amiba/sync`, { method: 'POST' })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(data.detail || '同步失败', undefined, res.status)
  return data
}

export function getAdminToken(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem(ADMIN_TOKEN_KEY) || ''
}

export function setAdminToken(token: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(ADMIN_TOKEN_KEY, token)
}

export function getCurrentUser(): AppUser | null {
  if (typeof window === 'undefined') return null
  const raw = localStorage.getItem(CURRENT_USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as AppUser
  } catch {
    return null
  }
}

export function setCurrentUser(user: AppUser): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(CURRENT_USER_KEY, JSON.stringify(user))
}

export function clearAdminToken(): void {
  if (typeof window === 'undefined') return
  localStorage.removeItem(ADMIN_TOKEN_KEY)
  localStorage.removeItem(CURRENT_USER_KEY)
}

function adminHeaders(): Record<string, string> {
  const token = getAdminToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function authHeaders(): Record<string, string> {
  return adminHeaders()
}

export async function getAdminOverview(signal?: AbortSignal): Promise<AdminOverview> {
  const res = await fetch(`${API_BASE}/admin/overview`, { cache: 'no-store', signal })
  if (!res.ok) throw new Error(`admin overview failed: ${res.status}`)
  return res.json()
}

export async function listSubscriptionPlans(signal?: AbortSignal): Promise<SubscriptionPlan[]> {
  const res = await fetch(`${API_BASE}/admin/plans`, { cache: 'no-store', signal })
  if (!res.ok) throw new Error(`plans failed: ${res.status}`)
  return res.json()
}

export async function listPublicFeatures(signal?: AbortSignal): Promise<FeatureFlag[]> {
  const res = await fetch(`${API_BASE}/admin/overview`, { cache: 'no-store', signal })
  if (!res.ok) throw new Error(`admin overview failed: ${res.status}`)
  const data = (await res.json()) as AdminOverview
  return data.features || []
}

export async function sendEmailCode(
  email: string,
  purpose = 'register',
): Promise<{ ok: boolean; message: string; dev_code?: string; expires_in_seconds: number }> {
  const res = await fetch(`${API_BASE}/admin/email-codes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, purpose }),
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      detail = (await res.json())?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `email code failed: ${res.status}`)
  }
  return res.json()
}

export async function createPaymentOrder(
  planId: string,
  email: string,
): Promise<PaymentOrder> {
  const res = await fetch(`${API_BASE}/admin/payment-orders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan_id: planId, email }),
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      detail = (await res.json())?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `payment order failed: ${res.status}`)
  }
  return res.json()
}

export async function confirmPaymentOrder(orderId: string): Promise<PaymentOrder> {
  const res = await fetch(`${API_BASE}/admin/payment-orders/${orderId}/confirm`, {
    method: 'POST',
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      detail = (await res.json())?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `payment confirm failed: ${res.status}`)
  }
  return res.json()
}

export async function adminLogin(
  username: string,
  password: string,
): Promise<{ token: string; user: AppUser }> {
  const res = await fetch(`${API_BASE}/admin/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      detail = (await res.json())?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `login failed: ${res.status}`)
  }
  const data = await res.json()
  setAdminToken(data.token)
  setCurrentUser(data.user)
  return data
}

export async function internalBetaLogin(
  email: string,
  inviteCode: string,
): Promise<{ token: string; user: AppUser }> {
  const res = await fetch(`${API_BASE}/admin/internal-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, invite_code: inviteCode }),
  })
  if (!res.ok) {
    let message: string | undefined
    let action: string | undefined
    try {
      const body = await res.json()
      const detail = body?.detail
      if (typeof detail === 'string') {
        message = detail
      } else if (detail && typeof detail === 'object') {
        message = detail.message
        action = detail.action
      }
    } catch { /* ignore */ }
    throw new ApiError(message || `internal login failed: ${res.status}`, action, res.status)
  }
  const data = await res.json()
  setAdminToken(data.token)
  setCurrentUser(data.user)
  return data
}

export async function registerTenantUser(
  planId: string,
  username: string,
  password: string,
  displayName?: string,
  email?: string,
  emailCode?: string,
  paymentOrderId?: string,
): Promise<{ token: string; user: AppUser }> {
  const res = await fetch(`${API_BASE}/admin/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      plan_id: planId,
      username,
      password,
      display_name: displayName,
      email,
      email_code: emailCode,
      payment_order_id: paymentOrderId,
    }),
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      detail = (await res.json())?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `register failed: ${res.status}`)
  }
  const data = await res.json()
  setAdminToken(data.token)
  setCurrentUser(data.user)
  return data
}

export async function updateAdminFeature(
  featureId: string,
  patch: Partial<Pick<FeatureFlag, 'enabled' | 'feature_name' | 'description' | 'config'>>,
): Promise<AdminOverview> {
  const res = await fetch(`${API_BASE}/admin/features/${featureId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...adminHeaders() },
    body: JSON.stringify(patch),
  })
  if (!res.ok) throw new Error(`feature update failed: ${res.status}`)
  return res.json()
}

export async function updateAdminPlan(
  planId: string,
  patch: Partial<Pick<SubscriptionPlan, 'name' | 'description' | 'price_label' | 'price_cents' | 'currency' | 'duration_days' | 'seat_limit' | 'bom_limit' | 'enabled'>>,
): Promise<AdminOverview> {
  const res = await fetch(`${API_BASE}/admin/plans/${planId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...adminHeaders() },
    body: JSON.stringify(patch),
  })
  if (!res.ok) throw new Error(`plan update failed: ${res.status}`)
  return res.json()
}

export async function updateAdminUser(
  userId: string,
  patch: Partial<Pick<AppUser, 'display_name' | 'email' | 'phone' | 'role' | 'status'>> & { password?: string },
): Promise<AdminOverview> {
  const res = await fetch(`${API_BASE}/admin/users/${userId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...adminHeaders() },
    body: JSON.stringify(patch),
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      detail = (await res.json())?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `user update failed: ${res.status}`)
  }
  return res.json()
}

export interface MappingScan {
  bom_id: string
  total_nodes: number
  confirmed_count: number
  unmapped_count: number
  candidate_count: number
  items: MappingScanItem[]
}

export interface PartReference {
  bom_id: string
  bom_name: string
  node_id: string
  node_label: string
  part_number: string | null
  quantity: number
  uom: string
  supplier: string | null
  unit_cost: number | null
}

export interface PartAlias {
  raw_name: string
  raw_part_number: string | null
  confirmed_at: string | null
}

export interface PartDetail {
  part: Part
  references: PartReference[]
  aliases: PartAlias[]
}

export interface PartDraftRow {
  action: string
  name_standard: string
  sku_internal: string | null
  part_number: string | null
  category_id: string | null
  category_name: string | null
  brand: string | null
  supplier: string | null
  uom: string
  unit_cost: number | null
  typical_lead_time: string | null
  notes: string | null
  risk: string | null
}

export interface PartImportDraft {
  id: string
  status: 'draft' | 'confirmed' | 'cancelled'
  source_type: string
  rows: PartDraftRow[]
  created_at: string
  confirmed_at: string | null
}

export async function getNodeMapping(
  bomId: string,
  nodeId: string,
  signal?: AbortSignal,
): Promise<MappingStatus> {
  const res = await fetch(`${API_BASE}/boms/${bomId}/nodes/${nodeId}/mapping`, {
    cache: 'no-store',
    signal,
  })
  if (!res.ok) throw new Error(`mapping failed: ${res.status}`)
  return res.json()
}

// ─── Risk scan ────────────────────────────────────────────────────────────

export interface RiskTag {
  code: string
  severity: 'info' | 'warn' | 'critical'
  message: string
}

export interface RiskScanItem {
  node_id: string
  node_label: string
  tags: RiskTag[]
}

export interface RiskScan {
  bom_id: string
  total_nodes: number
  flagged_nodes: number
  severity_counts: { info?: number; warn?: number; critical?: number }
  items: RiskScanItem[]
}

export async function scanBOMRisks(
  bomId: string,
  signal?: AbortSignal,
): Promise<RiskScan> {
  const res = await fetch(`${API_BASE}/boms/${bomId}/risks`, {
    cache: 'no-store',
    signal,
  })
  if (!res.ok) throw new Error(`risks failed: ${res.status}`)
  return res.json()
}

export async function scanBOMMapping(
  bomId: string,
  signal?: AbortSignal,
): Promise<MappingScan> {
  const res = await fetch(`${API_BASE}/boms/${bomId}/mapping/scan?limit=1000`, {
    cache: 'no-store',
    signal,
  })
  if (!res.ok) throw new Error(`mapping scan failed: ${res.status}`)
  return res.json()
}

export async function listParts(
  query = '',
  signal?: AbortSignal,
  categoryId?: string | null,
): Promise<{ items: Part[]; total: number }> {
  const params = new URLSearchParams()
  if (query.trim()) params.set('q', query.trim())
  if (categoryId) params.set('category_id', categoryId)
  const suffix = params.toString() ? `?${params.toString()}` : ''
  const res = await fetch(`${API_BASE}/parts${suffix}`, { cache: 'no-store', signal })
  if (!res.ok) throw new Error(`parts failed: ${res.status}`)
  return res.json()
}

export async function getPartDetail(
  partId: string,
  signal?: AbortSignal,
): Promise<PartDetail> {
  const res = await fetch(`${API_BASE}/parts/${partId}`, { cache: 'no-store', signal })
  if (!res.ok) throw new Error(`part detail failed: ${res.status}`)
  return res.json()
}

export async function getPartImportDraft(
  draftId: string,
  signal?: AbortSignal,
): Promise<PartImportDraft> {
  const res = await fetch(`${API_BASE}/parts/import-drafts/${draftId}`, { cache: 'no-store', signal })
  if (!res.ok) throw new Error(`import draft failed: ${res.status}`)
  return res.json()
}

export async function confirmPartImportDraft(
  draftId: string,
): Promise<{ draft: PartImportDraft; created: Part[] }> {
  const res = await fetch(`${API_BASE}/parts/import-drafts/${draftId}/confirm`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error(`confirm import draft failed: ${res.status}`)
  return res.json()
}

export async function uploadPartImportDraft(file: File): Promise<PartImportDraft> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${API_BASE}/parts/import-drafts/upload`, {
    method: 'POST',
    body: fd,
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      const body = await res.json()
      detail = body?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `upload import draft failed: ${res.status}`)
  }
  return res.json()
}

export async function confirmNodeMapping(
  bomId: string,
  nodeId: string,
  partId: string,
  userName?: string,
): Promise<BOMNode> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (userName) headers['X-User-Name'] = encodeURIComponent(userName)
  const res = await fetch(`${API_BASE}/boms/${bomId}/nodes/${nodeId}/mapping/confirm`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ part_id: partId }),
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      const body = await res.json()
      detail = body?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `mapping confirm failed: ${res.status}`)
  }
  return res.json()
}

export async function createPartFromNode(
  bomId: string,
  nodeId: string,
  userName?: string,
): Promise<BOMNode> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (userName) headers['X-User-Name'] = encodeURIComponent(userName)
  const res = await fetch(`${API_BASE}/boms/${bomId}/nodes/${nodeId}/mapping/create`, {
    method: 'POST',
    headers,
    body: JSON.stringify({}),
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      const body = await res.json()
      detail = body?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `mapping create failed: ${res.status}`)
  }
  return res.json()
}

export async function updatePart(
  partId: string,
  patch: Partial<Pick<Part, 'sku_internal' | 'name_standard' | 'part_number' | 'category_id' | 'brand' | 'supplier' | 'uom' | 'unit_cost' | 'typical_lead_time' | 'status' | 'notes'>>,
): Promise<Part> {
  const res = await fetch(`${API_BASE}/parts/${partId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      const body = await res.json()
      detail = body?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `part update failed: ${res.status}`)
  }
  return res.json()
}

export async function listBrands(signal?: AbortSignal): Promise<{ brands: BrandRecommendation[]; total: number }> {
  const res = await fetch(`${API_BASE}/brands`, { cache: 'no-store', signal })
  if (!res.ok) throw new Error(`brands failed: ${res.status}`)
  return res.json()
}

export async function createBrand(
  name: string,
  categories: string[] = [],
  signal?: AbortSignal,
): Promise<BrandRecommendation> {
  const res = await fetch(`${API_BASE}/brands`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, categories }),
    signal,
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      const body = await res.json()
      detail = body?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `create brand failed: ${res.status}`)
  }
  return res.json()
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

export async function createCategory(
  nameZh: string,
  signal?: AbortSignal,
): Promise<ComponentCategory> {
  const res = await fetch(`${API_BASE}/component-categories`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name_zh: nameZh }),
    signal,
  })
  if (!res.ok) {
    let detail: string | undefined
    try {
      const body = await res.json()
      detail = body?.detail
    } catch { /* ignore */ }
    throw new Error(detail || `create category failed: ${res.status}`)
  }
  return res.json()
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
  const token = getAdminToken()
  const suffix = token ? `?auth_token=${encodeURIComponent(token)}` : ''
  return `${API_BASE}/export/${bomId}.xlsx${suffix}`
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

export async function createNode(
  bomId: string,
  body: {
    parent_id?: string | null
    part_name: string
    part_number?: string | null
    quantity?: number
    uom?: string
  },
): Promise<BOMNode> {
  const res = await fetch(`${API_BASE}/boms/${bomId}/nodes`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-User-Name': encodeURIComponent(getUserName()),
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`Create node failed: ${res.status} ${await res.text()}`)
  return res.json()
}

export async function deleteNode(
  bomId: string,
  nodeId: string,
  cascade = false,
): Promise<{ deleted: number }> {
  const res = await fetch(
    `${API_BASE}/boms/${bomId}/nodes/${nodeId}?cascade=${cascade ? 'true' : 'false'}`,
    {
      method: 'DELETE',
      headers: {
        'X-User-Name': encodeURIComponent(getUserName()),
      },
    },
  )
  if (!res.ok) throw new Error(`Delete node failed: ${res.status} ${await res.text()}`)
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
  | { type: 'tool_call'; name: string; args: Record<string, unknown>; ok: boolean; summary: string; mutated: boolean; data?: Record<string, unknown> }
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
            data: parsed.data,
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
