'use client'
import { useEffect, useState } from 'react'
import { applyHierarchy, detectHierarchy } from '@/lib/api'
import type { HierarchyRule } from '@/lib/api'

export default function HierarchyRulePanel({
  bomId,
  onApplied,
}: {
  bomId: string
  onApplied: () => void
}) {
  const [rule, setRule] = useState<HierarchyRule | null>(null)
  const [separator, setSeparator] = useState('-')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    detectHierarchy(bomId)
      .then((r) => {
        setRule(r)
        setSeparator(r.separator)
      })
      .catch(() => setMsg('识别失败'))
  }, [bomId])

  async function onApply() {
    if (!separator) return
    setBusy(true)
    setMsg(null)
    try {
      const r = await applyHierarchy(bomId, separator)
      setMsg(
        `已重建：${r.top_level} 个顶层节点，${r.linked} 个关联到父节点，` +
          `${r.orphans} 个孤立，${r.no_partnumber} 个无零件号`,
      )
      onApplied()
    } catch (e: any) {
      setMsg(`错误: ${e?.message || e}`)
    } finally {
      setBusy(false)
    }
  }

  if (!rule) {
    return <div style={{ padding: 10, fontSize: 12, color: '#8b949e' }}>识别零件号层级规则中…</div>
  }

  const confPct = Math.round(rule.confidence * 100)
  return (
    <div style={{ padding: '10px 14px', borderBottom: '1px solid #e5e7eb', background: '#fafbfc' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, color: '#6b7280' }}>
          零件号层级识别：置信度 <b style={{ color: confPct >= 50 ? '#10b981' : '#d97706' }}>{confPct}%</b>
          {rule.orphan_count > 0 && (
            <span style={{ marginLeft: 8, color: '#6b7280' }}>孤立 {rule.orphan_count} 个</span>
          )}
        </span>
        <span style={{ fontSize: 13 }}>分隔符：</span>
        {rule.candidate_separators.map((s) => (
          <label key={s} style={{ fontSize: 13, cursor: 'pointer' }}>
            <input
              type="radio"
              name="sep"
              value={s}
              checked={separator === s}
              onChange={() => setSeparator(s)}
              style={{ marginRight: 4 }}
            />
            {s === '·' ? '中点(·)' : `"${s}"`}
          </label>
        ))}
        <input
          type="text"
          value={separator}
          onChange={(e) => setSeparator(e.target.value)}
          placeholder="或自定义"
          style={{
            width: 70,
            padding: '4px 6px',
            border: '1px solid #d0d7de',
            borderRadius: 4,
            fontSize: 13,
          }}
        />
        <button className="btn btn-primary" disabled={busy || !separator} onClick={onApply}>
          {busy ? '应用中...' : '应用规则并重建'}
        </button>
      </div>
      {rule.sample_chains.length > 0 && (
        <div style={{ marginTop: 6, fontSize: 12, color: '#6b7280' }}>
          样例链：
          {rule.sample_chains.map((c, i) => (
            <span key={i} style={{ marginLeft: 8, fontFamily: 'monospace' }}>
              {c[0]} → {c[1]}
            </span>
          ))}
        </div>
      )}
      {msg && (
        <div style={{ marginTop: 6, fontSize: 12, color: msg.startsWith('错误') ? '#d93025' : '#10b981' }}>
          {msg}
        </div>
      )}
    </div>
  )
}
