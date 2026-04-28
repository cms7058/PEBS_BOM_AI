'use client'
import { useState } from 'react'
import { chatStream } from '@/lib/api'

interface ToolCallRecord {
  name: string
  summary: string
  ok: boolean
  mutated: boolean
}

interface Msg {
  role: 'user' | 'assistant'
  content: string
  toolCalls?: ToolCallRecord[]
}

export default function AgentSidebar({
  bomId,
  onBomUpdated,
}: {
  bomId: string
  onBomUpdated?: () => void
}) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  // Live phase string from backend, e.g. "思考中…", "执行工具 bom_update_node…"
  // Stays visible until the request completes so the user always knows the
  // agent is still working (fixes "no feedback" complaint).
  const [phase, setPhase] = useState<string>('')

  async function send() {
    const text = input.trim()
    if (!text || busy) return
    setInput('')

    const history = msgs.map((m) => ({ role: m.role, content: m.content }))
    setMsgs((prev) => [
      ...prev,
      { role: 'user', content: text },
      { role: 'assistant', content: '', toolCalls: [] },
    ])
    setBusy(true)
    // Show "submitting" state immediately — first SSE event may take a sec.
    setPhase('正在发送请求…')

    const updateLast = (fn: (m: Msg) => Msg) =>
      setMsgs((prev) => {
        const copy = [...prev]
        copy[copy.length - 1] = fn(copy[copy.length - 1])
        return copy
      })

    try {
      for await (const evt of chatStream(bomId, text, history)) {
        if (evt.type === 'delta') {
          // First text token means the model is producing the reply.
          setPhase('回复生成中…')
          updateLast((m) => ({ ...m, content: m.content + evt.text }))
        } else if (evt.type === 'tool_call') {
          setPhase(
            evt.ok
              ? `工具 ${evt.name} 已完成${evt.mutated ? '（已修改 BOM）' : ''}`
              : `工具 ${evt.name} 失败`,
          )
          updateLast((m) => ({
            ...m,
            toolCalls: [
              ...(m.toolCalls || []),
              {
                name: evt.name,
                summary: evt.summary,
                ok: evt.ok,
                mutated: evt.mutated,
              },
            ],
          }))
        } else if (evt.type === 'status') {
          setPhase(evt.phase)
        } else if (evt.type === 'bom_updated') {
          // eslint-disable-next-line no-console
          console.log('[AgentSidebar] bom_updated event → triggering reload')
          onBomUpdated?.()
        } else if (evt.type === 'error') {
          updateLast((m) => ({ ...m, content: `[错误] ${evt.message}` }))
        }
      }
    } catch (ex: any) {
      updateLast((m) => ({ ...m, content: `[错误] ${ex?.message || ex}` }))
    } finally {
      setBusy(false)
      setPhase('')
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {busy && (
        <div
          style={{
            padding: '6px 12px',
            background: '#eff6ff',
            borderBottom: '1px solid #bfdbfe',
            color: '#1d4ed8',
            fontSize: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span
            aria-hidden="true"
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: '#1677ff',
              boxShadow: '0 0 0 0 rgba(22,119,255,0.6)',
              animation: 'agentPulse 1.2s infinite',
              flex: '0 0 auto',
            }}
          />
          <span style={{ flex: 1 }}>智能体工作中 · {phase || '处理中…'}</span>
          <span className="agent-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        </div>
      )}
      <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
        {msgs.length === 0 && (
          <p style={{ color: '#8b949e', fontSize: 13, lineHeight: 1.8 }}>
            试试：
            <br />• "列出所有零件"
            <br />• "在电机组件下加一个编码器子节点，数量 1"
            <br />• "所有外购件的节点改成红色描边"
            <br />• "把螺钉 M4×10 的数量改成 30"
            <br />• "删除刹车总成及其子节点"
          </p>
        )}
        {msgs.map((m, i) => (
          <div key={i} style={{ marginBottom: 14 }}>
            <div
              style={{
                fontSize: 12,
                color: m.role === 'user' ? '#1677ff' : '#10b981',
                marginBottom: 4,
              }}
            >
              {m.role === 'user' ? '你' : 'Assistant'}
            </div>
            {m.toolCalls && m.toolCalls.length > 0 && (
              <div style={{ marginBottom: 6 }}>
                {m.toolCalls.map((tc, j) => (
                  <div
                    key={j}
                    style={{
                      fontSize: 12,
                      padding: '4px 8px',
                      margin: '2px 0',
                      background: tc.ok ? (tc.mutated ? '#ecfdf3' : '#eff6ff') : '#fef2f2',
                      border: `1px solid ${tc.ok ? (tc.mutated ? '#10b981' : '#60a5fa') : '#ef4444'}`,
                      borderRadius: 4,
                      color: '#1f2329',
                    }}
                  >
                    <span style={{ fontFamily: 'monospace', color: '#6b7280' }}>
                      {tc.mutated ? '✎ ' : '· '}{tc.name}
                    </span>
                    <span style={{ marginLeft: 6 }}>{tc.summary}</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ whiteSpace: 'pre-wrap', fontSize: 14, lineHeight: 1.6 }}>
              {m.content || (busy && i === msgs.length - 1 ? '...' : '')}
            </div>
          </div>
        ))}
      </div>
      <div style={{ padding: 10, borderTop: '1px solid #e5e7eb', display: 'flex', gap: 8 }}>
        <input
          style={{
            flex: 1,
            padding: '8px 10px',
            border: '1px solid #d0d7de',
            borderRadius: 6,
            fontSize: 14,
          }}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          placeholder="向智能体提问或下指令…"
          disabled={busy}
        />
        <button className="btn btn-primary" onClick={send} disabled={busy}>
          {busy ? '...' : '发送'}
        </button>
      </div>
    </div>
  )
}
