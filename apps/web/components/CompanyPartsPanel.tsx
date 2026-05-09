'use client'
import { useEffect, useMemo, useState } from 'react'
import type { BrandRecommendation, ComponentCategory, Part, PartDetail } from '@/lib/api'
import {
  createBrand,
  createCategory,
  getPartDetail,
  listBrands,
  listCategories,
  listParts,
  updatePart,
} from '@/lib/api'
import { useAppDialog } from './AppDialog'

type EditablePartField =
  | 'sku_internal'
  | 'name_standard'
  | 'part_number'
  | 'category_id'
  | 'brand'
  | 'supplier'
  | 'uom'
  | 'typical_lead_time'
  | 'notes'

function editableValue(part: Part, field: EditablePartField): string {
  return part[field] == null ? '' : String(part[field])
}

function formatDateTime(value: string | null): string {
  if (!value) return '-'
  const date = new Date(value.endsWith('Z') ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function MiniBars({
  rows,
  total,
  tone,
}: {
  rows: Array<{ label: string; value: number }>
  total: number
  tone: 'blue' | 'teal' | 'green'
}) {
  if (rows.length === 0) return <p className="parts-chart-empty">暂无数据</p>
  return (
    <div className={`mini-bars ${tone}`}>
      {rows.map((row) => (
        <div key={row.label} className="mini-bar-row">
          <span>{row.label}</span>
          <div>
            <i style={{ width: `${total > 0 ? Math.max(6, (row.value / total) * 100) : 0}%` }} />
          </div>
          <b>{row.value}</b>
        </div>
      ))}
    </div>
  )
}

export default function CompanyPartsPanel({
  refreshKey = 0,
  initialQuery = '',
}: {
  refreshKey?: number
  initialQuery?: string
}) {
  const dialog = useAppDialog()
  const [items, setItems] = useState<Part[]>([])
  const [query, setQuery] = useState(initialQuery)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savingCell, setSavingCell] = useState<string | null>(null)
  const [categories, setCategories] = useState<ComponentCategory[]>([])
  const [brands, setBrands] = useState<BrandRecommendation[]>([])
  const [selectedPartId, setSelectedPartId] = useState<string | null>(null)
  const [partDetail, setPartDetail] = useState<PartDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  useEffect(() => {
    setQuery(initialQuery)
  }, [initialQuery])

  useEffect(() => {
    const ctrl = new AbortController()
    setLoading(true)
    setError(null)
    listParts(query, ctrl.signal)
      .then((r) => setItems(r.items || []))
      .catch((ex) => {
        if (ex?.name !== 'AbortError') setError(ex?.message || String(ex))
      })
      .finally(() => setLoading(false))
    return () => ctrl.abort()
  }, [query, refreshKey])

  useEffect(() => {
    const ctrl = new AbortController()
    Promise.all([listCategories(ctrl.signal), listBrands(ctrl.signal)])
      .then(([nextCategories, nextBrands]) => {
        setCategories(nextCategories)
        setBrands(nextBrands.brands || [])
      })
      .catch((ex) => {
        if (ex?.name !== 'AbortError') setError(ex?.message || String(ex))
      })
    return () => ctrl.abort()
  }, [refreshKey])

  useEffect(() => {
    if (!selectedPartId) {
      setPartDetail(null)
      setDetailError(null)
      return
    }
    const ctrl = new AbortController()
    setDetailLoading(true)
    setDetailError(null)
    getPartDetail(selectedPartId, ctrl.signal)
      .then(setPartDetail)
      .catch((ex) => {
        if (ex?.name !== 'AbortError') setDetailError(ex?.message || String(ex))
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setDetailLoading(false)
      })
    return () => ctrl.abort()
  }, [selectedPartId, items])

  async function saveField(part: Part, field: EditablePartField, rawValue: string) {
    const value = rawValue.trim()
    const nextValue = value || null
    const previousValue = editableValue(part, field)
    if (value === previousValue) return
    if (field === 'name_standard' && !value) {
      setError('标准物料名不能为空。')
      return
    }

    const cellKey = `${part.id}:${field}`
    setSavingCell(cellKey)
    setError(null)
    setItems((prev) => prev.map((p) => (
      p.id === part.id ? { ...p, [field]: field === 'name_standard' ? value : nextValue } : p
    )))
    try {
      const updated = await updatePart(part.id, {
        [field]: field === 'name_standard' ? value : nextValue,
      })
      setItems((prev) => prev.map((p) => (p.id === part.id ? updated : p)))
      if (partDetail?.part.id === updated.id) {
        setPartDetail((prev) => prev ? { ...prev, part: updated } : prev)
      }
    } catch (ex: any) {
      setItems((prev) => prev.map((p) => (p.id === part.id ? part : p)))
      setError(ex?.message || String(ex))
    } finally {
      setSavingCell(null)
    }
  }

  function editableCell(part: Part, field: EditablePartField, placeholder = '-') {
    const cellKey = `${part.id}:${field}`
    return (
      <input
        key={`${cellKey}:${editableValue(part, field)}`}
        className="parts-cell-input"
        defaultValue={editableValue(part, field)}
        placeholder={placeholder}
        disabled={savingCell === cellKey}
        onBlur={(e) => saveField(part, field, e.currentTarget.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur()
          if (e.key === 'Escape') {
            e.currentTarget.value = editableValue(part, field)
            e.currentTarget.blur()
          }
        }}
      />
    )
  }

  async function saveCategory(part: Part, categoryId: string | null) {
    const previous = part.category_id || null
    if ((categoryId || null) === previous) return
    const cellKey = `${part.id}:category_id`
    setSavingCell(cellKey)
    setError(null)
    setItems((prev) => prev.map((p) => (
      p.id === part.id
        ? {
            ...p,
            category_id: categoryId,
            category_name: categories.find((c) => c.id === categoryId)?.name_zh || null,
          }
        : p
    )))
    try {
      const updated = await updatePart(part.id, { category_id: categoryId })
      setItems((prev) => prev.map((p) => (p.id === part.id ? updated : p)))
      if (partDetail?.part.id === updated.id) {
        setPartDetail((prev) => prev ? { ...prev, part: updated } : prev)
      }
    } catch (ex: any) {
      setItems((prev) => prev.map((p) => (p.id === part.id ? part : p)))
      setError(ex?.message || String(ex))
    } finally {
      setSavingCell(null)
    }
  }

  async function createAndApplyCategory(part: Part) {
    const name = await dialog.prompt('新增类目名称：')
    const trimmed = name?.trim()
    if (!trimmed) return
    const cellKey = `${part.id}:category_id`
    setSavingCell(cellKey)
    setError(null)
    try {
      const category = await createCategory(trimmed)
      setCategories((prev) => (
        prev.some((c) => c.id === category.id) ? prev : [...prev, category]
      ))
      await saveCategory(part, category.id)
    } catch (ex: any) {
      setError(ex?.message || String(ex))
    } finally {
      setSavingCell(null)
    }
  }

  function categoryCell(part: Part) {
    const cellKey = `${part.id}:category_id`
    return (
      <select
        className="parts-cell-select"
        value={part.category_id || ''}
        disabled={savingCell === cellKey}
        onChange={(e) => {
          const value = e.currentTarget.value
          if (value === '__new__') {
            createAndApplyCategory(part)
            return
          }
          saveCategory(part, value || null)
        }}
      >
        <option value="">未分类</option>
        {categories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name_zh}
          </option>
        ))}
        <option value="__new__">+ 新增类目...</option>
      </select>
    )
  }

  async function saveBrand(part: Part, brandName: string | null) {
    const previous = part.brand || null
    const next = brandName?.trim() || null
    if (next === previous) return
    await saveField(part, 'brand', next || '')
  }

  async function createAndApplyBrand(part: Part) {
    const name = await dialog.prompt('新增品牌名称：')
    const trimmed = name?.trim()
    if (!trimmed) return
    const cellKey = `${part.id}:brand`
    setSavingCell(cellKey)
    setError(null)
    try {
      const brand = await createBrand(trimmed, part.category_id ? [part.category_id] : [])
      setBrands((prev) => (
        prev.some((b) => b.id === brand.id) ? prev : [...prev, brand].sort((a, b) => a.name.localeCompare(b.name))
      ))
      await saveBrand(part, brand.name)
    } catch (ex: any) {
      setError(ex?.message || String(ex))
    } finally {
      setSavingCell(null)
    }
  }

  function brandCell(part: Part) {
    const cellKey = `${part.id}:brand`
    const knownBrandNames = new Set(brands.map((b) => b.name))
    const hasLegacyBrand = part.brand && !knownBrandNames.has(part.brand)
    return (
      <select
        className="parts-cell-select"
        value={part.brand || ''}
        disabled={savingCell === cellKey}
        onChange={(e) => {
          const value = e.currentTarget.value
          if (value === '__new__') {
            createAndApplyBrand(part)
            return
          }
          saveBrand(part, value || null)
        }}
      >
        <option value="">未指定</option>
        {hasLegacyBrand && (
          <option value={part.brand || ''}>{part.brand}</option>
        )}
        {brands.map((b) => (
          <option key={b.id} value={b.name}>
            {b.name}
          </option>
        ))}
        <option value="__new__">+ 新增品牌...</option>
      </select>
    )
  }

  async function saveUnitCost(part: Part, rawValue: string) {
    const value = rawValue.trim()
    const nextValue = value ? Number(value) : null
    if (value && Number.isNaN(nextValue)) {
      setError('单价必须是数字。')
      return
    }
    if ((part.unit_cost ?? null) === nextValue) return
    const cellKey = `${part.id}:unit_cost`
    setSavingCell(cellKey)
    setError(null)
    setItems((prev) => prev.map((p) => (
      p.id === part.id ? { ...p, unit_cost: nextValue } : p
    )))
    try {
      const updated = await updatePart(part.id, { unit_cost: nextValue })
      setItems((prev) => prev.map((p) => (p.id === part.id ? updated : p)))
      if (partDetail?.part.id === updated.id) {
        setPartDetail((prev) => prev ? { ...prev, part: updated } : prev)
      }
    } catch (ex: any) {
      setItems((prev) => prev.map((p) => (p.id === part.id ? part : p)))
      setError(ex?.message || String(ex))
    } finally {
      setSavingCell(null)
    }
  }

  function unitCostCell(part: Part) {
    const cellKey = `${part.id}:unit_cost`
    const value = part.unit_cost == null ? '' : String(part.unit_cost)
    return (
      <input
        key={`${cellKey}:${value}`}
        className="parts-cell-input"
        defaultValue={value}
        inputMode="decimal"
        placeholder="-"
        disabled={savingCell === cellKey}
        onBlur={(e) => saveUnitCost(part, e.currentTarget.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur()
          if (e.key === 'Escape') {
            e.currentTarget.value = value
            e.currentTarget.blur()
          }
        }}
      />
    )
  }

  async function saveStatus(part: Part, status: Part['status']) {
    if (part.status === status) return
    const cellKey = `${part.id}:status`
    setSavingCell(cellKey)
    setError(null)
    setItems((prev) => prev.map((p) => (p.id === part.id ? { ...p, status } : p)))
    try {
      const updated = await updatePart(part.id, { status })
      setItems((prev) => prev.map((p) => (p.id === part.id ? updated : p)))
      if (partDetail?.part.id === updated.id) {
        setPartDetail((prev) => prev ? { ...prev, part: updated } : prev)
      }
    } catch (ex: any) {
      setItems((prev) => prev.map((p) => (p.id === part.id ? part : p)))
      setError(ex?.message || String(ex))
    } finally {
      setSavingCell(null)
    }
  }

  function statusCell(part: Part) {
    const cellKey = `${part.id}:status`
    return (
      <select
        className="parts-cell-select compact"
        value={part.status || 'active'}
        disabled={savingCell === cellKey}
        onChange={(e) => saveStatus(part, e.currentTarget.value as Part['status'])}
      >
        <option value="active">启用</option>
        <option value="pending">待确认</option>
        <option value="inactive">停用</option>
      </select>
    )
  }

  const charts = useMemo(() => {
    const countBy = (fn: (part: Part) => string) => {
      const counts = new Map<string, number>()
      for (const part of items) {
        const key = fn(part) || '未填写'
        counts.set(key, (counts.get(key) || 0) + 1)
      }
      return [...counts.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(([label, value]) => ({ label, value }))
    }
    return {
      categories: countBy((p) => p.category_name || p.category_id || '未分类'),
      brands: countBy((p) => p.brand || '未指定'),
      statuses: countBy((p) => ({ active: '启用', pending: '待确认', inactive: '停用' }[p.status] || p.status)),
      totalUsage: items.reduce((sum, p) => sum + (p.usage_count || 0), 0),
    }
  }, [items])

  return (
    <div className="parts-panel">
      <div className="parts-toolbar">
        <div>
          <h2>公司标准物料库</h2>
          <p>{loading ? '正在加载…' : `共 ${items.length} 项标准物料`}</p>
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索名称、料号、品牌…"
        />
      </div>
      {error && <div className="parts-error">{error}</div>}
      <div className="parts-chart-strip">
        <div className="parts-chart-card">
          <h3>类目分布</h3>
          <MiniBars rows={charts.categories} total={items.length} tone="blue" />
        </div>
        <div className="parts-chart-card">
          <h3>品牌分布</h3>
          <MiniBars rows={charts.brands} total={items.length} tone="teal" />
        </div>
        <div className="parts-chart-card">
          <h3>状态</h3>
          <MiniBars rows={charts.statuses} total={items.length} tone="green" />
        </div>
        <div className="parts-chart-card metric">
          <h3>历史复用</h3>
          <strong>{charts.totalUsage}</strong>
          <span>累计映射次数</span>
        </div>
      </div>
      <div className={`parts-content ${selectedPartId ? 'with-detail' : ''}`}>
      <div className="parts-table-wrap">
        <table className="parts-table">
          <thead>
            <tr>
              <th>标准物料</th>
              <th>内部 SKU</th>
              <th>零件号</th>
              <th>品牌</th>
              <th>供应商</th>
              <th>类目</th>
              <th>单位</th>
              <th>单价</th>
              <th>货期</th>
              <th>状态</th>
              <th>使用</th>
              <th>备注</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr>
                <td colSpan={12} className="parts-empty">
                  暂无标准物料。点击 BOM 节点后，可让智能体引导你新建和确认映射。
                </td>
              </tr>
            )}
            {items.map((p) => (
              <tr
                key={p.id}
                className={selectedPartId === p.id ? 'selected' : ''}
                onClick={() => setSelectedPartId(p.id)}
              >
                <td>
                  {editableCell(p, 'name_standard', '标准物料名')}
                  <small>{p.id.slice(0, 8)}</small>
                </td>
                <td>{editableCell(p, 'sku_internal')}</td>
                <td>{editableCell(p, 'part_number')}</td>
                <td>{brandCell(p)}</td>
                <td>{editableCell(p, 'supplier')}</td>
                <td>{categoryCell(p)}</td>
                <td>{editableCell(p, 'uom', 'EA')}</td>
                <td>{unitCostCell(p)}</td>
                <td>{editableCell(p, 'typical_lead_time', '如 7 天')}</td>
                <td>{statusCell(p)}</td>
                <td>
                  <span className="parts-usage">{p.usage_count || 0} 次</span>
                  <small>{formatDateTime(p.last_used_at)}</small>
                </td>
                <td>{editableCell(p, 'notes')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selectedPartId && (
        <aside className="part-detail-panel">
          <div className="part-detail-head">
            <div>
              <h3>{partDetail?.part.name_standard || '标准物料详情'}</h3>
              <p>{partDetail?.part.part_number || partDetail?.part.sku_internal || selectedPartId.slice(0, 8)}</p>
            </div>
            <button type="button" className="btn" onClick={() => setSelectedPartId(null)}>
              关闭
            </button>
          </div>
          {detailError && <div className="parts-error">{detailError}</div>}
          {detailLoading && !partDetail ? (
            <div className="part-detail-empty">正在加载详情...</div>
          ) : partDetail ? (
            <div className="part-detail-body">
              <dl className="part-kv">
                <div><dt>类目</dt><dd>{partDetail.part.category_name || '-'}</dd></div>
                <div><dt>品牌</dt><dd>{partDetail.part.brand || '-'}</dd></div>
                <div><dt>供应商</dt><dd>{partDetail.part.supplier || '-'}</dd></div>
                <div><dt>单价</dt><dd>{partDetail.part.unit_cost == null ? '-' : partDetail.part.unit_cost}</dd></div>
                <div><dt>货期</dt><dd>{partDetail.part.typical_lead_time || '-'}</dd></div>
                <div><dt>状态</dt><dd>{partDetail.part.status}</dd></div>
                <div><dt>使用次数</dt><dd>{partDetail.part.usage_count || 0} 次</dd></div>
                <div><dt>最近使用</dt><dd>{formatDateTime(partDetail.part.last_used_at)}</dd></div>
              </dl>

              <section>
                <h4>历史引用</h4>
                {partDetail.references.length === 0 ? (
                  <p className="part-detail-empty">暂无 BOM 节点引用。</p>
                ) : (
                  <ul className="part-ref-list">
                    {partDetail.references.map((r) => (
                      <li key={`${r.bom_id}:${r.node_id}`}>
                        <strong>{r.node_label}</strong>
                        <span>{r.bom_name}</span>
                        <small>
                          {[r.part_number, `${r.quantity}${r.uom}`, r.supplier, r.unit_cost == null ? null : `¥${r.unit_cost}`]
                            .filter(Boolean)
                            .join(' · ')}
                        </small>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section>
                <h4>历史写法</h4>
                {partDetail.aliases.length === 0 ? (
                  <p className="part-detail-empty">暂无别名记录。</p>
                ) : (
                  <ul className="part-alias-list">
                    {partDetail.aliases.map((a, idx) => (
                      <li key={`${a.raw_name}:${a.raw_part_number || ''}:${idx}`}>
                        {a.raw_name}
                        {a.raw_part_number && <span> / {a.raw_part_number}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          ) : null}
        </aside>
      )}
      </div>
    </div>
  )
}
