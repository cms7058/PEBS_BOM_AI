'use client'
import { Graph } from '@antv/g6'
import { useEffect, useRef } from 'react'
import type { BOMNode } from '@/lib/api'

interface Props {
  nodes: BOMNode[]
}

// Build {nodes, edges} payload from BOMNode list.
function toG6Data(nodes: BOMNode[]) {
  const g6Nodes = nodes.map((n) => ({
    id: n.id,
    data: {
      label: n.part_name,
      partNumber: n.part_number,
      qty: n.quantity,
      uom: n.uom,
      level: n.level,
      confidence: n.confidence,
    },
    style: n.style || {},
  }))
  const g6Edges = nodes
    .filter((n) => n.parent_id)
    .map((n) => ({ id: `${n.parent_id}->${n.id}`, source: n.parent_id!, target: n.id }))
  return { nodes: g6Nodes, edges: g6Edges }
}

export default function BOMGraph({ nodes }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const graphRef = useRef<Graph | null>(null)
  // Tracks which mount instance is current; used to ignore async work
  // (render promises) that resolve after we've already destroyed the graph.
  const aliveRef = useRef(true)

  // Mount once: create the Graph instance and do initial render.
  // Data updates happen in the second effect below — we never destroy +
  // recreate on prop change because G6 v5 has an async render pipeline that
  // races with destroy() and crashes deep inside graph.js with
  // "Cannot read properties of undefined (reading 'draw')".
  useEffect(() => {
    if (!containerRef.current) return
    aliveRef.current = true

    const graph = new Graph({
      container: containerRef.current,
      autoFit: 'view',
      data: toG6Data(nodes),
      node: {
        type: 'rect',
        style: {
          size: [180, 44],
          radius: 6,
          fill: (d: any) => (d.data?.confidence < 0.6 ? '#fff7e6' : '#eaf4ff'),
          stroke: '#1677ff',
          lineWidth: 1,
          labelText: (d: any) => {
            const pn = d.data?.partNumber ? `[${d.data.partNumber}] ` : ''
            return `${pn}${d.data?.label}\nx${d.data?.qty} ${d.data?.uom}`
          },
          labelFill: '#1f2329',
          labelFontSize: 12,
          labelPlacement: 'center',
        },
      },
      edge: {
        type: 'polyline',
        style: { stroke: '#9ca3af', endArrow: true },
      },
      layout: {
        type: 'dagre',
        rankdir: 'LR',
        nodesep: 20,
        ranksep: 60,
      },
      behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'click-select'],
    })

    graphRef.current = graph
    // .render() returns a promise; swallow late errors that happen after
    // the user navigates away mid-render.
    graph.render().catch(() => {
      /* ignore — likely "graph destroyed" */
    })

    const onResize = () => {
      if (aliveRef.current && graphRef.current) {
        try { graphRef.current.resize() } catch { /* ignore */ }
      }
    }
    window.addEventListener('resize', onResize)

    return () => {
      aliveRef.current = false
      window.removeEventListener('resize', onResize)
      try {
        graph.destroy()
      } catch {
        /* ignore */
      }
      graphRef.current = null
    }
    // Intentionally empty deps: this effect must run exactly once per mount.
    // Data refresh is handled by the next effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Data refresh: when `nodes` prop changes, push the new data into the
  // existing graph and re-layout. No teardown.
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    try {
      // G6 v5: setData replaces the entire graph data, then render() does
      // a layout + draw. This is the recommended in-place refresh path.
      graph.setData(toG6Data(nodes))
      graph.render().catch(() => {
        /* ignore late errors */
      })
    } catch {
      /* ignore — graph likely about to unmount */
    }
  }, [nodes])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}
