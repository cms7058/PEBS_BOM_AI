'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { amibaConnect, amibaStatus, amibaSync, API_BASE, setAdminToken, setCurrentUser, setUserName, type AmibaStatus } from '@/lib/api'

export const dynamic = 'force-dynamic'

type Phase = 'idle' | 'connecting' | 'done' | 'error' | 'missing'

interface Params {
  amiba_endpoint: string
  amiba_token: string
  enterprise_id: string
  source: string
}

function readParams(): Params {
  if (typeof window === 'undefined') {
    return { amiba_endpoint: '', amiba_token: '', enterprise_id: '', source: 'bom' }
  }
  const q = new URLSearchParams(window.location.search)
  return {
    amiba_endpoint: q.get('amiba_endpoint') || '',
    amiba_token: q.get('amiba_token') || '',
    enterprise_id: q.get('enterprise_id') || '',
    source: q.get('source') || 'bom',
  }
}

const S = {
  shell: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '24px',
    background: 'linear-gradient(160deg,#0f172a 0%,#1e293b 100%)',
    color: '#e2e8f0',
    fontFamily: 'system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif',
  } as const,
  card: {
    width: '100%',
    maxWidth: 520,
    background: '#0b1220',
    border: '1px solid #1e293b',
    borderRadius: 16,
    padding: '28px 28px 24px',
    boxShadow: '0 24px 60px rgba(0,0,0,.45)',
  } as const,
  brand: { display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700, fontSize: 15 } as const,
  cube: {
    width: 18,
    height: 18,
    borderRadius: 5,
    background: 'linear-gradient(135deg,#34d399,#10b981)',
    display: 'inline-block',
  } as const,
  h1: { margin: '18px 0 6px', fontSize: 20, fontWeight: 700, color: '#f8fafc' } as const,
  sub: { margin: 0, fontSize: 13, lineHeight: 1.6, color: '#94a3b8' } as const,
  row: { display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 13, padding: '7px 0', borderBottom: '1px solid #16202e' } as const,
  key: { color: '#94a3b8' } as const,
  val: { color: '#e2e8f0', fontFamily: 'ui-monospace,monospace', wordBreak: 'break-all' as const, textAlign: 'right' as const } as const,
  chips: { display: 'flex', flexWrap: 'wrap' as const, gap: 6, marginTop: 12 } as const,
  chip: { fontSize: 11, padding: '3px 9px', borderRadius: 999, background: '#10241c', color: '#6ee7b7', border: '1px solid #14532d' } as const,
  ok: { marginTop: 16, padding: '12px 14px', borderRadius: 10, background: '#0c2a1d', border: '1px solid #14532d', color: '#6ee7b7', fontSize: 13 } as const,
  warn: { marginTop: 16, padding: '12px 14px', borderRadius: 10, background: '#2a1d0c', border: '1px solid #533a14', color: '#fcd34d', fontSize: 13 } as const,
  err: { marginTop: 16, padding: '12px 14px', borderRadius: 10, background: '#2a0c0c', border: '1px solid #531414', color: '#fca5a5', fontSize: 13 } as const,
  btnRow: { display: 'flex', gap: 10, marginTop: 20 } as const,
  btn: { flex: 1, textAlign: 'center' as const, padding: '10px 16px', borderRadius: 10, fontSize: 13, fontWeight: 600, cursor: 'pointer', border: 'none' } as const,
  btnPrimary: { background: '#10b981', color: '#04130c' } as const,
  btnGhost: { background: 'transparent', color: '#94a3b8', border: '1px solid #1e293b', textDecoration: 'none', display: 'inline-block' } as const,
}

export default function RegisterPage() {
  const [params, setParams] = useState<Params | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [status, setStatus] = useState<AmibaStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)

  async function runSync() {
    setSyncing(true)
    try {
      await amibaSync()
      setStatus(await amibaStatus())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSyncing(false)
    }
  }

  // 读取 URL 参数并触发接入
  useEffect(() => {
    const p = readParams()
    setParams(p)

    // 若携带平台登录参数，建立 BOM 端会话；若同时带了产品，则按产品建 BOM 项目并
    // 直接进入编制页（与 /amiba/launch 一致，使「接入时选产品」与其它工具统一）。
    const q = new URLSearchParams(window.location.search)
    const platformToken = q.get('platform_token')
    const username = q.get('username')
    const productId = q.get('product_id')
    if (platformToken && username && p.amiba_endpoint) {
      ;(async () => {
        try {
          const login = await fetch(`${API_BASE}/amiba/platform-login`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amiba_endpoint: p.amiba_endpoint, username, platform_token: platformToken, tool: p.source }),
          })
          const ld = await login.json()
          if (!ld.ok) return
          setAdminToken(ld.session_token)
          setUserName(ld.display_name || username)
          setCurrentUser({
            id: ld.userId || username, tenant_id: 'default', username,
            display_name: ld.display_name || username, role: 'amiba',
            email: username.includes('@') ? username : null, phone: null, status: 'active',
            trial_expires_at: new Date(Date.now() + 365 * 24 * 3600 * 1000).toISOString(),
          })
          // 带产品：按产品建/复用 BOM 项目，直接进编制页
          if (productId) {
            let team: { username: string; displayName?: string }[] = []
            try { team = JSON.parse(q.get('team') || '[]') } catch {}
            const proj = await fetch(`${API_BASE}/amiba/projects`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                enterprise_id: p.enterprise_id, enterprise_name: q.get('enterprise_name'),
                product_id: productId, part_no: q.get('part_no'), product_name: q.get('product_name'),
                amiba_endpoint: p.amiba_endpoint, connector_token: p.amiba_token,
                created_by_username: username, team,
              }),
            })
            const pd = await proj.json()
            if (pd.bom_id) window.location.assign(`/bom/${pd.bom_id}`)
            else if (pd.id) window.location.assign(`/amiba/project/${pd.id}`)
          }
        } catch { /* ignore */ }
      })()
    }

    if (!p.amiba_endpoint || !p.amiba_token || !p.enterprise_id) {
      setPhase('missing')
      // 仍尝试读取已有接入状态（可能是直接进来查看的）
      amibaStatus().then(setStatus).catch(() => {})
      return
    }
    setPhase('connecting')
    amibaConnect({
      amiba_endpoint: p.amiba_endpoint,
      amiba_token: p.amiba_token,
      enterprise_id: p.enterprise_id,
      source: p.source,
    })
      .then((res) => {
        setStatus(res)
        setPhase(res.hello_ok ? 'done' : 'error')
        if (!res.hello_ok) setError(res.hello_error || '已保存接入信息，但回连阿米巴失败')
      })
      .catch((e: Error) => {
        setError(e.message)
        setPhase('error')
      })
  }, [])

  return (
    <main style={S.shell}>
      <div style={S.card}>
        <div style={S.brand}>
          <span style={S.cube} />
          <span>PEBS BOM</span>
          <span style={{ color: '#475569', fontWeight: 400 }}>·</span>
          <span style={{ color: '#94a3b8', fontWeight: 400, fontSize: 13 }}>接入阿米巴动态智能体</span>
        </div>

        {phase === 'connecting' && (
          <>
            <h1 style={S.h1}>正在接入…</h1>
            <p style={S.sub}>正在向阿米巴登记 PEBS BOM 的能力，请稍候。</p>
          </>
        )}

        {phase === 'missing' && (
          <>
            <h1 style={S.h1}>{status?.connected ? '已接入阿米巴' : '缺少接入参数'}</h1>
            <p style={S.sub}>
              {status?.connected
                ? '本系统已与阿米巴建立连接。如需重新接入，请回到阿米巴「工具接入」页点击接入。'
                : '此页面应由阿米巴「工具接入」页点击「接入并跳转」自动打开（携带令牌与企业信息）。直接访问缺少必要参数。'}
            </p>
          </>
        )}

        {(phase === 'done' || phase === 'error') && (
          <>
            <h1 style={S.h1}>{phase === 'done' ? '接入成功 ✓' : '接入未完成'}</h1>
            <p style={S.sub}>
              {phase === 'done'
                ? 'PEBS BOM 已登记到阿米巴，对应 OTD 节点会显示「已注册」。后续 BOM 指标将回填到阿米巴节点。'
                : '接入信息已保存，但与阿米巴的连接校验未通过，请检查阿米巴地址是否可达、令牌是否有效。'}
            </p>
          </>
        )}

        {(status?.connected || phase === 'done' || phase === 'error') && status && (
          <div style={{ marginTop: 18 }}>
            <div style={S.row}><span style={S.key}>服务企业 ID</span><span style={S.val}>{status.enterprise_id || params?.enterprise_id}</span></div>
            <div style={S.row}><span style={S.key}>阿米巴地址</span><span style={S.val}>{status.amiba_endpoint || params?.amiba_endpoint}</span></div>
            <div style={S.row}><span style={S.key}>能力上报</span><span style={S.val}>{status.hello_ok ? '已确认' : '待确认'}</span></div>
            {status.last_hello_at && (
              <div style={S.row}><span style={S.key}>最近上报</span><span style={S.val}>{new Date(status.last_hello_at).toLocaleString('zh-CN')}</span></div>
            )}
            {!!status.capabilities?.length && (
              <div style={S.chips}>
                {status.capabilities.map((c) => (
                  <span key={c} style={S.chip}>{c}</span>
                ))}
              </div>
            )}
          </div>
        )}

        {phase === 'done' && <div style={S.ok}>能力已上报阿米巴，接入闭环完成。</div>}
        {phase === 'error' && <div style={S.err}>{error}</div>}

        {status?.connected && (
          <div style={{ marginTop: 14, padding: '12px 14px', borderRadius: 10, background: '#0b1c2a', border: '1px solid #14304a' }}>
            <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 6 }}>指标回填阿米巴 OTD 节点</div>
            {status.last_sync_summary ? (
              <div style={{ fontSize: 12.5, color: '#bae6fd', lineHeight: 1.6 }}>{status.last_sync_summary}</div>
            ) : (
              <div style={{ fontSize: 12.5, color: '#64748b' }}>尚未同步</div>
            )}
            {status.last_sync_at && (
              <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>最近同步 {new Date(status.last_sync_at).toLocaleString('zh-CN')}</div>
            )}
            <button
              onClick={runSync}
              disabled={syncing}
              style={{ ...S.btn, ...S.btnPrimary, marginTop: 12, width: '100%', opacity: syncing ? 0.6 : 1 }}
            >
              {syncing ? '同步中…' : '立即同步指标'}
            </button>
          </div>
        )}

        <div style={S.btnRow}>
          <Link href="/" style={{ ...S.btn, ...S.btnGhost }}>进入 BOM 工作台</Link>
          {params?.amiba_endpoint && (
            <a href={params.amiba_endpoint} style={{ ...S.btn, ...S.btnPrimary }}>返回阿米巴</a>
          )}
        </div>
      </div>
    </main>
  )
}
