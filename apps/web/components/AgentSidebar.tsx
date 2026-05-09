'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  BOMNode,
  BrandRecommendation,
  MappingScan,
  MappingStatus,
  ModelOption,
  Part,
  PartSuggestion,
  RiskTag,
} from '@/lib/api'
import {
  chatStream,
  confirmNodeMapping,
  createPartFromNode,
  getNodeMapping,
  listModels,
  recommendBrands,
  scanBOMMapping,
  scanBOMRisks,
  uploadPartImportDraft,
} from '@/lib/api'

const MODEL_STORAGE_KEY = 'agent.model.v2'

// Brand recommendations strip — shows up to ~5 brands, badged by trust
// (★★ private / ★ shared-by-you / · community). Falls back to the
// category's bundled common_brands list if KB is empty for this category.
const TRUST_BADGE: Record<string, { label: string; color: string }> = {
  private: { label: '★★', color: '#15803d' },
  'shared-by-you': { label: '★', color: '#0369a1' },
  community: { label: '·', color: '#6b7280' },
}
function BrandStrip({
  brands,
  fallback,
  loading,
}: {
  brands: BrandRecommendation[]
  fallback: string[]
  loading: boolean
}) {
  if (loading) {
    return (
      <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 6 }}>
        正在拉取品牌推荐…
      </div>
    )
  }
  const hasKb = brands.length > 0
  const fb = fallback.slice(0, 5)
  return (
    <div style={{ marginBottom: 6, lineHeight: 1.6 }}>
      <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 2 }}>
        {hasKb ? '推荐品牌（私有库优先）：' : '通用参考品牌：'}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {hasKb &&
          brands.slice(0, 5).map((b) => {
            const tag = TRUST_BADGE[b.trust] || TRUST_BADGE.community
            return (
              <span
                key={b.id}
                title={[b.region, b.price_tier, b.notes].filter(Boolean).join(' · ')}
                style={{
                  background: '#fff',
                  border: '1px solid #bfdbfe',
                  borderRadius: 4,
                  padding: '2px 6px',
                  fontSize: 11,
                  color: '#1f2937',
                }}
              >
                <span
                  style={{
                    color: tag.color,
                    fontWeight: 600,
                    marginRight: 4,
                  }}
                >
                  {tag.label}
                </span>
                {b.name}
              </span>
            )
          })}
        {!hasKb &&
          fb.map((name) => (
            <span
              key={name}
              style={{
                background: '#fff',
                border: '1px dashed #d1d5db',
                borderRadius: 4,
                padding: '2px 6px',
                fontSize: 11,
                color: '#6b7280',
              }}
            >
              {name}
            </span>
          ))}
      </div>
    </div>
  )
}

// Renders "1 天前" / "今天" / ISO date for a Part.last_used_at value.
function formatLastUsed(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diffDays = Math.floor((Date.now() - d.getTime()) / 86_400_000)
  if (diffDays <= 0) return '今天'
  if (diffDays === 1) return '昨天'
  if (diffDays < 30) return `${diffDays} 天前`
  return d.toISOString().slice(0, 10)
}

// Pinned card showing the currently-selected node, plus 4 quick prompts
// that pre-fill the input box (don't auto-send — user can edit before
// hitting Enter). The label always names the part so the model has the
// reference unambiguously when the prompt fires.
function SelectionContextCard({
  bomId,
  node,
  onClear,
  onUseQuickPrompt,
  onOpenConfigurator,
  onMappingChanged,
}: {
  bomId: string
  node: BOMNode
  onClear?: () => void
  onUseQuickPrompt: (text: string) => void
  onOpenConfigurator?: () => void
  onMappingChanged?: () => void
}) {
  const ref = node.part_name || node.part_number || node.id.slice(0, 8)
  const specEntries = Object.entries(node.spec || {}).slice(0, 4)

  // Auto-fetch brand recommendations whenever the selected node is classified.
  // Avoids the user having to ask "推荐几个品牌" each time — the answer is
  // already there when they look at the card.
  const [brands, setBrands] = useState<BrandRecommendation[]>([])
  const [fallbackBrands, setFallbackBrands] = useState<string[]>([])
  const [loadingBrands, setLoadingBrands] = useState(false)
  // Always fetch mapping for the selected node:
  //  · mapped → render usage_count / last_used_at on mapped_part
  //  · unmapped → render top suggestions inline so the user can confirm
  //    "yes, this is the same part" or create a new standard part in one
  //    click instead of going through chat.
  const [mappedPart, setMappedPart] = useState<Part | null>(null)
  const [suggestions, setSuggestions] = useState<PartSuggestion[]>([])
  const [loadingMapping, setLoadingMapping] = useState(false)
  // Risk tags for this node — fetched alongside mapping. Cheap rule-based
  // scan, runs in milliseconds; we slice to top 3 to avoid card clutter.
  const [riskTags, setRiskTags] = useState<RiskTag[]>([])
  useEffect(() => {
    const ctrl = new AbortController()
    scanBOMRisks(bomId, ctrl.signal)
      .then((r) => {
        const item = r.items.find((it) => it.node_id === node.id)
        setRiskTags(item?.tags || [])
      })
      .catch((ex) => {
        if (ex?.name !== 'AbortError') {
          // eslint-disable-next-line no-console
          console.warn('[SelectionContextCard] scanBOMRisks failed', ex)
        }
      })
    return () => ctrl.abort()
    // reloadTick refetches after mapping mutations — supplier/category may
    // have changed and the same risk rules need re-evaluation.
  }, [bomId, node.id, node.part_id, node.category_id, node.supplier])
  // Set while a confirm/create mutation is in flight, so we can disable
  // both buttons and show a "处理中…" hint without flicker.
  const [mappingBusy, setMappingBusy] = useState(false)
  // Bumped after every successful mutation to retrigger the mapping fetch
  // (the parent BOMNode prop won't update until the parent reloads, which
  // lags behind our local state).
  const [reloadTick, setReloadTick] = useState(0)
  useEffect(() => {
    const ctrl = new AbortController()
    setLoadingMapping(true)
    getNodeMapping(bomId, node.id, ctrl.signal)
      .then((m) => {
        setMappedPart(m.mapped_part)
        setSuggestions(m.suggestions || [])
      })
      .catch((ex) => {
        if (ex?.name !== 'AbortError') {
          // eslint-disable-next-line no-console
          console.warn('[SelectionContextCard] getNodeMapping failed', ex)
        }
      })
      .finally(() => setLoadingMapping(false))
    return () => ctrl.abort()
  }, [bomId, node.id, node.part_id, reloadTick])

  async function handleConfirmCandidate(partId: string) {
    if (mappingBusy) return
    setMappingBusy(true)
    try {
      await confirmNodeMapping(bomId, node.id, partId)
      setReloadTick((n) => n + 1)
      onMappingChanged?.()
    } catch (ex) {
      // eslint-disable-next-line no-console
      console.warn('[SelectionContextCard] confirmNodeMapping failed', ex)
    } finally {
      setMappingBusy(false)
    }
  }

  async function handleCreateNew() {
    if (mappingBusy) return
    setMappingBusy(true)
    try {
      await createPartFromNode(bomId, node.id)
      setReloadTick((n) => n + 1)
      onMappingChanged?.()
    } catch (ex) {
      // eslint-disable-next-line no-console
      console.warn('[SelectionContextCard] createPartFromNode failed', ex)
    } finally {
      setMappingBusy(false)
    }
  }
  useEffect(() => {
    if (!node.category_id) {
      setBrands([])
      setFallbackBrands([])
      return
    }
    const ctrl = new AbortController()
    setLoadingBrands(true)
    recommendBrands(node.category_id, ctrl.signal)
      .then((r) => {
        setBrands(r.recommendations || [])
        setFallbackBrands(r.fallback_brands || [])
      })
      .catch((ex) => {
        if (ex?.name !== 'AbortError') {
          // eslint-disable-next-line no-console
          console.warn('[SelectionContextCard] recommendBrands failed', ex)
        }
      })
      .finally(() => setLoadingBrands(false))
    return () => ctrl.abort()
  }, [node.category_id, node.id])
  const prompts: Array<{ label: string; text: string }> = []

  if (!node.category_id) {
    prompts.push({
      label: '🔍 这是什么类目？',
      text: `${ref} 看起来是什么类目？如果能确定，把它分类到对应类目下。`,
    })
  } else {
    prompts.push({
      label: '✨ 推荐品牌',
      text: `给我推荐几个${node.category_name}的品牌，看看私有库里有没有，没有就给通用建议。`,
    })
    prompts.push({
      label: '📐 规格',
      text: `${ref} 现在的规格是什么？哪些参数还没填？`,
    })
  }
  prompts.push({
    label: '🎨 改样式',
    text: `改一下『${ref}』的样式`,
  })

  return (
    <div
      style={{
        padding: '8px 12px',
        background: '#eff6ff',
        borderTop: '1px solid #bfdbfe',
        borderBottom: '1px solid #bfdbfe',
        fontSize: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ color: '#1d4ed8', fontWeight: 500 }}>📌 已选中</span>
        <span style={{ color: '#1f2329', fontWeight: 600 }}>{node.part_name}</span>
        {node.part_number && node.part_number !== node.part_name && (
          <span style={{ color: '#6b7280' }}>· {node.part_number}</span>
        )}
        {node.category_name && (
          <span
            style={{
              fontSize: 11,
              color: '#fff',
              background: '#1783FF',
              padding: '1px 6px',
              borderRadius: 8,
            }}
          >
            {node.category_name}
          </span>
        )}
        <span
          style={{
            fontSize: 11,
            color: node.part_id ? '#047857' : '#b45309',
            background: node.part_id ? '#d1fae5' : '#fef3c7',
            padding: '1px 6px',
            borderRadius: 8,
          }}
        >
          {node.part_id ? '已映射' : '未映射'}
        </span>
        <span style={{ flex: 1 }} />
        {onClear && (
          <button
            onClick={onClear}
            title="取消选中"
            style={{
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              color: '#6b7280',
              fontSize: 14,
              padding: '0 4px',
            }}
          >
            ×
          </button>
        )}
      </div>
      {riskTags.length > 0 && (
        <div style={{ marginBottom: 6, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {riskTags.slice(0, 3).map((t) => {
            const style =
              t.severity === 'critical'
                ? { bg: '#fee2e2', fg: '#b91c1c', border: '#fca5a5', icon: '🚨' }
                : t.severity === 'warn'
                ? { bg: '#fef3c7', fg: '#92400e', border: '#fcd34d', icon: '⚠️' }
                : { bg: '#e0f2fe', fg: '#075985', border: '#7dd3fc', icon: 'ℹ️' }
            return (
              <span
                key={t.code}
                title={t.message}
                style={{
                  background: style.bg,
                  color: style.fg,
                  border: `1px solid ${style.border}`,
                  borderRadius: 4,
                  padding: '1px 6px',
                  fontSize: 11,
                  lineHeight: 1.4,
                }}
              >
                {style.icon} {t.message}
              </span>
            )
          })}
          {riskTags.length > 3 && (
            <span style={{ fontSize: 11, color: '#6b7280' }}>+{riskTags.length - 3}</span>
          )}
        </div>
      )}
      {node.part_id && mappedPart && (
        <div
          style={{
            color: '#374151',
            marginBottom: 6,
            fontSize: 11,
            display: 'flex',
            gap: 10,
            alignItems: 'center',
          }}
        >
          <span style={{ color: '#6b7280' }}>📊 历史使用</span>
          <span style={{ color: '#1f2937', fontWeight: 500 }}>
            {mappedPart.usage_count ?? 0} 次
          </span>
          {mappedPart.last_used_at && (
            <>
              <span style={{ color: '#d1d5db' }}>·</span>
              <span style={{ color: '#6b7280' }}>上次</span>
              <span style={{ color: '#1f2937' }} title={mappedPart.last_used_at}>
                {formatLastUsed(mappedPart.last_used_at)}
              </span>
            </>
          )}
        </div>
      )}
      {!node.part_id && (
        <div style={{ marginBottom: 6 }}>
          <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 4 }}>
            {loadingMapping
              ? '正在比对历史物料…'
              : suggestions.length > 0
              ? '可能是这些已有标准物料：'
              : '历史里没找到相似物料'}
          </div>
          {suggestions.slice(0, 3).map((s) => (
            <div
              key={s.part.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '4px 6px',
                marginBottom: 4,
                background: '#fff',
                border: '1px solid #bfdbfe',
                borderRadius: 4,
                fontSize: 12,
              }}
            >
              <div style={{ flex: 1, lineHeight: 1.4, minWidth: 0 }}>
                <div style={{ color: '#1f2937', fontWeight: 500 }}>
                  {s.part.name_standard}
                  {s.part.brand && (
                    <span style={{ color: '#6b7280', fontWeight: 400 }}> · {s.part.brand}</span>
                  )}
                </div>
                <div style={{ fontSize: 11, color: '#6b7280' }}>
                  {s.reason}
                  {(s.part.usage_count ?? 0) > 0 && (
                    <>
                      {' · 用过 '}
                      {s.part.usage_count}{' 次'}
                    </>
                  )}
                  {s.part.last_used_at && <> · 上次 {formatLastUsed(s.part.last_used_at)}</>}
                </div>
                {s.reference && s.reference.bom_id !== bomId && (
                  // Cross-BOM deep link. Hidden when the historical match is
                  // in *this* BOM (would just reload the current page).
                  <div style={{ fontSize: 11, marginTop: 2 }}>
                    <a
                      href={`/bom/${s.reference.bom_id}?node=${s.reference.node_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: '#1d4ed8', textDecoration: 'none' }}
                      title="在新标签页打开历史 BOM 并定位到该节点"
                    >
                      ↗ 在 BOM「{s.reference.bom_name || s.reference.bom_id.slice(0, 8)}」节点「{s.reference.node_label}」
                    </a>
                  </div>
                )}
              </div>
              <button
                disabled={mappingBusy}
                onClick={() => handleConfirmCandidate(s.part.id)}
                style={{
                  background: '#1d4ed8',
                  color: '#fff',
                  border: 'none',
                  borderRadius: 4,
                  padding: '3px 8px',
                  fontSize: 11,
                  cursor: mappingBusy ? 'wait' : 'pointer',
                }}
                title="确认映射到这个标准物料"
              >
                ✓ 选这个
              </button>
            </div>
          ))}
          <button
            disabled={mappingBusy}
            onClick={handleCreateNew}
            style={{
              background: 'transparent',
              color: '#1d4ed8',
              border: '1px dashed #1d4ed8',
              borderRadius: 4,
              padding: '3px 8px',
              fontSize: 11,
              cursor: mappingBusy ? 'wait' : 'pointer',
            }}
            title="把当前节点作为一个新的公司标准物料"
          >
            {suggestions.length > 0 ? '× 都不是，新建标准物料' : '+ 新建标准物料'}
          </button>
          {mappingBusy && (
            <span style={{ marginLeft: 8, fontSize: 11, color: '#6b7280' }}>处理中…</span>
          )}
        </div>
      )}
      {specEntries.length > 0 && (
        <div style={{ color: '#374151', marginBottom: 6, lineHeight: 1.5 }}>
          {specEntries.map(([k, v]) => (
            <span
              key={k}
              style={{
                marginRight: 8,
                fontFamily: 'monospace',
                fontSize: 11,
                color: '#1f2937',
              }}
            >
              {k}={String(v)}
            </span>
          ))}
        </div>
      )}
      {node.category_id && (
        <BrandStrip
          brands={brands}
          fallback={fallbackBrands}
          loading={loadingBrands}
        />
      )}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {onOpenConfigurator && (
          // Distinct from chat prompts — this opens a UI modal instead.
          // Filled style (vs outline) signals it's the primary action.
          <button
            onClick={onOpenConfigurator}
            style={{
              background: '#1d4ed8',
              border: '1px solid #1d4ed8',
              color: '#fff',
              padding: '3px 8px',
              borderRadius: 4,
              fontSize: 12,
              cursor: 'pointer',
              fontWeight: 500,
            }}
          >
            🛠 选型
          </button>
        )}
        {prompts.map((p) => (
          <button
            key={p.label}
            onClick={() => onUseQuickPrompt(p.text)}
            style={{
              background: '#fff',
              border: '1px solid #bfdbfe',
              color: '#1d4ed8',
              padding: '3px 8px',
              borderRadius: 4,
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  )
}

interface ToolCallRecord {
  name: string
  summary: string
  ok: boolean
  mutated: boolean
}

interface Msg {
  role: 'user' | 'assistant'
  content: string
  toolCalls?: ToolCallRecord[]
  viewAction?: {
    label: string
    type: 'parts' | 'partDraft'
    query?: string | null
    draftId?: string | null
    allowImport?: boolean
  }
  partsPreview?: {
    items: Part[]
    total: number
  }
}

type WorkspaceView = 'bom' | 'parts'

interface PendingNavigation {
  from: WorkspaceView
  to: WorkspaceView
}

function viewLabel(view: WorkspaceView): string {
  return view === 'parts' ? '公司标准物料库' : 'BOM 详情页'
}

function isNegativeNavigationAnswer(text: string): boolean {
  const normalized = text.trim().toLowerCase()
  return /^(否|不|不用|不要|不需要|不留|不留在这里|返回|回去|退回|回上一页|返回上一页)$/.test(normalized)
}

function isPositiveNavigationAnswer(text: string): boolean {
  const normalized = text.trim().toLowerCase()
  return /^(是|好|好的|可以|留|停留|继续|留下|留在这里|就这里|yes|y|ok)$/.test(normalized)
}

function isReturnBomCommand(text: string): boolean {
  return /(返回|回到|回|打开|切到).*(bom|BOM|详情|工作台)|^(bom|BOM)详情页$/.test(text)
}

function mappingPrompt(node: BOMNode, mapping: MappingStatus): string {
  if (mapping.mapped_part) {
    return [
      `当前选中节点「${node.part_name}」已映射到公司标准物料：`,
      `${mapping.mapped_part.name_standard}${mapping.mapped_part.part_number ? ` / ${mapping.mapped_part.part_number}` : ''}`,
      '',
      '我可以继续帮你查看历史使用、供应商、品牌或风险信息。',
    ].join('\n')
  }
  const lines = [
    `当前选中节点「${node.part_name}」还没有映射到公司标准物料库。`,
    `节点 id：${node.id}`,
  ]
  if (node.part_number) lines.push(`零件号：${node.part_number}`)
  if (mapping.suggestions.length > 0) {
    lines.push('', '我找到了这些可能匹配的历史标准物料：')
    mapping.suggestions.forEach((s, idx) => {
      const p = s.part
      lines.push(
        `${idx + 1}. ${p.name_standard}${p.part_number ? ` / ${p.part_number}` : ''}（匹配度 ${Math.round(s.score * 100)}%，id=${p.id}，原因：${s.reason}）`,
      )
    })
    lines.push('', '你可以直接回复“选第一个”“用第二个”“新建一个”或“先跳过”。')
  } else {
    lines.push('', '暂时没有找到历史候选。你可以回复“新建一个”，我会把它保存为新的公司标准物料；也可以回复“先跳过”。')
  }
  return lines.join('\n')
}

function mappingScanPrompt(scan: MappingScan): string {
  const lines = [
    `我已扫描这个 BOM 的标准物料映射状态：共 ${scan.total_nodes} 个节点，${scan.confirmed_count} 个已映射，${scan.unmapped_count} 个还未确认。`,
  ]
  if (scan.candidate_count > 0) {
    lines.push(`其中 ${scan.candidate_count} 个节点已有历史候选，可以优先确认。`)
    const preview = scan.items
      .filter((item) => item.suggestions.length > 0)
      .slice(0, 5)
      .map((item, idx) => {
        const top = item.suggestions[0]
        return `${idx + 1}. ${item.node_label} → ${top.part.name_standard}（${Math.round(top.score * 100)}%）`
      })
    if (preview.length > 0) lines.push('', ...preview)
    lines.push('', '你可以回复“带我逐个确认”，我会按候选顺序引导你确认；也可以先点击某个节点单独处理。')
  } else if (scan.unmapped_count > 0) {
    lines.push('目前没有足够可靠的历史候选。你可以点击节点后让我帮你新建标准物料，系统会逐步积累公司物料库。')
  } else {
    lines.push('这个 BOM 当前都已经映射到标准物料，可以继续查看供应商、品牌、成本或风险信息。')
  }
  return lines.join('\n')
}

function partsMiniCharts(items: Part[]) {
  const countBy = (fn: (part: Part) => string) => {
    const counts = new Map<string, number>()
    for (const part of items) {
      const key = fn(part) || '未填写'
      counts.set(key, (counts.get(key) || 0) + 1)
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([label, value]) => ({ label, value }))
  }
  return {
    categories: countBy((p) => p.category_name || p.category_id || '未分类'),
    brands: countBy((p) => p.brand || '未指定'),
    statuses: countBy((p) => ({ active: '启用', pending: '待确认', inactive: '停用' }[p.status] || p.status)),
    usage: items.reduce((sum, p) => sum + (p.usage_count || 0), 0),
  }
}

function ChatMiniBars({
  rows,
  total,
  tone,
}: {
  rows: Array<{ label: string; value: number }>
  total: number
  tone: 'blue' | 'teal' | 'green'
}) {
  if (rows.length === 0) return <span className="chat-chart-empty">暂无数据</span>
  return (
    <div className={`chat-mini-bars ${tone}`}>
      {rows.map((row) => (
        <div key={row.label} className="chat-mini-bar-row">
          <span>{row.label}</span>
          <div><i style={{ width: `${total > 0 ? Math.max(8, (row.value / total) * 100) : 0}%` }} /></div>
          <b>{row.value}</b>
        </div>
      ))}
    </div>
  )
}

function PartsPreviewCard({ items, total }: { items: Part[]; total: number }) {
  const charts = partsMiniCharts(items)
  return (
    <div className="chat-parts-preview">
      <div className="chat-parts-preview-head">
        <strong>{total}</strong>
        <span>项标准物料</span>
        <b>{charts.usage}</b>
        <span>累计复用</span>
      </div>
      <div className="chat-chart-grid">
        <div>
          <h4>类目</h4>
          <ChatMiniBars rows={charts.categories} total={items.length} tone="blue" />
        </div>
        <div>
          <h4>品牌</h4>
          <ChatMiniBars rows={charts.brands} total={items.length} tone="teal" />
        </div>
        <div>
          <h4>状态</h4>
          <ChatMiniBars rows={charts.statuses} total={items.length} tone="green" />
        </div>
      </div>
    </div>
  )
}

export default function AgentSidebar({
  bomId,
  onBomUpdated,
  selectedNode,
  onClearSelection,
  onOpenConfigurator,
  onOpenParts,
  onOpenBom,
  onMappingScan,
  onOpenPartDraft,
  currentView = 'bom',
}: {
  bomId: string
  onBomUpdated?: () => void
  // The currently-selected BOMNode (set by clicking on the graph or table).
  // When non-null, a context card appears above the input box and quick
  // prompts target this node specifically.
  selectedNode?: BOMNode | null
  onClearSelection?: () => void
  // Called when the user clicks "🛠 选型" on the context card. Workspace
  // owns the modal so it can refresh the BOM after save.
  onOpenConfigurator?: () => void
  onOpenParts?: (opts?: { query?: string | null }) => void
  onOpenBom?: () => void
  onMappingScan?: (scan: MappingScan) => void
  onOpenPartDraft?: (draftId: string) => void
  currentView?: WorkspaceView
}) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [pendingNavigation, setPendingNavigation] = useState<PendingNavigation | null>(null)
  const [mappingScan, setMappingScan] = useState<MappingScan | null>(null)
  const [mappingScanLoading, setMappingScanLoading] = useState(false)
  const [mappingScanError, setMappingScanError] = useState<string | null>(null)
  const promptedMappingNodeRef = useRef<string | null>(null)
  const scannedBomRef = useRef<string | null>(null)
  const importFileRef = useRef<HTMLInputElement | null>(null)
  // Live phase string from backend, e.g. "思考中…", "执行工具 bom_update_node…"
  // Stays visible until the request completes so the user always knows the
  // agent is still working (fixes "no feedback" complaint).
  const [phase, setPhase] = useState<string>('')
  const [importingParts, setImportingParts] = useState(false)

  // Model picker — fetched from /agent/models on mount, persisted in
  // localStorage so user's choice survives reloads. The default is whatever
  // the backend says (settings.llm_model).
  const [models, setModels] = useState<ModelOption[]>([])
  const [selectedModel, setSelectedModel] = useState<string | null>(null)
  useEffect(() => {
    const ctrl = new AbortController()
    listModels(ctrl.signal)
      .then((r) => {
        setModels(r.models)
        // Restore saved choice if it's still in the registry, else fall back
        // to backend default.
        let saved: string | null = null
        try { saved = localStorage.getItem(MODEL_STORAGE_KEY) } catch { /* ignore */ }
        const savedValid = saved && r.models.some((m) => m.id === saved)
        setSelectedModel(savedValid ? saved : r.default)
      })
      .catch((ex) => {
        if (ex?.name !== 'AbortError') {
          // eslint-disable-next-line no-console
          console.warn('[AgentSidebar] listModels failed', ex)
        }
      })
    return () => ctrl.abort()
  }, [])
  useEffect(() => {
    if (selectedModel) {
      try { localStorage.setItem(MODEL_STORAGE_KEY, selectedModel) } catch { /* ignore */ }
    }
  }, [selectedModel])

  const loadMappingScan = useCallback((announce: boolean) => {
    if (!announce) scannedBomRef.current = null
    const ctrl = new AbortController()
    setMappingScanLoading(true)
    setMappingScanError(null)
    scanBOMMapping(bomId, ctrl.signal)
      .then((scan) => {
        if (ctrl.signal.aborted) return
        scannedBomRef.current = bomId
        setMappingScan(scan)
        onMappingScan?.(scan)
        if (announce && scan.total_nodes > 0) {
          setMsgs((prev) => {
            if (prev.some((m) => m.content.includes('我已扫描这个 BOM 的标准物料映射状态'))) return prev
            return [...prev, { role: 'assistant', content: mappingScanPrompt(scan) }]
          })
        }
      })
      .catch((ex) => {
        if (ex?.name !== 'AbortError') {
          // eslint-disable-next-line no-console
          console.warn('[AgentSidebar] scanBOMMapping failed', ex)
          setMappingScanError(ex?.message || String(ex))
        }
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setMappingScanLoading(false)
      })
    return ctrl
  }, [bomId])

  useEffect(() => {
    if (scannedBomRef.current === bomId) return
    const ctrl = loadMappingScan(true)
    return () => ctrl.abort()
  }, [bomId, loadMappingScan])

  useEffect(() => {
    if (!selectedNode) return
    const key = `${selectedNode.id}:${selectedNode.part_id || selectedNode.mapping_status}`
    if (promptedMappingNodeRef.current === key) return
    const ctrl = new AbortController()
    getNodeMapping(bomId, selectedNode.id, ctrl.signal)
      .then((mapping) => {
        if (ctrl.signal.aborted) return
        promptedMappingNodeRef.current = key
        if (mapping.status === 'confirmed' && mapping.mapped_part) return
        setMsgs((prev) => {
          const last = prev[prev.length - 1]
          const content = mappingPrompt(selectedNode, mapping)
          if (last?.role === 'assistant' && last.content === content) return prev
          return [...prev, { role: 'assistant', content }]
        })
      })
      .catch((ex) => {
        if (ex?.name !== 'AbortError') {
          // eslint-disable-next-line no-console
          console.warn('[AgentSidebar] getNodeMapping failed', ex)
        }
      })
    return () => ctrl.abort()
  }, [bomId, selectedNode])

  async function send(textOverride?: string) {
    const text = (textOverride ?? input).trim()
    if (!text || busy) return
    if (textOverride === undefined) setInput('')

    const appendLocalReply = (content: string) => {
      setMsgs((prev) => [
        ...prev,
        { role: 'user', content: text },
        { role: 'assistant', content },
      ])
    }

    const goToView = (view: WorkspaceView) => {
      if (view === 'parts') onOpenParts?.()
      else onOpenBom?.()
    }

    if (isReturnBomCommand(text)) {
      goToView('bom')
      setPendingNavigation(null)
      appendLocalReply('好的，已返回 BOM 详情页。')
      return
    }

    if (pendingNavigation) {
      if (isNegativeNavigationAnswer(text)) {
        goToView(pendingNavigation.from)
        appendLocalReply(`好的，已返回${viewLabel(pendingNavigation.from)}。`)
        setPendingNavigation(null)
        return
      }
      if (isPositiveNavigationAnswer(text)) {
        appendLocalReply(`好的，继续停留在${viewLabel(pendingNavigation.to)}。`)
        setPendingNavigation(null)
        return
      }
    }

    const history = msgs.map((m) => ({ role: m.role, content: m.content }))
    setMsgs((prev) => [
      ...prev,
      { role: 'user', content: text },
      { role: 'assistant', content: '', toolCalls: [] },
    ])
    setBusy(true)
    // Show "submitting" state immediately — first SSE event may take a sec.
    setPhase('正在发送请求…')

    const updateLast = (fn: (m: Msg) => Msg) =>
      setMsgs((prev) => {
        const copy = [...prev]
        copy[copy.length - 1] = fn(copy[copy.length - 1])
        return copy
      })

    try {
      for await (const evt of chatStream(bomId, text, history, selectedModel)) {
        if (evt.type === 'delta') {
          // First text token means the model is producing the reply.
          setPhase('回复生成中…')
          updateLast((m) => (
            m.viewAction?.type === 'parts'
              ? m
              : { ...m, content: m.content + evt.text }
          ))
        } else if (evt.type === 'tool_call') {
          if (evt.name === 'part_list' && evt.ok) {
            const q = typeof evt.args?.q === 'string' ? evt.args.q : null
            updateLast((m) => ({
              ...m,
              content: [
                m.content,
                `已生成「公司标准物料清单」。`,
              ].filter(Boolean).join('\n\n'),
              viewAction: {
                label: '点击查看',
                type: 'parts',
                query: q,
                allowImport: true,
              },
              partsPreview: {
                items: Array.isArray(evt.data?.items) ? (evt.data.items as Part[]) : [],
                total: typeof evt.data?.total === 'number' ? evt.data.total : 0,
              },
            }))
          } else if (evt.name === 'part_draft_from_text' && evt.ok) {
            const draftId = typeof evt.data?.draft_id === 'string' ? evt.data.draft_id : null
            updateLast((m) => ({
              ...m,
              content: [
                m.content,
                `已生成「标准物料导入预览」。`,
              ].filter(Boolean).join('\n\n'),
              viewAction: draftId
                ? {
                    label: '点击查看',
                    type: 'partDraft',
                    draftId,
                  }
                : m.viewAction,
            }))
          }
          setPhase(
            evt.ok
              ? `工具 ${evt.name} 已完成${evt.mutated ? '（已修改 BOM）' : ''}`
              : `工具 ${evt.name} 失败`,
          )
          updateLast((m) => ({
            ...m,
            toolCalls: [
              ...(m.toolCalls || []),
              {
                name: evt.name,
                summary: evt.summary,
                ok: evt.ok,
                mutated: evt.mutated,
              },
            ],
          }))
        } else if (evt.type === 'status') {
          setPhase(evt.phase)
        } else if (evt.type === 'bom_updated') {
          // eslint-disable-next-line no-console
          console.log('[AgentSidebar] bom_updated event → triggering reload')
          onBomUpdated?.()
        } else if (evt.type === 'error') {
          updateLast((m) => ({ ...m, content: `[错误] ${evt.message}` }))
        }
      }
    } catch (ex: any) {
      updateLast((m) => ({ ...m, content: `[错误] ${ex?.message || ex}` }))
    } finally {
      setBusy(false)
      setPhase('')
    }
  }

  const noMappingCount = mappingScan
    ? Math.max(0, mappingScan.unmapped_count - mappingScan.candidate_count)
    : 0

  async function handleImportPartsFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file || importingParts) return
    setImportingParts(true)
    setMsgs((prev) => [
      ...prev,
      { role: 'user', content: `导入自有物料文件：${file.name}` },
      { role: 'assistant', content: '正在识别文件格式和字段，生成导入预览...' },
    ])
    try {
      const draft = await uploadPartImportDraft(file)
      setMsgs((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant') {
          next[next.length - 1] = {
            ...last,
            content: `已生成「标准物料导入预览」，共 ${draft.rows.length} 行。请先检查，确认后再入库。`,
            viewAction: {
              label: '点击查看',
              type: 'partDraft',
              draftId: draft.id,
            },
          }
        }
        return next
      })
    } catch (ex: any) {
      setMsgs((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'assistant') {
          next[next.length - 1] = {
            ...last,
            content: `[错误] ${ex?.message || ex}`,
          }
        }
        return next
      })
    } finally {
      setImportingParts(false)
    }
  }

  return (
    <div className="agent-sidebar" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <input
        ref={importFileRef}
        type="file"
        accept=".xlsx,.xls,.xlsm,.csv,.tsv"
        style={{ display: 'none' }}
        onChange={handleImportPartsFile}
      />
      {/* Model picker — sits above the busy bar so it's always visible.
          Disabled while a request is in flight so users don't switch models
          mid-stream and confuse history. */}
      {models.length >= 1 && (
        <div
          style={{
            padding: '6px 12px',
            borderBottom: '1px solid #e5e7eb',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 12,
            color: '#374151',
            background: '#fafafa',
          }}
        >
          <span style={{ color: '#6b7280' }}>模型</span>
          <select
            value={selectedModel || ''}
            onChange={(e) => setSelectedModel(e.target.value || null)}
            disabled={busy}
            style={{
              flex: 1,
              padding: '3px 6px',
              border: '1px solid #d0d7de',
              borderRadius: 4,
              fontSize: 12,
              background: '#fff',
              cursor: busy ? 'not-allowed' : 'pointer',
            }}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      )}
      <div
        style={{
          padding: '10px 12px',
          borderBottom: '1px solid #dbe7fb',
          background: 'linear-gradient(135deg, rgba(239,246,255,0.96), rgba(245,250,255,0.88))',
          fontSize: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span style={{ color: '#0a1730', fontWeight: 700 }}>标准物料映射扫描</span>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            className="btn"
            disabled={mappingScanLoading}
            onClick={() => loadMappingScan(false)}
            style={{ padding: '3px 8px', fontSize: 12 }}
          >
            {mappingScanLoading ? '扫描中...' : '刷新'}
          </button>
        </div>
        {mappingScanError ? (
          <div style={{ color: '#b42318' }}>扫描失败：{mappingScanError}</div>
        ) : mappingScan ? (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6 }}>
              <div className="mapping-stat">
                <strong>{mappingScan.total_nodes}</strong>
                <span>总节点</span>
              </div>
              <div className="mapping-stat ok">
                <strong>{mappingScan.confirmed_count}</strong>
                <span>已映射</span>
              </div>
              <div className="mapping-stat warn">
                <strong>{noMappingCount}</strong>
                <span>未映射</span>
              </div>
              <div className="mapping-stat info">
                <strong>{mappingScan.candidate_count}</strong>
                <span>有候选</span>
              </div>
            </div>
            {mappingScan.candidate_count > 0 && (
              <div style={{ marginTop: 8, color: '#58667d', lineHeight: 1.5 }}>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() =>
                      send(
                        '请用 bom_confirm_all_suggested 工具一键确认所有 score ≥ 0.85 的强匹配候选。',
                      )
                    }
                    style={{
                      padding: '3px 8px',
                      fontSize: 12,
                      background: '#1d4ed8',
                      color: '#fff',
                      border: 'none',
                      borderRadius: 4,
                      cursor: busy ? 'wait' : 'pointer',
                    }}
                    title="批量确认 score ≥ 0.85 的高分候选；其他保持未映射等手动确认"
                  >
                    ⚡ 一键确认高分候选
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() => send('带我逐个确认这些候选。')}
                    style={{ padding: '3px 8px', fontSize: 12 }}
                  >
                    🧭 逐个确认
                  </button>
                </div>
                也可以直接输入“一键确认”或“带我逐个确认”给智能体下指令。
              </div>
            )}
            {mappingScan.candidate_count === 0 && noMappingCount > 0 && (
              // Empty state. Reached when the part library is too small (or
              // too dissimilar) to score-match this BOM's nodes. PEBS_BOM's
              // value compounds with use, so guide the user toward seeding
              // the library — don't leave the panel feeling broken.
              <div style={{ marginTop: 8, color: '#58667d', lineHeight: 1.5 }}>
                <div style={{ marginBottom: 6 }}>
                  公司物料库里暂时没有跟这 {noMappingCount} 个未映射节点相似的标准件。
                  PEBS 越用越聪明：把这些件沉淀进物料库后，下次再上传同类 BOM 就能自动建议复用。
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() =>
                      send(
                        '物料库里还没有相似件。请帮我从这个 BOM 里挑出最值得沉淀的 3-5 个关键节点（优先非标件、自制件、有规格参数的），逐个建议我新建为标准物料；每个先告诉我建议的 name_standard / category / brand，等我确认再调用 part_create_from_node。',
                      )
                    }
                    style={{
                      padding: '3px 8px',
                      fontSize: 12,
                      background: '#1d4ed8',
                      color: '#fff',
                      border: 'none',
                      borderRadius: 4,
                      cursor: busy ? 'wait' : 'pointer',
                    }}
                    title="让智能体挑出最值得沉淀的关键节点，逐个引导你建为标准物料"
                  >
                    🌱 引导沉淀关键件
                  </button>
                  {onOpenParts && (
                    <button
                      type="button"
                      className="btn"
                      onClick={() => onOpenParts()}
                      style={{ padding: '3px 8px', fontSize: 12 }}
                      title="跳到公司标准物料库列表"
                    >
                      📋 打开物料库
                    </button>
                  )}
                </div>
                <div style={{ marginTop: 4, fontSize: 11, color: '#8c98ad' }}>
                  也可以直接点 BOM 里某个节点，在右下卡片里『+ 新建标准物料』。
                </div>
              </div>
            )}
            {mappingScan.candidate_count === 0 && noMappingCount === 0 && (
              <div style={{ marginTop: 8, color: '#10b981', lineHeight: 1.5 }}>
                ✓ 这个 BOM 全部节点都已映射到标准物料。
              </div>
            )}
          </>
        ) : (
          <div style={{ color: '#58667d' }}>
            {mappingScanLoading ? '正在扫描全部 BOM 节点...' : '等待扫描结果...'}
          </div>
        )}
      </div>
      {busy && (
        <div
          style={{
            padding: '6px 12px',
            background: '#eff6ff',
            borderBottom: '1px solid #bfdbfe',
            color: '#1d4ed8',
            fontSize: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span
            aria-hidden="true"
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: '#1677ff',
              boxShadow: '0 0 0 0 rgba(22,119,255,0.6)',
              animation: 'agentPulse 1.2s infinite',
              flex: '0 0 auto',
            }}
          />
          <span style={{ flex: 1 }}>智能体工作中 · {phase || '处理中…'}</span>
          <span className="agent-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        </div>
      )}
      <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
        {msgs.length === 0 && (
          <p style={{ color: '#8b949e', fontSize: 13, lineHeight: 1.8 }}>
            试试：
            <br />内容编辑：
            <br />• "把螺钉 M4×10 的数量改成 30"
            <br />• "在电机组件下加一个编码器子节点，数量 1"
            <br />• "删除刹车总成及其子节点"
            <br />整张卡片：
            <br />• "高亮所有外购件"
            <br />• "给非标件加一个『非标』标签"
            <br />• "把已停产的零件变灰"
            <br />单个元素（slot）：
            <br />• "改一下基座的样式"（会列出可改清单）
            <br />• "把基座的进度条改成红色"
            <br />• "把右下角 100% 改成显示供应商"
            <br />非标件分类：
            <br />• "把这份 BOM 里的非标件分类一下"
            <br />• "基座是什么类目？规格是多少？"
            <br />供应商品牌库：
            <br />• "我们直线导轨用雅威达，国产中端，账期 30 天"
            <br />• "推荐几个滚珠丝杠的品牌"
            <br />• "我录过哪些品牌？"
            <br />• 直接粘贴 AVL 表格（多行）→ 自动批量入库
          </p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`chat-row ${m.role === 'user' ? 'user' : 'assistant'}`}>
            {m.role === 'assistant' && <div className="chat-avatar assistant">AI</div>}
            <div className="chat-bubble">
            <div
              className="chat-name"
            >
              {m.role === 'user' ? '你' : 'Assistant'}
            </div>
            {m.toolCalls && m.toolCalls.length > 0 && (
              <div style={{ marginBottom: 6 }}>
                {m.toolCalls.map((tc, j) => (
                  <div
                    key={j}
                    className={`tool-chip ${tc.ok ? (tc.mutated ? 'mutated' : 'ok') : 'failed'}`}
                  >
                    <span style={{ fontFamily: 'monospace', color: '#6b7280' }}>
                      {tc.mutated ? '✎ ' : '· '}{tc.name}
                    </span>
                    <span style={{ marginLeft: 6 }}>{tc.summary}</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ whiteSpace: 'pre-wrap', fontSize: 14, lineHeight: 1.6 }}>
              {m.content || (busy && i === msgs.length - 1 ? '...' : '')}
            </div>
            {m.viewAction && (
              <div className="chat-action-row">
                <button
                  type="button"
                  className="chat-view-link"
                  onClick={() => {
                    if (m.viewAction?.type === 'partDraft' && m.viewAction.draftId) {
                      onOpenPartDraft?.(m.viewAction.draftId)
                    } else {
                      onOpenParts?.({ query: m.viewAction?.query || null })
                    }
                  }}
                >
                  {m.viewAction.label}
                </button>
                {m.viewAction.allowImport && (
                  <button
                    type="button"
                    className="chat-view-link secondary"
                    disabled={importingParts}
                    onClick={() => importFileRef.current?.click()}
                  >
                    {importingParts ? '解析中...' : '导入自有物料'}
                  </button>
                )}
              </div>
            )}
            {m.partsPreview && (
              <PartsPreviewCard items={m.partsPreview.items} total={m.partsPreview.total} />
            )}
            </div>
            {m.role === 'user' && <div className="chat-avatar user">♙</div>}
          </div>
        ))}
      </div>
      {selectedNode && (
        <SelectionContextCard
          bomId={bomId}
          node={selectedNode}
          onClear={onClearSelection}
          onUseQuickPrompt={(text) => setInput(text)}
          onOpenConfigurator={onOpenConfigurator}
          onMappingChanged={onBomUpdated}
        />
      )}
      <div style={{ padding: 10, borderTop: '1px solid #e5e7eb', display: 'flex', gap: 8 }}>
        <input
          style={{
            flex: 1,
            padding: '8px 10px',
            border: '1px solid #d0d7de',
            borderRadius: 6,
            fontSize: 14,
          }}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          placeholder={
            selectedNode
              ? `针对『${selectedNode.part_name}』提问或下指令…`
              : '向智能体提问或下指令…'
          }
          disabled={busy}
        />
        <button className="btn btn-primary" onClick={() => send()} disabled={busy}>
          {busy ? '...' : '发送'}
        </button>
      </div>
    </div>
  )
}
