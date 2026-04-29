'use client'
import { Rect as GRect, Text as GText } from '@antv/g'
import {
  Badge,
  CommonEvent,
  ExtensionCategory,
  Graph,
  Label,
  Rect,
  register,
  iconfont,
} from '@antv/g6'
import { useEffect, useRef } from 'react'
import type { BOMNode } from '@/lib/api'

interface Props {
  nodes: BOMNode[]
  selectedId?: string | null
  onSelect?: (id: string | null) => void
}

// ─── Status color palette (from fund-flow example) ────────────────────────
const COLORS = {
  G: '#60C42D', // confidence >= 0.85 — high confidence
  B: '#1783FF', // 0.70 ≤ confidence < 0.85 — ok
  Y: '#DB9D0D', // 0.50 ≤ confidence < 0.70 — warning
  R: '#F46649', // confidence < 0.50 — low confidence
} as const
const GREY = '#CED4D9'

function statusOf(confidence: number): keyof typeof COLORS {
  if (confidence >= 0.85) return 'G'
  if (confidence >= 0.7) return 'B'
  if (confidence >= 0.5) return 'Y'
  return 'R'
}

// Truncate visible text so the layout doesn't blow out the 220×60 box.
function clip(s: string | null | undefined, max: number): string {
  if (!s) return '—'
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

// ─── Slot model ──────────────────────────────────────────────────────────
// Every visible element of the card is a "slot" that the agent can override
// individually. Stored under BOMNode.style.slots[<slot_id>] with this shape:
//   { text?: string, bound?: string, color?: string, visible?: boolean }
//
//   slot id     | default content              | text? bound? color? visible?
//   ------------+------------------------------+-------------------------------
//   header      | part_number (top-left small) |  ✓     ✓      ✓      ✓
//   title       | part_name (bottom-left big)  |  ✓     ✓      ✓      ✓
//   qty         | "× N UOM"                    |  ✓     —      ✓      ✓
//   metric      | confidence% (right)          |  ✓     ✓      ✓      ✓
//   trend       | up/down triangle             |  —     —      ✓      ✓
//   progress    | bottom progress bar          |  —     —      ✓      ✓
//   badge       | top-right corner pill        |  ✓     —      ✓      ✓
//   card        | rect background/border       |  uses fill/stroke/lineWidth/opacity
//
// `bound` (text slots only) names a BOMNode field whose value drives the
// slot's text. Supported: part_number, part_name, quantity, uom, material,
// supplier, unit_cost, notes, description, confidence_pct.

const BOUND_RESOLVERS: Record<string, (d: any) => string> = {
  part_number: (d) => d.partNumber || '',
  part_name: (d) => d.partName || '',
  quantity: (d) => (d.qty ?? '').toString(),
  uom: (d) => d.uom || '',
  material: (d) => d.material || '',
  supplier: (d) => d.supplier || '',
  unit_cost: (d) => (d.unitCost != null ? `¥${d.unitCost}` : ''),
  notes: (d) => d.notes || '',
  description: (d) => d.description || '',
  confidence_pct: (d) => `${Math.round((d.confidence ?? 0) * 100)}%`,
  category: (d) => d.categoryName || '',
}

function slotOf(d: any, id: string): any {
  return (d?.slots && d.slots[id]) || {}
}
function slotVisible(slot: any): boolean {
  return slot.visible !== false
}
// Resolve a text slot's content: explicit text wins, then bound field,
// then the caller's default (which is the slot's "natural" content).
function slotText(d: any, id: string, fallback: string): string {
  const s = slotOf(d, id)
  if (typeof s.text === 'string' && s.text.length > 0) return s.text
  if (typeof s.bound === 'string' && BOUND_RESOLVERS[s.bound]) {
    return BOUND_RESOLVERS[s.bound](d)
  }
  return fallback
}

// ─── Custom node class — mirrors fund-flow's TreeNode ─────────────────────
// Shape composition:
//   ┌──────────────────────────────────────────────────┐
//   │ header                                            │
//   │                                                   │
//   │ title                          qty  trend metric  │
//   │ ████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │ ← progress
//   └──────────────────────────────────────────────────┘
class BomNode extends Rect {
  get data(): any {
    return (this as any).context.model.getNodeLikeDatum(this.id)
  }

  get childrenData(): any[] {
    return (this as any).context.model.getChildrenData(this.id)
  }

  // Top-left small label — slot id "header" (default: part_number)
  // @ts-expect-error — Rect.getLabelStyle signature is loose in g6 v5
  getLabelStyle(attributes: any) {
    const [width, height] = (this as any).getSize(attributes)
    const s = slotOf(this.data, 'header')
    if (!slotVisible(s)) return false
    return {
      x: -width / 2 + 8,
      y: -height / 2 + 14,
      text: clip(slotText(this.data, 'header', this.data.partNumber || ''), 28),
      fontSize: 12,
      opacity: 0.85,
      fill: s.color || '#000',
      cursor: 'pointer',
    }
  }

  // Bottom-left big label — slot id "title" (default: part_name)
  getNameStyle(attributes: any) {
    const [width, height] = (this as any).getSize(attributes)
    const s = slotOf(this.data, 'title')
    if (!slotVisible(s)) return null
    return {
      x: -width / 2 + 8,
      y: height / 2 - 10,
      text: clip(slotText(this.data, 'title', this.data.partName || ''), 16),
      fontSize: 15,
      fontWeight: 500,
      fill: s.color || '#000',
      opacity: 0.9,
    }
  }

  drawNameShape(attributes: any, container: any) {
    const style = this.getNameStyle(attributes)
    if (!style) return
    ;(this as any).upsert('name', GText, style, container)
  }

  // Right-side metric — slot id "metric".
  // Default content rules (in priority order):
  //   1. user-set explicit text (slot.text)
  //   2. user-set bound field (slot.bound)
  //   3. node has been classified → show category_name (e.g. "直线导轨")
  //   4. fall back to confidence percentage
  // Color: explicit slot.color → accent → CATEGORY_BLUE if classified →
  // status palette (G/B/Y/R by confidence).
  getPercentStyle(attributes: any) {
    const [width, height] = (this as any).getSize(attributes)
    const s = slotOf(this.data, 'metric')
    if (!slotVisible(s)) return null
    const accent = this.data.accent as string | undefined
    const status = statusOf(Number(this.data.confidence) || 0)
    const isClassified = !!this.data.categoryName
    const defaultText = isClassified
      ? String(this.data.categoryName)
      : `${((Number(this.data.confidence) || 0) * 100).toFixed(0)}%`
    const defaultColor = isClassified ? COLORS.B : COLORS[status]
    return {
      x: width / 2 - 24,
      y: height / 2 - 10,
      text: clip(slotText(this.data, 'metric', defaultText), 14),
      fontSize: 12,
      textAlign: 'right',
      fill: s.color || accent || defaultColor,
    }
  }

  drawPercentShape(attributes: any, container: any) {
    const style = this.getPercentStyle(attributes)
    if (!style) return
    ;(this as any).upsert('percent', GText, style, container)
  }

  // Trend triangle — slot id "trend" (color/visible only).
  // Auto-hides when the metric slot is rebound away from confidence
  // (a confidence-based up/down doesn't make sense over a supplier name).
  getTriangleStyle(attributes: any) {
    const s = slotOf(this.data, 'trend')
    if (!slotVisible(s)) return null
    const metric = slotOf(this.data, 'metric')
    // Hide the up/down trend whenever the metric slot is showing something
    // other than the confidence percentage — the trend has no meaning over
    // a category name, supplier, etc. User can force-show via slots.trend.visible.
    const metricRebound =
      (typeof metric.text === 'string' && metric.text.length > 0) ||
      (typeof metric.bound === 'string' && metric.bound !== 'confidence_pct') ||
      (!metric.text && !metric.bound && !!this.data.categoryName)
    if (metricRebound && s.visible !== true) return null
    const percentShape = (this as any).shapeMap['percent']
    if (!percentShape) return null
    const percentMinX = percentShape.getLocalBounds().min[0]
    const [, height] = (this as any).getSize(attributes)
    const accent = this.data.accent as string | undefined
    const status = statusOf(Number(this.data.confidence) || 0)
    const isUp = (Number(this.data.confidence) || 0) >= 0.7
    return {
      fill: s.color || accent || COLORS[status],
      x: isUp ? percentMinX - 16 : percentMinX - 2,
      y: height / 2 - 16,
      fontFamily: 'iconfont',
      fontSize: 14,
      text: '',
      transform: isUp ? [] : [['rotate', 180]],
    }
  }

  drawTriangleShape(attributes: any, container: any) {
    const style = this.getTriangleStyle(attributes)
    if (!style) return
    ;(this as any).upsert('triangle', Label, style, container)
  }

  // qty × uom — slot id "qty"
  getQtyStyle(attributes: any) {
    const [width, height] = (this as any).getSize(attributes)
    const s = slotOf(this.data, 'qty')
    if (!slotVisible(s)) return null
    // Anchor next to whichever right-side neighbor exists. If trend is hidden
    // (e.g. metric was rebound), fall back to the percent shape; if that is
    // also gone, anchor to the card's right edge.
    const tri = (this as any).shapeMap['triangle']
    const pct = (this as any).shapeMap['percent']
    let anchor = width / 2 - 8
    if (tri) anchor = tri.getLocalBounds().min[0] - 4
    else if (pct) anchor = pct.getLocalBounds().min[0] - 4
    const qty = this.data.qty ?? ''
    const uom = this.data.uom ?? ''
    return {
      fill: s.color || '#000',
      fontSize: 12,
      opacity: 0.55,
      text: slotText(this.data, 'qty', `× ${qty} ${uom}`.trim()),
      textAlign: 'right',
      x: anchor,
      y: height / 2 - 10,
    }
  }

  drawQtyShape(attributes: any, container: any) {
    const style = this.getQtyStyle(attributes)
    if (!style) return
    ;(this as any).upsert('qty', GText, style, container)
  }

  // Collapse / expand button on the right edge
  getCollapseStyle(attributes: any) {
    if (this.childrenData.length === 0) return false
    const { collapsed } = attributes
    const [width] = (this as any).getSize(attributes)
    return {
      backgroundFill: '#fff',
      backgroundHeight: 16,
      backgroundLineWidth: 1,
      backgroundRadius: 0,
      backgroundStroke: GREY,
      backgroundWidth: 16,
      cursor: 'pointer',
      fill: GREY,
      fontSize: 16,
      text: collapsed ? '+' : '-',
      textAlign: 'center',
      textBaseline: 'middle',
      x: width / 2,
      y: 0,
    }
  }

  drawCollapseShape(attributes: any, container: any) {
    const collapseStyle = this.getCollapseStyle(attributes)
    const btn = (this as any).upsert('collapse', Badge, collapseStyle, container)
    if (btn && !Reflect.has(btn, '__bind__')) {
      Reflect.set(btn, '__bind__', true)
      btn.addEventListener(CommonEvent.CLICK, () => {
        const { collapsed } = (this as any).attributes
        const graph = (this as any).context.graph
        if (collapsed) graph.expandElement(this.id)
        else graph.collapseElement(this.id)
      })
    }
  }

  // Bottom progress bar — slot id "progress"
  getProcessBarStyle(attributes: any) {
    const s = slotOf(this.data, 'progress')
    if (!slotVisible(s)) return null
    const conf = Number(this.data.confidence) || 0
    const accent = this.data.accent as string | undefined
    const status = statusOf(conf)
    const { radius } = attributes
    const color = s.color || accent || COLORS[status]
    const percent = `${conf * 100}%`
    const [width, height] = (this as any).getSize(attributes)
    return {
      x: -width / 2,
      y: height / 2 - 4,
      width: width,
      height: 4,
      radius: [0, 0, radius, radius],
      fill: `linear-gradient(to right, ${color} ${percent}, ${GREY} ${percent})`,
    }
  }

  drawProcessBarShape(attributes: any, container: any) {
    const style = this.getProcessBarStyle(attributes)
    if (!style) return
    ;(this as any).upsert('process-bar', GRect, style, container)
  }

  // Card body — slot id "card", plus legacy whole-node shortcuts
  // (highlight / dim / fill / stroke / lineWidth / opacity at the top level).
  // `selected` (set by BOMGraph from the workspace's selectedId state)
  // wins over user style for the border treatment so the user always
  // knows which node the chat is currently anchored to.
  getKeyStyle(attributes: any) {
    const keyStyle = (super.getKeyStyle as any)(attributes)
    const u = this.data
    const card = slotOf(u, 'card')
    const highlight = u.highlight === true
    const dim = u.dim === true
    const selected = u.selected === true
    return {
      ...keyStyle,
      fill: card.fill || u.fill || '#fff',
      lineWidth: selected
        ? 3
        : (card.lineWidth ?? u.lineWidth ?? (highlight ? 2 : 1)),
      stroke: selected
        ? COLORS.B
        : (card.stroke || u.stroke || (highlight ? COLORS.R : GREY)),
      opacity: card.opacity ?? u.opacity ?? (dim ? 0.45 : 1),
    }
  }

  // Top-right corner badge — slot id "badge".
  // Backwards-compat: top-level `badge` string still works (legacy shortcut).
  getCornerBadgeStyle(attributes: any) {
    const s = slotOf(this.data, 'badge')
    if (!slotVisible(s)) return false
    const text = slotText(this.data, 'badge', this.data.badge || '')
    if (!text) return false
    const [width, height] = (this as any).getSize(attributes)
    const accent = s.color || (this.data.accent as string | undefined) || COLORS.B
    return {
      backgroundFill: accent,
      backgroundHeight: 14,
      backgroundLineWidth: 0,
      backgroundRadius: 7,
      backgroundWidth: Math.max(28, String(text).length * 8 + 12),
      cursor: 'default',
      fill: '#fff',
      fontSize: 10,
      text: String(text),
      textAlign: 'center',
      textBaseline: 'middle',
      x: width / 2 - 18,
      y: -height / 2 + 10,
    }
  }

  drawBadgeShape(attributes: any, container: any) {
    const style = this.getCornerBadgeStyle(attributes)
    if (!style) return
    ;(this as any).upsert('cornerBadge', Badge, style, container)
  }

  render(attributes: any = (this as any).parsedAttributes, container: any) {
    super.render(attributes, container)
    this.drawNameShape(attributes, container)
    this.drawPercentShape(attributes, container)
    this.drawTriangleShape(attributes, container)
    this.drawQtyShape(attributes, container)
    this.drawProcessBarShape(attributes, container)
    this.drawBadgeShape(attributes, container)
    this.drawCollapseShape(attributes, container)
  }
}

// Register on every module load. G6 v5's `register` overwrites the previous
// constructor, which is exactly what we want under HMR — otherwise edits to
// BomNode wouldn't take effect until a full page reload.
register(ExtensionCategory.NODE, 'bom-node', BomNode)

// Inject iconfont stylesheet once for the up/down triangle glyph.
const ICONFONT_FLAG = '__pebs_bom_iconfont_loaded__'
if (typeof document !== 'undefined' && !(document as any)[ICONFONT_FLAG]) {
  const styleEl = document.createElement('style')
  styleEl.innerHTML = `@import url('${iconfont.css}');`
  document.head.appendChild(styleEl)
  ;(document as any)[ICONFONT_FLAG] = true
}

// ─── Flat BOMNode[] → G6 graph data ──────────────────────────────────────
// We build {nodes, edges} directly instead of going through `treeToGraphData`
// because BOM data is often a forest (e.g. STEP imports come back as 10
// independent level-0 nodes with no parent_id), and the indented layout
// needs a single root. Multi-root → we synthesize an invisible super-root
// and connect all real roots to it.
const SYNTHETIC_ROOT_ID = '__bom_root__'

function nodeData(n: BOMNode) {
  const s = (n.style || {}) as Record<string, unknown>
  return {
    partNumber: n.part_number,
    partName: n.part_name,
    qty: n.quantity,
    uom: n.uom,
    confidence: n.confidence,
    material: n.material,
    supplier: n.supplier,
    unitCost: n.unit_cost,
    notes: n.notes,
    description: n.description,
    categoryId: n.category_id,
    categoryName: n.category_name,
    spec: n.spec,
    fill: typeof s.fill === 'string' ? s.fill : undefined,
    stroke: typeof s.stroke === 'string' ? s.stroke : undefined,
    lineWidth: typeof s.lineWidth === 'number' ? s.lineWidth : undefined,
    opacity: typeof s.opacity === 'number' ? s.opacity : undefined,
    accent: typeof s.accent === 'string' ? s.accent : undefined,
    badge: typeof s.badge === 'string' ? s.badge : undefined,
    highlight: s.highlight === true,
    dim: s.dim === true,
    slots: s.slots && typeof s.slots === 'object'
      ? (s.slots as Record<string, any>)
      : undefined,
  }
}

function toGraphData(nodes: BOMNode[], selectedId: string | null = null) {
  if (nodes.length === 0) return { nodes: [], edges: [] }
  const ids = new Set(nodes.map((n) => n.id))

  // BOM fields are spread at top level (not nested under `data`) because
  // BomNode reads via `context.model.getNodeLikeDatum(id)` which returns the
  // full datum — same convention as the fund-flow example.
  // We deliberately do NOT set `style.collapsed` here: with dagre layout,
  // setting collapsed on individual nodes caused G6 to hide siblings of a
  // non-collapsed parent. dagre handles forests fine without that hint.
  const g6Nodes: any[] = nodes.map((n) => ({
    id: n.id,
    ...nodeData(n),
    selected: selectedId === n.id,
  }))
  const g6Edges: any[] = nodes
    .filter((n) => n.parent_id && ids.has(n.parent_id))
    .map((n) => ({
      id: `${n.parent_id}->${n.id}`,
      source: n.parent_id!,
      target: n.id,
    }))

  // Forest detection: any node with no resolvable parent is a real root.
  const roots = nodes.filter((n) => !n.parent_id || !ids.has(n.parent_id))
  if (roots.length > 1) {
    g6Nodes.unshift({
      id: SYNTHETIC_ROOT_ID,
      partNumber: null,
      partName: 'BOM',
      qty: roots.length,
      uom: '件',
      confidence: 1,
      material: null,
      supplier: null,
      unitCost: null,
      notes: null,
      description: null,
    })
    roots.forEach((r) => {
      g6Edges.push({
        id: `${SYNTHETIC_ROOT_ID}->${r.id}`,
        source: SYNTHETIC_ROOT_ID,
        target: r.id,
      })
    })
  }
  return { nodes: g6Nodes, edges: g6Edges }
}

export default function BOMGraph({ nodes, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const graphRef = useRef<Graph | null>(null)
  // Latest onSelect — held in a ref so the mount-once effect can still call
  // the freshest callback (props change but the graph instance persists).
  const onSelectRef = useRef(onSelect)
  useEffect(() => { onSelectRef.current = onSelect }, [onSelect])
  // Tracks which mount instance is current; used to ignore async work
  // (render promises) that resolve after we've already destroyed the graph.
  const aliveRef = useRef(true)

  // Mount once. We never destroy + recreate on prop change — G6 v5's async
  // render pipeline races with destroy() and crashes deep inside graph.js
  // with "Cannot read properties of undefined (reading 'draw')".
  useEffect(() => {
    if (!containerRef.current) return
    aliveRef.current = true

    const graph = new Graph({
      container: containerRef.current,
      autoFit: 'view',
      data: toGraphData(nodes, selectedId ?? null),
      node: {
        type: 'bom-node',
        style: {
          size: [220, 60],
          ports: [{ placement: 'left' }, { placement: 'right' }],
          radius: 4,
        },
      },
      edge: {
        type: 'cubic-horizontal',
        style: { stroke: GREY },
      },
      layout: {
        // Dagre handles forests gracefully (multiple roots, missing edges
        // between siblings, etc). Indented layout only follows a single
        // root spine and dropped the other 9 nodes for STEP-imported BOMs.
        type: 'dagre',
        rankdir: 'LR',
        nodesep: 18,
        ranksep: 80,
      },
      behaviors: ['zoom-canvas', 'drag-canvas'],
    })

    graphRef.current = graph

    // Wire G6 click events → React onSelect callback.
    // 'node:click' fires for left-click on any node (incl. the synthetic root).
    // 'canvas:click' fires when the user clicks empty area → deselect.
    graph.on('node:click', (evt: any) => {
      const id = evt?.target?.id ?? evt?.itemId ?? null
      if (typeof id === 'string' && id !== SYNTHETIC_ROOT_ID) {
        onSelectRef.current?.(id)
      }
    })
    graph.on('canvas:click', () => onSelectRef.current?.(null))

    // Defer render() to next frame. React 18 strict-mode does mount → unmount →
    // remount in dev; if we render() synchronously, the first mount's async
    // render is still in flight when cleanup destroys the graph, and G6
    // logs "[G6 v5.1.0] The graph instance has been destroyed" deep inside
    // its own promise chain — which our outer .catch() can't suppress.
    // With rAF, cleanup cancels the frame before render even starts.
    let renderScheduled: number | null = requestAnimationFrame(() => {
      renderScheduled = null
      if (!aliveRef.current || (graph as any).destroyed) return
      graph.render().catch(() => {
        /* late rejection — ignore */
      })
    })

    const usable = (g: Graph | null): g is Graph =>
      !!g && !(g as any).destroyed

    const onResize = () => {
      if (!aliveRef.current) return
      const g = graphRef.current
      if (!usable(g)) return
      try {
        g.resize()
      } catch {
        /* ignore */
      }
    }
    window.addEventListener('resize', onResize)

    return () => {
      aliveRef.current = false
      if (renderScheduled !== null) cancelAnimationFrame(renderScheduled)
      window.removeEventListener('resize', onResize)
      try {
        if (!(graph as any).destroyed) graph.destroy()
      } catch {
        /* ignore */
      }
      graphRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Data refresh: push new data into the existing graph, no teardown.
  // Same rAF deferral: lets us cancel pending work if the parent reloads
  // BOM data twice in rapid succession.
  useEffect(() => {
    const graph = graphRef.current
    if (!graph || (graph as any).destroyed) return
    let id: number | null = requestAnimationFrame(() => {
      id = null
      if (!aliveRef.current || (graph as any).destroyed) return
      try {
        graph.setData(toGraphData(nodes, selectedId ?? null))
        graph.render().catch(() => {
          /* ignore late errors */
        })
      } catch {
        /* ignore — graph likely about to unmount */
      }
    })
    return () => {
      if (id !== null) cancelAnimationFrame(id)
    }
  }, [nodes, selectedId])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}
