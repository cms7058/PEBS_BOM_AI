'use client'
import { useEffect, useState } from 'react'
import type { BrandRecommendation, ComponentCategory, Part } from '@/lib/api'
import {
  createBrand,
  createCategory,
  listBrands,
  listCategories,
  listParts,
  updatePart,
} from '@/lib/api'
import { useAppDialog } from './AppDialog'

type EditablePartField = 'sku_internal' | 'name_standard' | 'part_number' | 'category_id' | 'brand' | 'notes'

function editableValue(part: Part, field: EditablePartField): string {
  return String(part[field] || '')
}

export default function CompanyPartsPanel({
  refreshKey = 0,
}: {
  refreshKey?: number
}) {
  const dialog = useAppDialog()
  const [items, setItems] = useState<Part[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savingCell, setSavingCell] = useState<string | null>(null)
  const [categories, setCategories] = useState<ComponentCategory[]>([])
  const [brands, setBrands] = useState<BrandRecommendation[]>([])

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
      <div className="parts-table-wrap">
        <table className="parts-table">
          <thead>
            <tr>
              <th>标准物料</th>
              <th>内部 SKU</th>
              <th>零件号</th>
              <th>品牌</th>
              <th>类目</th>
              <th>备注</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr>
                <td colSpan={6} className="parts-empty">
                  暂无标准物料。点击 BOM 节点后，可让智能体引导你新建和确认映射。
                </td>
              </tr>
            )}
            {items.map((p) => (
              <tr key={p.id}>
                <td>
                  {editableCell(p, 'name_standard', '标准物料名')}
                  <small>{p.id.slice(0, 8)}</small>
                </td>
                <td>{editableCell(p, 'sku_internal')}</td>
                <td>{editableCell(p, 'part_number')}</td>
                <td>{brandCell(p)}</td>
                <td>{categoryCell(p)}</td>
                <td>{editableCell(p, 'notes')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
