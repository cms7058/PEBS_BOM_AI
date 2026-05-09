'use client'
import { useEffect, useMemo, useState } from 'react'
import type {
  BOMNode,
  BrandRecommendation,
  ComponentCategory,
  ParameterDef,
  Part,
} from '@/lib/api'
import {
  classifyNode,
  confirmNodeMapping,
  getUserName,
  listCategories,
  listParts,
  patchNode,
  recommendBrands,
} from '@/lib/api'
import { useAppDialog } from './AppDialog'

// ─── Selection configurator modal ────────────────────────────────────────
// Single-screen MVP: pick category at top, fill parameters in middle,
// browse / pick a brand at the bottom. Save commits both classification
// (category_id + spec) and supplier in two PATCH calls.
//
// Why no multi-step wizard: engineers don't want to click through 3 pages
// just to label one part as "linear_guide 25mm 1500mm". Everything visible,
// stuff-it-and-go.

interface Props {
  bomId: string
  node: BOMNode
  onClose: () => void
  onSaved: () => void
}

export default function SelectionConfiguratorModal({
  bomId,
  node,
  onClose,
  onSaved,
}: Props) {
  const dialog = useAppDialog()
  const [categories, setCategories] = useState<ComponentCategory[]>([])
  const [loadingCats, setLoadingCats] = useState(true)
  const [selectedCategoryId, setSelectedCategoryId] = useState<string | null>(
    node.category_id,
  )
  // Spec values keyed by param name. Initial seed = node.spec.
  const [spec, setSpec] = useState<Record<string, unknown>>(
    () => ({ ...(node.spec || {}) }),
  )
  // Supplier override — defaults to whatever node already has.
  const [supplier, setSupplier] = useState<string>(node.supplier || '')
  const [companyParts, setCompanyParts] = useState<Part[]>([])
  const [loadingParts, setLoadingParts] = useState(false)
  const [selectedPartId, setSelectedPartId] = useState<string | null>(node.part_id)

  const [saving, setSaving] = useState(false)
  const [errMsg, setErrMsg] = useState<string | null>(null)

  // Brand recommendations — refetched whenever category changes.
  const [brands, setBrands] = useState<BrandRecommendation[]>([])
  const [fallbackBrands, setFallbackBrands] = useState<string[]>([])

  useEffect(() => {
    const ctrl = new AbortController()
    setLoadingCats(true)
    listCategories(ctrl.signal)
      .then(setCategories)
      .catch((ex) => {
        if (ex?.name !== 'AbortError') setErrMsg(`加载类目失败: ${ex.message}`)
      })
      .finally(() => setLoadingCats(false))
    return () => ctrl.abort()
  }, [])

  useEffect(() => {
    if (!selectedCategoryId) {
      setBrands([])
      setFallbackBrands([])
      setCompanyParts([])
      setSelectedPartId(null)
      return
    }
    const ctrl = new AbortController()
    setLoadingParts(true)
    recommendBrands(selectedCategoryId, ctrl.signal)
      .then((r) => {
        setBrands(r.recommendations || [])
        setFallbackBrands(r.fallback_brands || [])
      })
      .catch((ex) => {
        if (ex?.name !== 'AbortError') {
          // eslint-disable-next-line no-console
          console.warn('[Configurator] recommendBrands failed', ex)
        }
      })
    listParts('', ctrl.signal, selectedCategoryId)
      .then((r) => setCompanyParts(r.items || []))
      .catch((ex) => {
        if (ex?.name !== 'AbortError') {
          // eslint-disable-next-line no-console
          console.warn('[Configurator] listParts failed', ex)
        }
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoadingParts(false)
      })
    return () => ctrl.abort()
  }, [selectedCategoryId])

  const selectedCategory = useMemo(
    () => categories.find((c) => c.id === selectedCategoryId) || null,
    [categories, selectedCategoryId],
  )

  // When category changes, prune spec to only keys allowed by the new
  // category's parameter schema. Otherwise the backend rejects the patch.
  useEffect(() => {
    if (!selectedCategory) return
    const allowed = new Set(selectedCategory.parameters.map((p) => p.name))
    setSpec((prev) => {
      const next: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(prev)) {
        if (allowed.has(k)) next[k] = v
      }
      return next
    })
  }, [selectedCategory])

  const setParamValue = (name: string, raw: string) => {
    setSpec((prev) => {
      const next = { ...prev }
      const param = selectedCategory?.parameters.find((p) => p.name === name)
      if (raw === '') {
        delete next[name]
      } else if (param?.type === 'number') {
        const v = Number(raw)
        next[name] = Number.isFinite(v) ? v : raw
      } else if (param?.type === 'integer') {
        const v = parseInt(raw, 10)
        next[name] = Number.isFinite(v) ? v : raw
      } else {
        next[name] = raw
      }
      return next
    })
  }

  const handleSave = async () => {
    setSaving(true)
    setErrMsg(null)
    try {
      // 1. Classification (atomic — backend validates spec against schema)
      await classifyNode(bomId, node.id, {
        category_id: selectedCategoryId,
        spec,
      })
      // 2. Supplier — only if user changed it
      if ((node.supplier || '') !== supplier) {
        await patchNode(bomId, node.id, {
          supplier: supplier.trim() || null,
        })
      }
      if (selectedPartId && selectedPartId !== node.part_id) {
        await confirmNodeMapping(bomId, node.id, selectedPartId, getUserName())
      }
      onSaved()
      onClose()
    } catch (ex: any) {
      setErrMsg(ex?.message || String(ex))
    } finally {
      setSaving(false)
    }
  }

  const handleClearClassification = async () => {
    if (!(await dialog.confirm('清除当前类目和规格参数？'))) return
    setSaving(true)
    setErrMsg(null)
    try {
      await classifyNode(bomId, node.id, { category_id: null })
      onSaved()
      onClose()
    } catch (ex: any) {
      setErrMsg(ex?.message || String(ex))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15, 23, 42, 0.5)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff',
          borderRadius: 8,
          boxShadow: '0 20px 60px rgba(0,0,0,0.25)',
          width: '100%',
          maxWidth: 720,
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '14px 20px',
            borderBottom: '1px solid #e5e7eb',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <span style={{ fontSize: 16, fontWeight: 600 }}>选型配置</span>
          <span style={{ color: '#6b7280', fontSize: 14 }}>
            {node.part_name}
            {node.part_number && node.part_number !== node.part_name && (
              <span style={{ marginLeft: 6 }}>· {node.part_number}</span>
            )}
          </span>
          <span style={{ flex: 1 }} />
          <button
            onClick={onClose}
            aria-label="关闭"
            style={{
              border: 'none',
              background: 'transparent',
              fontSize: 20,
              color: '#6b7280',
              cursor: 'pointer',
            }}
          >
            ×
          </button>
        </div>

        {/* Body — scrollable */}
        <div style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
          {errMsg && (
            <div
              style={{
                padding: '8px 12px',
                background: '#fef2f2',
                border: '1px solid #fca5a5',
                color: '#991b1b',
                borderRadius: 6,
                marginBottom: 12,
                fontSize: 13,
              }}
            >
              {errMsg}
            </div>
          )}

          {/* 1) Category picker */}
          <Section title="① 类目">
            {loadingCats ? (
              <span style={{ color: '#6b7280', fontSize: 13 }}>加载中…</span>
            ) : (
              <select
                value={selectedCategoryId || ''}
                onChange={(e) => setSelectedCategoryId(e.target.value || null)}
                style={selectStyle}
              >
                <option value="">— 未分类 —</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name_zh} ({c.name_en})
                  </option>
                ))}
              </select>
            )}
            {selectedCategory?.description && (
              <p style={{ color: '#6b7280', fontSize: 12, margin: '6px 0 0 0' }}>
                {selectedCategory.description}
              </p>
            )}
          </Section>

          {/* 2) Parameter form */}
          {selectedCategory && (
            <Section title="② 规格参数">
              {selectedCategory.parameters.length === 0 ? (
                <span style={{ color: '#6b7280', fontSize: 13 }}>
                  此类目暂无参数定义。
                </span>
              ) : (
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '1fr 1fr',
                    gap: '8px 16px',
                  }}
                >
                  {selectedCategory.parameters.map((p) => (
                    <ParamField
                      key={p.name}
                      param={p}
                      value={spec[p.name]}
                      onChange={(v) => setParamValue(p.name, v)}
                    />
                  ))}
                </div>
              )}
            </Section>
          )}

          {/* 3) Company standard parts */}
          {selectedCategoryId && (
            <Section title="③ 公司自有物料（可选）">
              {loadingParts ? (
                <span style={{ color: '#6b7280', fontSize: 13 }}>正在加载公司标准物料…</span>
              ) : companyParts.length === 0 ? (
                <span style={{ color: '#6b7280', fontSize: 13 }}>
                  当前类目下暂无公司标准物料。保存分类后，可以通过智能体新建标准物料。
                </span>
              ) : (
                <div style={{ display: 'grid', gap: 8 }}>
                  {companyParts.slice(0, 12).map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => {
                        setSelectedPartId(selectedPartId === p.id ? null : p.id)
                        if (p.brand) setSupplier(p.brand)
                        else if (p.supplier) setSupplier(p.supplier)
                      }}
                      style={{
                        textAlign: 'left',
                        border: `1px solid ${selectedPartId === p.id ? '#1783FF' : '#d8e2f3'}`,
                        background: selectedPartId === p.id ? '#eff6ff' : '#fff',
                        borderRadius: 6,
                        padding: '8px 10px',
                        cursor: 'pointer',
                      }}
                    >
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <strong style={{ color: '#0a1730', fontSize: 13 }}>
                          {p.name_standard}
                        </strong>
                        {p.part_number && (
                          <span style={{ color: '#58667d', fontSize: 12 }}>{p.part_number}</span>
                        )}
                        <span style={{ flex: 1 }} />
                        <span style={{ color: '#7d8aa0', fontSize: 11 }}>
                          用过 {p.usage_count || 0} 次
                        </span>
                      </div>
                      <div style={{ color: '#7d8aa0', fontSize: 12, marginTop: 3 }}>
                        {[p.sku_internal, p.brand, p.supplier, p.typical_lead_time]
                          .filter(Boolean)
                          .join(' · ') || '暂无补充信息'}
                      </div>
                    </button>
                  ))}
                  {companyParts.length > 12 && (
                    <span style={{ color: '#7d8aa0', fontSize: 12 }}>
                      还有 {companyParts.length - 12} 个物料，可到公司标准物料库继续筛选。
                    </span>
                  )}
                </div>
              )}
            </Section>
          )}

          {/* 4) Brand recommendations */}
          {selectedCategoryId && (
            <Section title="④ 选品牌（可选）">
              <input
                type="text"
                value={supplier}
                onChange={(e) => setSupplier(e.target.value)}
                placeholder="点击下方品牌一键填充，或手动输入"
                style={{ ...selectStyle, width: '100%', boxSizing: 'border-box' }}
              />
              <div
                style={{
                  display: 'flex',
                  gap: 6,
                  flexWrap: 'wrap',
                  marginTop: 8,
                }}
              >
                {brands.length > 0
                  ? brands.map((b) => (
                      <BrandChip
                        key={b.id}
                        label={b.name}
                        trust={b.trust}
                        meta={[b.region, b.price_tier].filter(Boolean).join(' · ')}
                        active={supplier === b.name}
                        onClick={() => setSupplier(b.name)}
                      />
                    ))
                  : fallbackBrands.map((name) => (
                      <BrandChip
                        key={name}
                        label={name}
                        trust="generic"
                        active={supplier === name}
                        onClick={() => setSupplier(name)}
                      />
                    ))}
              </div>
              {brands.length === 0 && fallbackBrands.length > 0 && (
                <p style={{ color: '#6b7280', fontSize: 11, margin: '6px 0 0 0' }}>
                  ↑ 通用参考品牌（尚未录入私有 KB）。在 chat 里说『我们 X
                  类目用 Y 牌』可以加入私有库，下次选型这里就会优先显示。
                </p>
              )}
            </Section>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '12px 20px',
            borderTop: '1px solid #e5e7eb',
            display: 'flex',
            gap: 8,
            background: '#fafafa',
          }}
        >
          {node.category_id && (
            <button
              onClick={handleClearClassification}
              disabled={saving}
              style={{ ...btnSecondaryStyle, color: '#b91c1c' }}
            >
              清除分类
            </button>
          )}
          <span style={{ flex: 1 }} />
          <button onClick={onClose} disabled={saving} style={btnSecondaryStyle}>
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving || (selectedCategoryId === null && !node.category_id)}
            style={btnPrimaryStyle}
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Subcomponents ───────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 18 }}>
      <h3
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: '#1f2937',
          margin: '0 0 8px 0',
        }}
      >
        {title}
      </h3>
      {children}
    </section>
  )
}

function ParamField({
  param,
  value,
  onChange,
}: {
  param: ParameterDef
  value: unknown
  onChange: (v: string) => void
}) {
  const v = value == null ? '' : String(value)
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, color: '#374151' }}>
        {param.label_zh}
        {param.unit && (
          <span style={{ color: '#9ca3af' }}> ({param.unit})</span>
        )}
        {param.required && <span style={{ color: '#dc2626' }}> *</span>}
      </span>
      {param.type === 'enum' && param.values ? (
        <select value={v} onChange={(e) => onChange(e.target.value)} style={selectStyle}>
          <option value="">—</option>
          {param.values.map((opt) => (
            <option key={String(opt)} value={String(opt)}>
              {opt}
            </option>
          ))}
        </select>
      ) : param.type === 'number' || param.type === 'integer' ? (
        <input
          type="number"
          step={param.type === 'integer' ? 1 : 'any'}
          value={v}
          onChange={(e) => onChange(e.target.value)}
          style={selectStyle}
        />
      ) : (
        <input
          type="text"
          value={v}
          onChange={(e) => onChange(e.target.value)}
          style={selectStyle}
        />
      )}
    </label>
  )
}

const TRUST_TAG: Record<string, { label: string; color: string }> = {
  private: { label: '★★ 私有', color: '#15803d' },
  'shared-by-you': { label: '★ 共享', color: '#0369a1' },
  community: { label: '· 社区', color: '#6b7280' },
  generic: { label: '通用', color: '#9ca3af' },
}

function BrandChip({
  label,
  trust,
  meta,
  active,
  onClick,
}: {
  label: string
  trust: string
  meta?: string
  active?: boolean
  onClick: () => void
}) {
  const tag = TRUST_TAG[trust] || TRUST_TAG.community
  return (
    <button
      onClick={onClick}
      title={meta}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '4px 10px',
        background: active ? '#1d4ed8' : '#fff',
        border: `1px solid ${active ? '#1d4ed8' : '#bfdbfe'}`,
        color: active ? '#fff' : '#1f2937',
        borderRadius: 6,
        fontSize: 12,
        cursor: 'pointer',
      }}
    >
      <span
        style={{
          color: active ? '#dbeafe' : tag.color,
          fontSize: 11,
          fontWeight: 600,
        }}
      >
        {tag.label}
      </span>
      {label}
    </button>
  )
}

const selectStyle: React.CSSProperties = {
  padding: '6px 10px',
  border: '1px solid #d1d5db',
  borderRadius: 4,
  fontSize: 13,
  background: '#fff',
  fontFamily: 'inherit',
}

const btnPrimaryStyle: React.CSSProperties = {
  padding: '6px 14px',
  background: '#1677ff',
  color: '#fff',
  border: 'none',
  borderRadius: 4,
  fontSize: 13,
  cursor: 'pointer',
}

const btnSecondaryStyle: React.CSSProperties = {
  padding: '6px 14px',
  background: '#fff',
  color: '#374151',
  border: '1px solid #d1d5db',
  borderRadius: 4,
  fontSize: 13,
  cursor: 'pointer',
}
