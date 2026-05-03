'use client'
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { BOM } from '@/lib/api'
import { createNode, deleteNode, exportUrl, getBOM, getUserName, setUserName } from '@/lib/api'
import BOMTable from './BOMTable'
import BOMGraph from './BOMGraph'
import AgentSidebar from './AgentSidebar'
import { useAppDialog } from './AppDialog'
import CompanyPartsPanel from './CompanyPartsPanel'
import HierarchyRulePanel from './HierarchyRulePanel'
import SelectionConfiguratorModal from './SelectionConfiguratorModal'

const SPLIT_STORAGE_KEY = 'bom.split.topRatio'
const AGENT_SPLIT_KEY = 'bom.split.agentRatio'
const MIN_RATIO = 0.15
const MAX_RATIO = 0.85
// Agent panel width as a fraction of .bom-page width.
// 0.25 ≈ matches the original 360px on a 1440-wide layout.
const AGENT_DEFAULT = 0.25
const AGENT_MIN = 0.15
const AGENT_MAX = 0.6

export default function BOMWorkspace({ bom: initial }: { bom: BOM }) {
  const dialog = useAppDialog()
  const [bom, setBom] = useState<BOM>(initial)
  const [reloading, setReloading] = useState(false)
  // Bumped on every successful reload. Used as React `key` on the children
  // so BOMGraph (G6 imperatively-managed canvas) and BOMTable (AG Grid
  // imperatively-managed) fully remount with the fresh data — no risk of
  // a stale internal cache surviving the prop change.
  const [refreshKey, setRefreshKey] = useState(0)
  const [userName, setUserNameState] = useState('anonymous')
  // Cross-pane selection: clicking a node in the graph or a row in the
  // table sets this; the agent sidebar reads it to pin context + show
  // quick prompts targeting that node.
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selectedNode = selectedId ? bom.nodes.find((n) => n.id === selectedId) || null : null

  // Selection configurator modal — opens when user clicks "🛠 选型" on the
  // context card. Workspace owns the open state so it can reload BOM after
  // save (via the existing reload() callback).
  const [configuratorOpen, setConfiguratorOpen] = useState(false)
  // Agent-triggered "page jumps" are intentionally kept as in-workspace view
  // switches. We do not push a /parts route, so the browser address never
  // exposes the company material-library view.
  const [mainView, setMainView] = useState<'bom' | 'parts'>('bom')

  // Top panel (graph) takes `topRatio` of the .bom-main column; the
  // bottom panel (table) gets the rest. Persisted across reloads.
  const [topRatio, setTopRatio] = useState<number>(0.5)
  // Agent (right) panel takes `agentRatio` of the .bom-page row width.
  const [agentRatio, setAgentRatio] = useState<number>(AGENT_DEFAULT)
  const mainRef = useRef<HTMLDivElement | null>(null)
  const pageRef = useRef<HTMLDivElement | null>(null)
  const draggingRef = useRef(false)

  // Restore saved ratios on mount.
  useEffect(() => {
    try {
      const v = parseFloat(localStorage.getItem(SPLIT_STORAGE_KEY) || '')
      if (!Number.isNaN(v) && v >= MIN_RATIO && v <= MAX_RATIO) setTopRatio(v)
      const a = parseFloat(localStorage.getItem(AGENT_SPLIT_KEY) || '')
      if (!Number.isNaN(a) && a >= AGENT_MIN && a <= AGENT_MAX) setAgentRatio(a)
      setUserNameState(getUserName())
    } catch {
      /* ignore */
    }
  }, [])

  const promptUserName = useCallback(async () => {
    if (typeof window === 'undefined') return
    const next = await dialog.prompt('请输入你的用户名（用于编辑历史记录）：', userName)
    if (next === null) return
    const trimmed = next.trim() || 'anonymous'
    setUserName(trimmed)
    setUserNameState(trimmed)
  }, [dialog, userName])

  // Persist on change (cheap; mousemove → setState → effect).
  useEffect(() => {
    try {
      localStorage.setItem(SPLIT_STORAGE_KEY, String(topRatio))
    } catch {
      /* ignore */
    }
  }, [topRatio])
  useEffect(() => {
    try {
      localStorage.setItem(AGENT_SPLIT_KEY, String(agentRatio))
    } catch {
      /* ignore */
    }
  }, [agentRatio])

  const reload = useCallback(async () => {
    setReloading(true)
    try {
      const fresh = await getBOM(initial.id)
      // eslint-disable-next-line no-console
      console.log('[BOMWorkspace] reload → got', fresh.nodes.length, 'nodes')
      setBom(fresh)
      setRefreshKey((k) => k + 1)
    } catch (ex) {
      // eslint-disable-next-line no-console
      console.error('[BOMWorkspace] reload failed', ex)
    } finally {
      setReloading(false)
    }
  }, [initial.id])

  const countDescendants = useCallback((nodeId: string): number => {
    const childrenByParent = new Map<string, string[]>()
    for (const n of bom.nodes) {
      if (!n.parent_id) continue
      const arr = childrenByParent.get(n.parent_id) || []
      arr.push(n.id)
      childrenByParent.set(n.parent_id, arr)
    }
    let count = 0
    const stack = [...(childrenByParent.get(nodeId) || [])]
    while (stack.length > 0) {
      const id = stack.pop()!
      count += 1
      stack.push(...(childrenByParent.get(id) || []))
    }
    return count
  }, [bom.nodes])

  const handleGraphAddChild = useCallback(async (parentId: string) => {
    const parent = bom.nodes.find((n) => n.id === parentId)
    if (!parent) return
    const name = await dialog.prompt(`在「${parent.part_name}」下新增子节点`, '新子节点')
    if (name === null) return
    const partName = name.trim() || '新子节点'
    setReloading(true)
    try {
      await createNode(bom.id, {
        parent_id: parentId,
        part_name: partName,
        quantity: 1,
        uom: 'EA',
      })
      await reload()
    } catch (ex) {
      // eslint-disable-next-line no-console
      console.error('[BOMWorkspace] add child failed', ex)
      await dialog.alert('新增子节点失败，请稍后重试。')
    } finally {
      setReloading(false)
    }
  }, [bom.id, bom.nodes, dialog, reload])

  const handleGraphDeleteNode = useCallback(async (nodeId: string) => {
    const node = bom.nodes.find((n) => n.id === nodeId)
    if (!node) return
    const descendants = countDescendants(nodeId)
    const message = descendants > 0
      ? `删除「${node.part_name}」及其 ${descendants} 个子节点？`
      : `删除「${node.part_name}」？`
    if (!(await dialog.confirm(message))) return
    setReloading(true)
    try {
      await deleteNode(bom.id, nodeId, descendants > 0)
      if (selectedId === nodeId) setSelectedId(null)
      await reload()
    } catch (ex) {
      // eslint-disable-next-line no-console
      console.error('[BOMWorkspace] delete node failed', ex)
      await dialog.alert('删除节点失败，请稍后重试。')
    } finally {
      setReloading(false)
    }
  }, [bom.id, bom.nodes, countDescendants, dialog, reload, selectedId])

  // Drag: translate mouse Y inside .bom-main into a 0..1 ratio.
  const onMouseDownSplitter = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    draggingRef.current = true
    document.body.classList.add('is-resizing-v')

    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current || !mainRef.current) return
      const rect = mainRef.current.getBoundingClientRect()
      const y = ev.clientY - rect.top
      let r = y / rect.height
      if (r < MIN_RATIO) r = MIN_RATIO
      if (r > MAX_RATIO) r = MAX_RATIO
      setTopRatio(r)
    }
    const onUp = () => {
      draggingRef.current = false
      document.body.classList.remove('is-resizing-v')
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [])

  // Drag: translate mouse X inside .bom-page into agent-panel ratio.
  // We measure from the right edge so dragging left grows the agent panel.
  const onMouseDownAgentSplitter = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    draggingRef.current = true
    document.body.classList.add('is-resizing-h')

    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current || !pageRef.current) return
      const rect = pageRef.current.getBoundingClientRect()
      const distFromRight = rect.right - ev.clientX
      let r = distFromRight / rect.width
      if (r < AGENT_MIN) r = AGENT_MIN
      if (r > AGENT_MAX) r = AGENT_MAX
      setAgentRatio(r)
    }
    const onUp = () => {
      draggingRef.current = false
      document.body.classList.remove('is-resizing-h')
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [])

  // Vertical splitter is 8px tall with 4px margin top/bottom (total 16px);
  // subtract half (8px) from each panel's basis so the three children sum
  // to the container height exactly.
  const topStyle = { flex: `0 0 calc(${topRatio * 100}% - 8px)`, minHeight: 0 } as const
  const bottomStyle = { flex: `0 0 calc(${(1 - topRatio) * 100}% - 8px)`, minHeight: 0 } as const

  // Horizontal splitter is 8px wide with 4px margin L/R (total 16px); same
  // 8px subtraction from each side. .bom-main flexes to fill the rest so
  // we only pin the agent panel's basis.
  const agentStyle = {
    flex: `0 0 calc(${agentRatio * 100}% - 8px)`,
    minWidth: 0,
  } as const
  const mainFlexStyle = { flex: '1 1 0', minWidth: 0 } as const

  return (
    <div className="bom-workspace-shell">
      <div className="topbar bom-topbar">
        <div className="workspace-brand">
          <Link href="/" className="brand-mark small">
            <span className="brand-cube" />
            <span>PEBS BOM</span>
          </Link>
          <span className="bom-title">
            / {bom.name} · {bom.nodes.length} 节点
            {reloading && <span className="sync-chip">同步中...</span>}
          </span>
        </div>
        <div className="workspace-actions">
          <button
            type="button"
            className={`view-tab ${mainView === 'bom' ? 'active' : ''}`}
            onClick={() => setMainView('bom')}
          >
            BOM 工作台
          </button>
          <button
            type="button"
            className={`view-tab ${mainView === 'parts' ? 'active' : ''}`}
            onClick={() => setMainView('parts')}
          >
            公司物料库
          </button>
          <button
            type="button"
            className="workspace-user"
            onClick={promptUserName}
            title="点击修改用户名（保存到本地，用于编辑历史）"
          >
            <span className="user-dot">♙</span>
            <span>{userName}</span>
          </button>
        </div>
      </div>
      <div className="bom-page" ref={pageRef}>
        <div className="bom-main" ref={mainRef} style={mainFlexStyle}>
          {mainView === 'parts' ? (
            <div className="panel" style={{ flex: '1 1 0', minHeight: 0 }}>
              <div className="panel-header">公司标准物料库</div>
              <div className="panel-body">
                <CompanyPartsPanel refreshKey={refreshKey} />
              </div>
            </div>
          ) : (
            <>
              <div className="panel" style={topStyle}>
                <div className="panel-header">BOM 结构图 (G6)</div>
                <HierarchyRulePanel bomId={bom.id} onApplied={reload} />
                <div className="panel-body">
                  <BOMGraph
                    nodes={bom.nodes}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    onAddChild={handleGraphAddChild}
                    onDeleteNode={handleGraphDeleteNode}
                  />
                </div>
              </div>
              <div
                className="splitter-v"
                role="separator"
                aria-orientation="horizontal"
                title="拖动调整上下区域大小（双击恢复 1:1）"
                onMouseDown={onMouseDownSplitter}
                onDoubleClick={() => setTopRatio(0.5)}
              />
              <div className="panel" style={bottomStyle}>
                <div className="panel-header">BOM 表格</div>
                <div className="panel-body">
                  <BOMTable
                    key={`t-${refreshKey}`}
                    nodes={bom.nodes}
                    bomId={bom.id}
                    onChanged={reload}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    exportHref={exportUrl(bom.id)}
                  />
                </div>
              </div>
            </>
          )}
        </div>
        <div
          className="splitter-h"
          role="separator"
          aria-orientation="vertical"
          title="拖动调整左右区域大小（双击恢复默认）"
          onMouseDown={onMouseDownAgentSplitter}
          onDoubleClick={() => setAgentRatio(AGENT_DEFAULT)}
        />
        <div className="panel" style={agentStyle}>
          <div className="panel-header">智能体对话</div>
          <div className="panel-body">
            <AgentSidebar
              bomId={bom.id}
              onBomUpdated={reload}
              selectedNode={selectedNode}
              onClearSelection={() => setSelectedId(null)}
              onOpenConfigurator={() => setConfiguratorOpen(true)}
              onOpenParts={() => setMainView('parts')}
              onOpenBom={() => setMainView('bom')}
              currentView={mainView}
            />
          </div>
        </div>
      </div>
      {configuratorOpen && selectedNode && (
        <SelectionConfiguratorModal
          bomId={bom.id}
          node={selectedNode}
          onClose={() => setConfiguratorOpen(false)}
          onSaved={reload}
        />
      )}
    </div>
  )
}
