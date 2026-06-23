'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE } from '@/lib/api'

export const dynamic = 'force-dynamic'

interface Task { id: string; assignee_username: string; assignee_display: string; scope: string; status: string; running: boolean; elapsed_seconds: number }
interface Project {
  id: string; enterprise_name?: string; part_no?: string; product_name?: string
  labor_rate: number; started_at?: string; submitted_at?: string; status: string
  total_seconds: number; man_hours: number; labor_cost: number; tasks: Task[]
  report?: { ok: boolean; error?: string; man_hours: number; labor_cost: number }
}

function hms(sec: number): string {
  sec = Math.max(0, Math.floor(sec))
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function ProjectWorkbench({ params }: { params: { id: string } }) {
  const { id } = params
  const [proj, setProj] = useState<Project | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const fetchedAt = useRef<number>(Date.now())
  const [, setTick] = useState(0)

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/amiba/projects/${id}`)
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || '加载失败')
      setProj(d); fetchedAt.current = Date.now()
    } catch (e) { setError((e as Error).message) }
  }, [id])

  useEffect(() => { load() }, [load])
  useEffect(() => { const t = setInterval(() => setTick((x) => x + 1), 1000); return () => clearInterval(t) }, [])

  async function act(taskId: string, action: 'start' | 'stop' | 'done') {
    const r = await fetch(`${API_BASE}/amiba/projects/${id}/tasks/${taskId}/${action}`, { method: 'POST' })
    const d = await r.json()
    if (r.ok) { setProj(d); fetchedAt.current = Date.now() }
  }
  async function submit() {
    if (!confirm('提交本 BOM 项目？将停止所有计时、汇总工时并回传到阿米巴。')) return
    setSubmitting(true)
    try {
      const r = await fetch(`${API_BASE}/amiba/projects/${id}/submit`, { method: 'POST' })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || '提交失败')
      setProj(d); fetchedAt.current = Date.now()
    } catch (e) { alert((e as Error).message) }
    finally { setSubmitting(false) }
  }

  if (error) return <Shell><p style={{ color: '#fca5a5' }}>{error}</p></Shell>
  if (!proj) return <Shell><p style={{ color: '#94a3b8' }}>加载中…</p></Shell>

  const live = (t: Task) => t.elapsed_seconds + (t.running && proj.status !== 'submitted' ? (Date.now() - fetchedAt.current) / 1000 : 0)
  const totalLive = proj.tasks.reduce((s, t) => s + live(t), 0)
  const submitted = proj.status === 'submitted'

  return (
    <Shell>
      {/* 头部 */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700, color: '#f8fafc' }}>{proj.product_name} <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#64748b' }}>{proj.part_no}</span></div>
          <div style={{ fontSize: 12, color: '#94a3b8' }}>{proj.enterprise_name} · BOM 多人多线程编制{submitted ? ' · 已提交' : ''}</div>
        </div>
        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ fontSize: 26, fontWeight: 700, fontFamily: 'monospace', color: '#34d399' }}>{hms(totalLive)}</div>
          <div style={{ fontSize: 11, color: '#94a3b8' }}>总人工工时 {(totalLive / 3600).toFixed(2)}h · 估算成本 ¥{Math.round(totalLive / 3600 * proj.labor_rate).toLocaleString()}</div>
        </div>
      </div>

      {/* 任务（多人多线程，各自计时） */}
      <div style={{ marginTop: 18, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {proj.tasks.map((t) => (
          <div key={t.id} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: 10, border: '1px solid #1e293b', background: t.running && !submitted ? '#0c2a1d' : '#0f172a' }}>
            <span style={{ width: 120, fontSize: 13, fontWeight: 600 }}>{t.assignee_display}</span>
            <span style={{ flex: 1, minWidth: 140, fontSize: 12, color: '#94a3b8' }}>{t.scope}</span>
            <span style={{ fontFamily: 'monospace', fontSize: 15, color: t.running ? '#34d399' : '#cbd5e1' }}>{hms(live(t))}</span>
            <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, background: '#1e293b', color: t.status === 'done' ? '#6ee7b7' : t.running ? '#fcd34d' : '#94a3b8' }}>
              {t.status === 'done' ? '已完成' : t.running ? '进行中' : '待开始'}
            </span>
            {!submitted && (
              <span style={{ display: 'flex', gap: 6 }}>
                {!t.running
                  ? <button onClick={() => act(t.id, 'start')} disabled={t.status === 'done'} style={btn('#10b981')}>开始</button>
                  : <button onClick={() => act(t.id, 'stop')} style={btn('#f59e0b')}>暂停</button>}
                <button onClick={() => act(t.id, 'done')} disabled={t.status === 'done'} style={btn('#334155')}>完成</button>
              </span>
            )}
          </div>
        ))}
      </div>

      {/* 提交 / 回传结果 */}
      {!submitted ? (
        <button onClick={submit} disabled={submitting} style={{ ...btn('#10b981'), marginTop: 18, padding: '10px 20px', fontSize: 14 }}>
          {submitting ? '提交中…' : '提交并回传工时到阿米巴'}
        </button>
      ) : (
        <div style={{ marginTop: 18, padding: '12px 14px', borderRadius: 10, background: proj.report?.ok ? '#0c2a1d' : '#2a1d0c', border: `1px solid ${proj.report?.ok ? '#14532d' : '#533a14'}`, color: proj.report?.ok ? '#6ee7b7' : '#fcd34d', fontSize: 13 }}>
          {proj.report?.ok
            ? `已提交并回传阿米巴：总工时 ${proj.man_hours}h · 人工成本 ¥${Math.round(proj.labor_cost).toLocaleString()}。该数据已落到对应产品的「工艺与BOM准备」节点。`
            : `已提交（总工时 ${proj.man_hours}h），但回传阿米巴未成功：${proj.report?.error || '未知'}。可在阿米巴侧检查连接器令牌/地址。`}
        </div>
      )}
    </Shell>
  )
}

function btn(bg: string): React.CSSProperties {
  return { background: bg, color: '#fff', border: 'none', borderRadius: 8, padding: '5px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer' }
}
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main style={{ minHeight: '100vh', padding: 24, fontFamily: 'system-ui,"PingFang SC",sans-serif', background: '#0b1220', color: '#e2e8f0' }}>
      <div style={{ maxWidth: 760, margin: '0 auto', padding: 24, border: '1px solid #1e293b', borderRadius: 16, background: '#0f172a' }}>
        <div style={{ fontWeight: 700, color: '#34d399', marginBottom: 12 }}>PEBS BOM · 阿米巴工作台</div>
        {children}
      </div>
    </main>
  )
}
