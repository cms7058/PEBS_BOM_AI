'use client'
import { useEffect, useState } from 'react'
import type { BOMNode, BrandRecommendation, ModelOption } from '@/lib/api'
import { chatStream, listModels, recommendBrands } from '@/lib/api'

const MODEL_STORAGE_KEY = 'agent.model'

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

// Pinned card showing the currently-selected node, plus 4 quick prompts
// that pre-fill the input box (don't auto-send — user can edit before
// hitting Enter). The label always names the part so the model has the
// reference unambiguously when the prompt fires.
function SelectionContextCard({
  node,
  onClear,
  onUseQuickPrompt,
  onOpenConfigurator,
}: {
  node: BOMNode
  onClear?: () => void
  onUseQuickPrompt: (text: string) => void
  onOpenConfigurator?: () => void
}) {
  const ref = node.part_name || node.part_number || node.id.slice(0, 8)
  const specEntries = Object.entries(node.spec || {}).slice(0, 4)

  // Auto-fetch brand recommendations whenever the selected node is classified.
  // Avoids the user having to ask "推荐几个品牌" each time — the answer is
  // already there when they look at the card.
  const [brands, setBrands] = useState<BrandRecommendation[]>([])
  const [fallbackBrands, setFallbackBrands] = useState<string[]>([])
  const [loadingBrands, setLoadingBrands] = useState(false)
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
}

export default function AgentSidebar({
  bomId,
  onBomUpdated,
  selectedNode,
  onClearSelection,
  onOpenConfigurator,
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
}) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  // Live phase string from backend, e.g. "思考中…", "执行工具 bom_update_node…"
  // Stays visible until the request completes so the user always knows the
  // agent is still working (fixes "no feedback" complaint).
  const [phase, setPhase] = useState<string>('')

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

  async function send() {
    const text = input.trim()
    if (!text || busy) return
    setInput('')

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
          updateLast((m) => ({ ...m, content: m.content + evt.text }))
        } else if (evt.type === 'tool_call') {
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
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
          <div key={i} style={{ marginBottom: 14 }}>
            <div
              style={{
                fontSize: 12,
                color: m.role === 'user' ? '#1677ff' : '#10b981',
                marginBottom: 4,
              }}
            >
              {m.role === 'user' ? '你' : 'Assistant'}
            </div>
            {m.toolCalls && m.toolCalls.length > 0 && (
              <div style={{ marginBottom: 6 }}>
                {m.toolCalls.map((tc, j) => (
                  <div
                    key={j}
                    style={{
                      fontSize: 12,
                      padding: '4px 8px',
                      margin: '2px 0',
                      background: tc.ok ? (tc.mutated ? '#ecfdf3' : '#eff6ff') : '#fef2f2',
                      border: `1px solid ${tc.ok ? (tc.mutated ? '#10b981' : '#60a5fa') : '#ef4444'}`,
                      borderRadius: 4,
                      color: '#1f2329',
                    }}
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
          </div>
        ))}
      </div>
      {selectedNode && (
        <SelectionContextCard
          node={selectedNode}
          onClear={onClearSelection}
          onUseQuickPrompt={(text) => setInput(text)}
          onOpenConfigurator={onOpenConfigurator}
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
        <button className="btn btn-primary" onClick={send} disabled={busy}>
          {busy ? '...' : '发送'}
        </button>
      </div>
    </div>
  )
}
