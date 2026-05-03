'use client'
import { useEffect, useState } from 'react'
import { listEdits, undoEdit, type BOMEdit } from '@/lib/api'
import { useAppDialog } from './AppDialog'

// Fields that the undo endpoint accepts. Other rows (style, parent_id,
// __create__, __delete__, etc.) show a disabled / non-actionable cell.
const UNDOABLE_FIELDS = new Set([
  'part_number',
  'part_name',
  'description',
  'quantity',
  'uom',
  'material',
  'weight',
  'supplier',
  'unit_cost',
  'notes',
])

function fmtTime(iso: string): string {
  // Backend stores naive UTC. Append Z so Date treats it as UTC, then
  // render in the user's local timezone.
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

function fmtValue(v: string | null): JSX.Element {
  if (v === null || v === '') return <em style={{ color: '#9ca3af' }}>（空）</em>
  return <span>{v}</span>
}

const SOURCE_LABEL: Record<string, { text: string; color: string; bg: string }> = {
  table: { text: '表格', color: '#1d4ed8', bg: '#dbeafe' },
  agent: { text: '智能体', color: '#047857', bg: '#d1fae5' },
  hierarchy: { text: '层级重建', color: '#7c3aed', bg: '#ede9fe' },
}

export default function EditHistoryModal({
  bomId,
  open,
  onClose,
  onChanged,
}: {
  bomId: string
  open: boolean
  onClose: () => void
  onChanged?: () => void
}) {
  const dialog = useAppDialog()
  const [edits, setEdits] = useState<BOMEdit[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [undoing, setUndoing] = useState<string | null>(null)

  const refresh = () => {
    setLoading(true)
    setError(null)
    listEdits(bomId, 500)
      .then(setEdits)
      .catch((e) => setError(e?.message || String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (!open) return
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, bomId])

  const onUndo = async (e: BOMEdit) => {
    if (!UNDOABLE_FIELDS.has(e.field)) return
    if (
      !(await dialog.confirm(
        `撤销该修改？\n\n` +
          `${e.node_label || e.node_id.slice(0, 8)} 的 ${e.field_label}` +
          `\n  当前: ${e.new_value ?? '（空）'} → 还原为: ${e.old_value ?? '（空）'}`,
      ))
    )
      return
    setUndoing(e.id)
    setError(null)
    try {
      await undoEdit(bomId, e.id)
      onChanged?.()
      refresh()
    } catch (ex: any) {
      setError(ex?.message || String(ex))
    } finally {
      setUndoing(null)
    }
  }

  if (!open) return null

  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15, 23, 42, 0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff',
          borderRadius: 8,
          width: 880,
          maxWidth: '95vw',
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 20px 50px rgba(0,0,0,0.25)',
        }}
      >
        <div
          style={{
            padding: '14px 20px',
            borderBottom: '1px solid #e5e7eb',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ fontSize: 16, fontWeight: 600 }}>
            BOM 编辑历史
            {edits && (
              <span style={{ marginLeft: 8, color: '#6b7280', fontSize: 13, fontWeight: 400 }}>
                共 {edits.length} 条记录
              </span>
            )}
          </div>
          <button className="btn" onClick={onClose}>
            关闭
          </button>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: '0 4px' }}>
          {loading && (
            <div style={{ padding: 30, textAlign: 'center', color: '#6b7280' }}>加载中…</div>
          )}
          {error && (
            <div style={{ padding: 20, color: '#b91c1c' }}>错误：{error}</div>
          )}
          {edits && edits.length === 0 && !loading && (
            <div style={{ padding: 30, textAlign: 'center', color: '#9ca3af' }}>
              暂无编辑记录。在表格中修改任意字段后会自动产生记录。
            </div>
          )}
          {edits && edits.length > 0 && (
            <table
              style={{
                width: '100%',
                borderCollapse: 'collapse',
                fontSize: 13,
              }}
            >
              <thead style={{ position: 'sticky', top: 0, background: '#f9fafb', zIndex: 1 }}>
                <tr>
                  <th style={th}>时间</th>
                  <th style={th}>用户</th>
                  <th style={th}>来源</th>
                  <th style={th}>零件</th>
                  <th style={th}>字段</th>
                  <th style={th}>原值</th>
                  <th style={th}>新值</th>
                  <th style={th}>操作</th>
                </tr>
              </thead>
              <tbody>
                {edits.map((e) => {
                  const src = SOURCE_LABEL[e.source] || {
                    text: e.source,
                    color: '#374151',
                    bg: '#f3f4f6',
                  }
                  return (
                    <tr
                      key={e.id}
                      style={{ borderBottom: '1px solid #f1f3f5', verticalAlign: 'top' }}
                    >
                      <td style={{ ...td, whiteSpace: 'nowrap', color: '#374151' }}>
                        {fmtTime(e.created_at)}
                      </td>
                      <td style={{ ...td, fontWeight: 500 }}>{e.user_name}</td>
                      <td style={td}>
                        <span
                          style={{
                            display: 'inline-block',
                            padding: '1px 8px',
                            borderRadius: 10,
                            background: src.bg,
                            color: src.color,
                            fontSize: 11,
                            fontWeight: 600,
                          }}
                        >
                          {src.text}
                        </span>
                      </td>
                      <td style={td}>{e.node_label || e.node_id.slice(0, 8)}</td>
                      <td style={{ ...td, color: '#6b7280' }}>{e.field_label}</td>
                      <td style={{ ...td, color: '#6b7280', textDecoration: 'line-through' }}>
                        {fmtValue(e.old_value)}
                      </td>
                      <td style={{ ...td, color: '#1677ff', fontWeight: 600 }}>
                        {fmtValue(e.new_value)}
                      </td>
                      <td style={td}>
                        {UNDOABLE_FIELDS.has(e.field) ? (
                          <button
                            className="btn"
                            disabled={undoing === e.id}
                            onClick={() => onUndo(e)}
                            style={{
                              padding: '2px 8px',
                              fontSize: 12,
                              border: '1px solid #d0d7de',
                            }}
                          >
                            {undoing === e.id ? '撤销中...' : '撤销'}
                          </button>
                        ) : (
                          <span style={{ color: '#9ca3af', fontSize: 12 }}>—</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

const th: React.CSSProperties = {
  padding: '8px 12px',
  textAlign: 'left',
  fontSize: 12,
  fontWeight: 600,
  color: '#6b7280',
  borderBottom: '1px solid #e5e7eb',
}

const td: React.CSSProperties = {
  padding: '8px 12px',
}
