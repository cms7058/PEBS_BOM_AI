'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { FormEvent, useState } from 'react'
import { adminLogin, internalBetaLogin } from '@/lib/api'

export default function LoginForm({ forcedPlan }: { forcedPlan?: string } = {}) {
  const router = useRouter()
  const isAdmin = forcedPlan === 'admin'
  const [email, setEmail] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('admin123456')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const result = isAdmin
        ? await adminLogin(username, password)
        : await internalBetaLogin(email, inviteCode)
      router.push(result.user.role === 'super_admin' ? '/admin' : '/')
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="auth-shell">
      <div className="home-topbar">
        <Link href="/guest" className="brand-mark">
          <span className="brand-cube" />
          <span>PEBS BOM</span>
        </Link>
        <div className="home-meta">
          <span className="user-dot">♙</span>
          <span>{isAdmin ? 'admin' : 'internal beta'}</span>
        </div>
      </div>

      <section className="auth-stage">
        <div className="auth-copy">
          <div className="hero-kicker">✧ {isAdmin ? '后台管理入口' : 'PEBS BOM 内测'}</div>
          <h1>{isAdmin ? '超级管理员登录' : '邮箱与邀请码登录'}</h1>
          {isAdmin ? (
            <p>超级管理员登录后可继续进入后台管理订阅、用户和功能权限。内测阶段后台保留，不对普通用户展示。</p>
          ) : (
            <div className="auth-feature-copy">
              <p>
                当前为内测阶段，订阅页面暂时关闭。通过邮箱和邀请码验证后，
                默认进入企业版能力空间，可上传 BOM 开始体验。
              </p>
              <ul>
                <li>
                  <b>企业版能力</b>
                  <span>默认开放自有物料、BOM 解析、物料映射等核心能力。</span>
                </li>
                <li>
                  <b>内测额度</b>
                  <span>有效期 14 天，最多导入 10 个 BOM、导出 10 个 BOM。</span>
                </li>
                <li>
                  <b>邀请验证</b>
                  <span>邮箱与邀请码会通过 PEBS 云函数实时校验。</span>
                </li>
              </ul>
            </div>
          )}
        </div>

        <form className="auth-card" onSubmit={submit}>
          {isAdmin ? (
            <>
              <label>
                <span>用户名</span>
                <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="请输入管理员用户名" />
              </label>
              <label>
                <span>密码</span>
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="请输入管理员密码" />
              </label>
            </>
          ) : (
            <>
              <label>
                <span>邮箱</span>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="请输入内测登记邮箱"
                />
              </label>
              <label>
                <span>邀请码</span>
                <input
                  value={inviteCode}
                  onChange={(e) => setInviteCode(e.target.value)}
                  placeholder="请输入 PEBS 内测邀请码"
                />
              </label>
              <div className="subscription-box">
                <div>
                  <span>默认权限</span>
                  <strong>企业版</strong>
                </div>
                <div>
                  <span>有效时间</span>
                  <strong>14 天</strong>
                </div>
                <div>
                  <span>BOM 导入/导出</span>
                  <strong>10 / 10</strong>
                </div>
              </div>
            </>
          )}
          {error && <p className="auth-error">{error}</p>}
          <button className="btn btn-primary auth-submit" disabled={loading} type="submit">
            {loading ? '验证中...' : isAdmin ? '登录后台' : '验证并进入'}
          </button>
        </form>
      </section>
    </main>
  )
}
