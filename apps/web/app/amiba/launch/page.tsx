'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { API_BASE, setAdminToken, setCurrentUser, setUserName } from '@/lib/api'

export const dynamic = 'force-dynamic'

// 阿米巴「打开 BOM 工作台」落地页：平台登录 → 按产品建/复用项目 → 进入工作台。
export default function AmibaLaunchPage() {
  const router = useRouter()
  const [msg, setMsg] = useState('正在用平台令牌登录…')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    (async () => {
      const q = new URLSearchParams(window.location.search)
      const amiba_endpoint = q.get('amiba_endpoint') || ''
      const username = q.get('username') || ''
      const platform_token = q.get('platform_token') || ''
      const tool = q.get('tool') || 'bom'
      if (!amiba_endpoint || !username || !platform_token) {
        setError('缺少平台登录参数，请从阿米巴「产品 · BOM 工作台」打开。')
        return
      }
      try {
        const login = await fetch(`${API_BASE}/amiba/platform-login`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amiba_endpoint, username, platform_token, tool }),
        })
        const ld = await login.json()
        if (!login.ok) throw new Error(ld.detail || '平台登录失败')
        // 建立 BOM 前端可识别的会话，使 AuthGuard 放行（阿米巴平台用户视作有效内测用户）
        try {
          localStorage.setItem('amiba_bom_session', ld.session_token)
          setAdminToken(ld.session_token)
          setUserName(ld.display_name || username)
          setCurrentUser({
            id: ld.userId || username,
            tenant_id: 'default',
            username,
            display_name: ld.display_name || username,
            role: 'amiba',
            email: username.includes('@') ? username : null,
            phone: null,
            status: 'active',
            trial_expires_at: new Date(Date.now() + 365 * 24 * 3600 * 1000).toISOString(),
          })
        } catch {}

        setMsg('登录成功，正在按产品建立 BOM 项目…')
        let team: { username: string; displayName?: string }[] = []
        try { team = JSON.parse(q.get('team') || '[]') } catch {}
        const proj = await fetch(`${API_BASE}/amiba/projects`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            enterprise_id: q.get('enterprise_id'),
            enterprise_name: q.get('enterprise_name'),
            product_id: q.get('product_id'),
            part_no: q.get('part_no'),
            product_name: q.get('product_name'),
            amiba_endpoint,
            connector_token: q.get('connector_token'),
            created_by_username: username,
            team,
          }),
        })
        const pd = await proj.json()
        if (!proj.ok) throw new Error(pd.detail || '建项目失败')
        // 直接进入实际 BOM 编制页（携带产品/人员上下文，由页内横幅承载计时与提交）
        if (pd.bom_id) router.replace(`/bom/${pd.bom_id}`)
        else router.replace(`/amiba/project/${pd.id}`)
      } catch (e) {
        setError((e as Error).message)
      }
    })()
  }, [router])

  return (
    <main style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'system-ui,"PingFang SC",sans-serif', background: '#0b1220', color: '#e2e8f0' }}>
      <div style={{ maxWidth: 460, padding: 28, border: '1px solid #1e293b', borderRadius: 16, background: '#0f172a' }}>
        <div style={{ fontWeight: 700, color: '#34d399' }}>PEBS BOM · 阿米巴模式</div>
        {!error ? (
          <p style={{ marginTop: 14, fontSize: 14, color: '#94a3b8' }}>{msg}</p>
        ) : (
          <>
            <h3 style={{ margin: '14px 0 6px', color: '#fca5a5' }}>无法进入工作台</h3>
            <p style={{ margin: 0, fontSize: 13, color: '#fca5a5' }}>{error}</p>
          </>
        )}
      </div>
    </main>
  )
}
