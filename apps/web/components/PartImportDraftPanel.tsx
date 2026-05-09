'use client'
import { useEffect, useState } from 'react'
import type { PartImportDraft } from '@/lib/api'
import { confirmPartImportDraft, getPartImportDraft } from '@/lib/api'

export default function PartImportDraftPanel({
  draftId,
  onConfirmed,
}: {
  draftId: string
  onConfirmed?: () => void
}) {
  const [draft, setDraft] = useState<PartImportDraft | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    setLoading(true)
    setError(null)
    getPartImportDraft(draftId, ctrl.signal)
      .then(setDraft)
      .catch((ex) => {
        if (ex?.name !== 'AbortError') setError(ex?.message || String(ex))
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false)
      })
    return () => ctrl.abort()
  }, [draftId])

  async function confirmDraft() {
    setSaving(true)
    setError(null)
    try {
      const result = await confirmPartImportDraft(draftId)
      setDraft(result.draft)
      onConfirmed?.()
    } catch (ex: any) {
      setError(ex?.message || String(ex))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="draft-panel">
      <div className="parts-toolbar">
        <div>
          <h2>标准物料导入预览</h2>
          <p>{loading ? '正在加载…' : `共 ${draft?.rows.length || 0} 行 · ${draft?.status || 'draft'}`}</p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!draft || draft.status !== 'draft' || saving}
          onClick={confirmDraft}
        >
          {saving ? '入库中...' : '确认入库'}
        </button>
      </div>
      {error && <div className="parts-error">{error}</div>}
      <div className="parts-table-wrap">
        <table className="parts-table">
          <thead>
            <tr>
              <th>操作</th>
              <th>标准物料</th>
              <th>料号</th>
              <th>类目</th>
              <th>品牌</th>
              <th>供应商</th>
              <th>单价</th>
              <th>货期</th>
              <th>风险</th>
            </tr>
          </thead>
          <tbody>
            {!draft && (
              <tr>
                <td colSpan={9} className="parts-empty">
                  {loading ? '正在加载导入草案...' : '暂无导入草案。'}
                </td>
              </tr>
            )}
            {draft?.rows.map((row, idx) => (
              <tr key={`${row.name_standard}:${idx}`}>
                <td>{row.action === 'create' ? '新增' : row.action}</td>
                <td>
                  <strong>{row.name_standard}</strong>
                  <small>{row.sku_internal || '-'}</small>
                </td>
                <td>{row.part_number || '-'}</td>
                <td>{row.category_name || row.category_id || '-'}</td>
                <td>{row.brand || '-'}</td>
                <td>{row.supplier || '-'}</td>
                <td>{row.unit_cost == null ? '-' : row.unit_cost}</td>
                <td>{row.typical_lead_time || '-'}</td>
                <td>{row.risk || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
