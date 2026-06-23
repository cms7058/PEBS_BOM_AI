'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE } from '@/lib/api'

interface Task { id: string; assignee_display: string; scope: string; status: string; running: boolean; elapsed_seconds: number }
interface Project {
  id: string; bom_id?: string; enterprise_name?: string; part_no?: string; product_name?: string
  labor_rate: number; status: string; total_seconds: number; man_hours: number; labor_cost: number
  tasks: Task[]; report?: { ok: boolean; error?: string; man_hours: number; labor_cost: number }
}

function hms(sec: number): string {
  sec = Math.max(0, Math.floor(sec))
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// 阿米巴项目横幅：挂在实际 BOM 编制页(/bom/[id])顶部，承载产品/人员/计时/提交回传。
export default function AmibaProjectBanner({ bomId }: { bomId: string }) {
  const [proj, setProj] = useState<Project | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const fetchedAt = useRef<number>(Date.now())
  const [, setTick] = useState(0)

  const load = useCallback(async () => {
    try {
      const d = await fetch(`${API_BASE}/amiba/projects/by-bom/${bomId}`).then((r) => r.json())
      if (d.project) { setProj(d.project); fetchedAt.current = Date.now() }
    } catch { /* ignore */ }
  }, [bomId])

  useEffect(() => { load() }, [load])
  useEffect(() => { const t = setInterval(() => setTick((x) => x + 1), 1000); return () => clearInterval(t) }, [])

  if (!proj) return null
  const submitted = proj.status === 'submitted'
  const live = (t: Task) => t.elapsed_seconds + (t.running && !submitted ? (Date.now() - fetchedAt.current) / 1000 : 0)
  const totalLive = proj.tasks.reduce((s, t) => s + live(t), 0)

  async function act(taskId: string, action: 'start' | 'stop' | 'done') {
    const r = await fetch(`${API_BASE}/amiba/projects/${proj!.id}/tasks/${taskId}/${action}`, { method: 'POST' })
    if (r.ok) { setProj(await r.json()); fetchedAt.current = Date.now() }
  }
  async function submit() {
    if (!confirm('提交本 BOM 项目？将停止计时、汇总工时并回传阿米巴。')) return
    setSubmitting(true)
    try {
      const r = await fetch(`${API_BASE}/amiba/projects/${proj!.id}/submit`, { method: 'POST' })
      const d = await r.json()
      if (r.ok) { setProj(d); fetchedAt.current = Date.now() }
    } finally { setSubmitting(false) }
  }

  return (
    <div style={{ margin: '0 0 12px', padding: '12px 16px', borderRadius: 12, border: '1px solid #14532d', background: 'linear-gradient(180deg,#0c2a1d,#0b1f17)', color: '#d1fae5', fontFamily: 'system-ui,"PingFang SC",sans-serif' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
        <div>
          <div style={{ fontSize: 12, color: '#6ee7b7', fontWeight: 700 }}>阿米巴项目 · {proj.enterprise_name}</div>
          <div style={{ fontSize: 15, fontWeight: 700, color: '#ecfdf5' }}>{proj.product_name} <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#6ee7b7' }}>{proj.part_no}</span></div>
        </div>
        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ fontSize: 22, fontWeight: 700, fontFamily: 'monospace', color: '#34d399' }}>{hms(totalLive)}</div>
          <div style={{ fontSize: 11, color: '#6ee7b7' }}>总工时 {(totalLive / 3600).toFixed(2)}h · 估算 ¥{Math.round(totalLive / 3600 * proj.labor_rate).toLocaleString()}</div>
        </div>
      </div>

      <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {proj.tasks.map((t) => (
          <div key={t.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', borderRadius: 8, background: t.running && !submitted ? '#10422e' : '#0a1813', border: '1px solid #14532d' }}>
            <span style={{ fontSize: 12, fontWeight: 600 }}>{t.assignee_display}</span>
            <span style={{ fontSize: 11, color: '#6ee7b7' }}>{t.scope}</span>
            <span style={{ fontFamily: 'monospace', fontSize: 13, color: t.running ? '#34d399' : '#a7f3d0' }}>{hms(live(t))}</span>
            {!submitted && (
              <>
                {!t.running
                  ? <button onClick={() => act(t.id, 'start')} disabled={t.status === 'done'} style={btn('#10b981')}>开始</button>
                  : <button onClick={() => act(t.id, 'stop')} style={btn('#f59e0b')}>暂停</button>}
                <button onClick={() => act(t.id, 'done')} disabled={t.status === 'done'} style={btn('#334155')}>{t.status === 'done' ? '已完成' : '完成'}</button>
              </>
            )}
          </div>
        ))}
      </div>

      {!submitted ? (
        <button onClick={submit} disabled={submitting} style={{ ...btn('#10b981'), marginTop: 10, padding: '8px 18px', fontSize: 13 }}>
          {submitting ? '提交中…' : '提交并回传工时到阿米巴'}
        </button>
      ) : (
        <div style={{ marginTop: 10, fontSize: 12, color: proj.report?.ok ? '#6ee7b7' : '#fcd34d' }}>
          {proj.report?.ok
            ? `已提交并回传：总工时 ${proj.man_hours}h · 人工成本 ¥${Math.round(proj.labor_cost).toLocaleString()}（已落到产品「工艺与BOM准备」节点）。`
            : `已提交（${proj.man_hours}h），回传未成功：${proj.report?.error || '未知'}。`}
        </div>
      )}
    </div>
  )
}

function btn(bg: string): React.CSSProperties {
  return { background: bg, color: '#fff', border: 'none', borderRadius: 6, padding: '3px 10px', fontSize: 11, fontWeight: 600, cursor: 'pointer' }
}
