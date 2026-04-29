'use client'
import { AgGridReact } from 'ag-grid-react'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-quartz.css'
import type { BOMNode } from '@/lib/api'
import { getUserName, patchNode, setUserName } from '@/lib/api'
import { useEffect, useMemo, useState } from 'react'
import EditHistoryModal from './EditHistoryModal'

const FIELD_LABEL: Record<string, string> = {
  part_number: '零件号',
  quantity: '数量',
  uom: '单位',
  material: '材料',
  weight: '重量',
  supplier: '供应商',
  unit_cost: '单价',
  notes: '备注',
}

interface PendingEdit {
  nodeId: string
  rowName: string
  field: string
  oldValue: unknown
  newValue: unknown
  // Reference to the underlying ag-grid row so we can roll back on cancel.
  rowNode: any
}

interface Row extends BOMNode {
  __depth: number
  __hasChildren: boolean
  __expanded: boolean
  __isLast: boolean              // last child of its parent
  __ancestorHasMore: boolean[]   // for each ancestor depth, is there a younger sibling to draw the vertical line
}

function buildVisibleRows(nodes: BOMNode[], expanded: Set<string>): Row[] {
  const childrenOf = new Map<string | null, BOMNode[]>()
  for (const n of nodes) {
    const arr = childrenOf.get(n.parent_id) || []
    arr.push(n)
    childrenOf.set(n.parent_id, arr)
  }
  for (const arr of childrenOf.values()) arr.sort((a, b) => a.sort_order - b.sort_order)

  const rows: Row[] = []
  // ancestorHasMore[d] = true if at depth d the current branch still has a
  // younger sibling waiting -> draw a vertical line │ at that depth.
  const visit = (n: BOMNode, depth: number, isLast: boolean, ancestors: boolean[]) => {
    const kids = childrenOf.get(n.id) || []
    const hasChildren = kids.length > 0
    const isExpanded = expanded.has(n.id)
    rows.push({
      ...n,
      __depth: depth,
      __hasChildren: hasChildren,
      __expanded: isExpanded,
      __isLast: isLast,
      __ancestorHasMore: ancestors.slice(),
    })
    if (hasChildren && isExpanded) {
      const childAncestors = ancestors.concat([!isLast])
      kids.forEach((k, i) => visit(k, depth + 1, i === kids.length - 1, childAncestors))
    }
  }

  // Top row of the tree = strictly L0 (level === 0). Collapsing all therefore
  // leaves *only* L0 parts visible, regardless of whether some non-L0 node
  // accidentally has parent_id === null.
  const roots = nodes
    .filter((n) => n.level === 0)
    .sort((a, b) => a.sort_order - b.sort_order)
  roots.forEach((r, i) => visit(r, 0, i === roots.length - 1, []))

  return rows
}

export default function BOMTable({
  nodes,
  bomId,
  onChanged,
  selectedId,
  onSelect,
}: {
  nodes: BOMNode[]
  bomId?: string
  onChanged?: () => void
  selectedId?: string | null
  onSelect?: (id: string | null) => void
}) {
  // Default: all collapsed — only L0 (top-level) nodes visible. The user
  // expands branches manually with the carets, or with 全部展开 below.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())

  // Pending edit awaiting user confirmation. Null = no dialog.
  const [pending, setPending] = useState<PendingEdit | null>(null)
  const [saving, setSaving] = useState(false)
  const [errMsg, setErrMsg] = useState<string | null>(null)

  // History modal + cached user name (rendered as a small chip in the toolbar).
  const [historyOpen, setHistoryOpen] = useState(false)
  const [userName, setUserNameState] = useState<string>('anonymous')
  useEffect(() => {
    setUserNameState(getUserName())
  }, [])
  const promptUserName = () => {
    if (typeof window === 'undefined') return
    const next = window.prompt('请输入你的用户名（用于编辑历史记录）：', userName)
    if (next === null) return
    const trimmed = next.trim() || 'anonymous'
    setUserName(trimmed)
    setUserNameState(trimmed)
  }

  // When the BOM reloads (e.g. after applying a hierarchy rule), reset back
  // to the collapsed L0-only view.
  useEffect(() => {
    setExpanded(new Set())
  }, [nodes])

  const rowData = useMemo(() => buildVisibleRows(nodes, expanded), [nodes, expanded])

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Tree cell: file-explorer-style guide lines + caret + level badge + name.
  const TreeCell = (p: any) => {
    const row: Row = p.data
    const guideStyle: React.CSSProperties = {
      display: 'inline-block',
      width: 18,
      height: 28,
      lineHeight: '28px',
      textAlign: 'center',
      color: '#c9ced6',
      fontFamily: 'monospace',
      fontSize: 14,
      userSelect: 'none',
      verticalAlign: 'middle',
    }
    const guides: JSX.Element[] = []
    // Vertical line for each ancestor that still has a younger sibling.
    for (let d = 0; d < row.__depth; d++) {
      guides.push(
        <span key={`g${d}`} style={guideStyle}>
          {row.__ancestorHasMore[d] ? '│' : ' '}
        </span>,
      )
    }
    // Branch connector for this row (skip for top-level roots).
    if (row.__depth > 0) {
      guides.push(
        <span key="branch" style={guideStyle}>
          {row.__isLast ? '└─' : '├─'}
        </span>,
      )
    }

    const caret = row.__hasChildren ? (
      <span
        onClick={(e) => {
          e.stopPropagation()
          toggle(row.id)
        }}
        style={{
          display: 'inline-block',
          width: 16,
          textAlign: 'center',
          cursor: 'pointer',
          color: '#1677ff',
          userSelect: 'none',
        }}
      >
        {row.__expanded ? '▾' : '▸'}
      </span>
    ) : (
      <span style={{ display: 'inline-block', width: 16 }} />
    )

    const levelBadge = (
      <span
        style={{
          display: 'inline-block',
          minWidth: 22,
          padding: '0 6px',
          margin: '0 6px',
          borderRadius: 10,
          background: row.__depth === 0 ? '#1677ff' : '#eef2ff',
          color: row.__depth === 0 ? '#fff' : '#3949ab',
          fontSize: 11,
          fontWeight: 600,
          textAlign: 'center',
          lineHeight: '18px',
        }}
      >
        L{row.level}
      </span>
    )

    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', whiteSpace: 'nowrap' }}>
        {guides}
        {caret}
        {levelBadge}
        <span style={{ fontWeight: row.__hasChildren ? 600 : 400 }}>{p.value ?? ''}</span>
      </span>
    )
  }

  // Row background tint by depth so visual grouping is obvious even when scrolled.
  // Selected row wins: bright blue band so user sees what chat is anchored to.
  const getRowStyle = (p: any) => {
    if (selectedId && p.data.id === selectedId) {
      return { background: '#deebff' }
    }
    const d: number = p.data.__depth
    if (d === 0) return { background: '#f6f9ff' }
    if (d === 1) return { background: '#fcfdff' }
    return undefined
  }

  const columnDefs = useMemo(
    () => [
      {
        headerName: '层级结构 / 零件名',
        field: 'part_name',
        minWidth: 380,
        flex: 1.4,
        cellRenderer: TreeCell,
        // disable editing on the tree column to keep guides intact;
        // user can still edit other columns
        editable: false,
        pinned: 'left',
      },
      { field: 'part_number', headerName: '零件号', width: 140 },
      { field: 'quantity', headerName: '数量', width: 90 },
      { field: 'uom', headerName: '单位', width: 70 },
      { field: 'material', headerName: '材料', width: 120 },
      { field: 'weight', headerName: '重量', width: 90 },
      { field: 'supplier', headerName: '供应商', width: 140 },
      { field: 'unit_cost', headerName: '单价', width: 90 },
      {
        field: 'confidence',
        headerName: '置信度',
        width: 90,
        valueFormatter: (p: any) => (p.value != null ? `${Math.round(p.value * 100)}%` : ''),
      },
      { field: 'notes', headerName: '备注', flex: 1, minWidth: 160 },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  return (
    <div className="ag-theme-quartz" style={{ width: '100%', height: '100%' }}>
      <div
        style={{
          padding: '6px 12px',
          fontSize: 12,
          color: '#6b7280',
          borderBottom: '1px solid #eef0f2',
          display: 'flex',
          gap: 12,
          alignItems: 'center',
        }}
      >
        <span>共 {nodes.length} 个节点 · 显示 {rowData.length} 行</span>
        <a
          onClick={() => {
            const all = new Set<string>()
            for (const n of nodes) if (n.parent_id) all.add(n.parent_id)
            setExpanded(all)
          }}
          style={{ cursor: 'pointer', color: '#1677ff' }}
        >
          全部展开
        </a>
        <a
          onClick={() => setExpanded(new Set())}
          style={{ cursor: 'pointer', color: '#1677ff' }}
        >
          全部折叠
        </a>
        <span style={{ flex: 1 }} />
        <a
          onClick={promptUserName}
          title="点击修改用户名（保存到本地，用于编辑历史）"
          style={{ cursor: 'pointer', color: '#374151' }}
        >
          👤 {userName}
        </a>
        {bomId && (
          <a
            onClick={() => setHistoryOpen(true)}
            style={{ cursor: 'pointer', color: '#1677ff', fontWeight: 500 }}
          >
            🕘 编辑历史
          </a>
        )}
      </div>
      <div style={{ width: '100%', height: 'calc(100% - 32px)' }}>
        <AgGridReact
          rowData={rowData}
          columnDefs={columnDefs as any}
          defaultColDef={{ resizable: true, sortable: false, editable: true }}
          getRowId={(p: any) => p.data.id}
          getRowStyle={getRowStyle}
          rowHeight={30}
          animateRows
          onRowClicked={(e: any) => {
            // Click anywhere on the row → select that node. The chat
            // sidebar listens to selectedId and pins context to it.
            const id = e?.data?.id
            if (id) onSelect?.(id)
          }}
          onCellValueChanged={(e: any) => {
            // ag-grid fires this AFTER the in-memory row has been mutated.
            // We capture old/new so we can roll back if user cancels.
            const field = e.colDef.field as string
            const oldValue = e.oldValue
            const newValue = e.newValue
            // Ignore no-ops (Enter without change). ag-grid uses === so '' vs null
            // can fire spuriously — guard with shallow eq.
            if (oldValue === newValue) return
            if (!bomId) {
              // No backend wiring → just keep the local edit (legacy behavior).
              return
            }
            setErrMsg(null)
            setPending({
              nodeId: e.data.id,
              rowName: e.data.part_name || e.data.part_number || e.data.id.slice(0, 8),
              field,
              oldValue,
              newValue,
              rowNode: e.node,
            })
          }}
        />
      </div>

      {bomId && (
        <EditHistoryModal
          bomId={bomId}
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          onChanged={onChanged}
        />
      )}

      {pending && (
        <ConfirmEditDialog
          pending={pending}
          saving={saving}
          errorMsg={errMsg}
          onCancel={() => {
            // Roll back: write the old value back into the row.
            try {
              pending.rowNode?.setDataValue(pending.field, pending.oldValue)
            } catch {
              /* ignore */
            }
            setPending(null)
            setErrMsg(null)
          }}
          onConfirm={async () => {
            if (!bomId) return
            setSaving(true)
            setErrMsg(null)
            try {
              await patchNode(bomId, pending.nodeId, {
                [pending.field]: pending.newValue,
              })
              setPending(null)
              // Trigger BOMWorkspace.reload() so graph + table refresh from server.
              onChanged?.()
            } catch (ex: any) {
              setErrMsg(ex?.message || String(ex))
            } finally {
              setSaving(false)
            }
          }}
        />
      )}
    </div>
  )
}

function ConfirmEditDialog({
  pending,
  saving,
  errorMsg,
  onCancel,
  onConfirm,
}: {
  pending: PendingEdit
  saving: boolean
  errorMsg: string | null
  onCancel: () => void
  onConfirm: () => void
}) {
  const fieldLabel = FIELD_LABEL[pending.field] || pending.field
  const fmt = (v: unknown) => {
    if (v === null || v === undefined || v === '') return <em style={{ color: '#9ca3af' }}>（空）</em>
    return <span>{String(v)}</span>
  }
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
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff',
          borderRadius: 8,
          width: 460,
          maxWidth: '90vw',
          padding: 20,
          boxShadow: '0 20px 50px rgba(0,0,0,0.25)',
        }}
      >
        <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>确认修改</div>
        <div style={{ fontSize: 13, color: '#6b7280', marginBottom: 16 }}>
          零件 <b style={{ color: '#1f2329' }}>{pending.rowName}</b> 的{' '}
          <b style={{ color: '#1f2329' }}>{fieldLabel}</b> 将被更新：
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '60px 1fr',
            gap: '8px 12px',
            background: '#f9fafb',
            padding: 12,
            borderRadius: 6,
            fontSize: 13,
            marginBottom: 16,
          }}
        >
          <div style={{ color: '#6b7280' }}>原值</div>
          <div>{fmt(pending.oldValue)}</div>
          <div style={{ color: '#6b7280' }}>新值</div>
          <div style={{ color: '#1677ff', fontWeight: 600 }}>{fmt(pending.newValue)}</div>
        </div>
        {errorMsg && (
          <div
            style={{
              background: '#fef2f2',
              border: '1px solid #fecaca',
              color: '#b91c1c',
              padding: 8,
              borderRadius: 4,
              fontSize: 12,
              marginBottom: 12,
            }}
          >
            {errorMsg}
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn" onClick={onCancel} disabled={saving}>
            取消
          </button>
          <button className="btn btn-primary" onClick={onConfirm} disabled={saving}>
            {saving ? '保存中...' : '确认并同步'}
          </button>
        </div>
      </div>
    </div>
  )
}
