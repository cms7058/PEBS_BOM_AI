import Link from 'next/link'
import UserStatusMenu from '@/components/UserStatusMenu'

export const dynamic = 'force-dynamic'

export default function GuestPage({
  searchParams,
}: {
  searchParams?: { reason?: string }
}) {
  const unauth = searchParams?.reason === 'unauth'
  return (
    <main className="home-shell guest-shell">
      <div className="home-topbar">
        <Link href="/guest" className="brand-mark">
          <span className="brand-cube" />
          <span>PEBS BOM</span>
        </Link>
        <div className="home-meta">
          <UserStatusMenu />
        </div>
      </div>

      {unauth && (
        <div className="guest-alert">当前处于内测阶段，请先使用邮箱和邀请码登录。</div>
      )}

      <section className="home-hero guest-hero">
        <div className="hero-copy">
          <div className="hero-kicker">✧ PEBS BOM 内测开放</div>
          <h1>
            <span>邮箱验证</span>
            <b> · 邀请码准入 · 企业版体验</b>
          </h1>
          <p>
            内测阶段暂时关闭订阅页面。受邀用户通过邮箱和邀请码验证后，
            默认获得 14 天企业版体验额度，可导入 10 个 BOM、导出 10 个 BOM。
          </p>
          <div className="hero-tags">
            <span>▣ BOM 智能解析</span>
            <span>✧ 物料映射</span>
            <span>♙ 企业版权限</span>
            <span>⟳ 14 天内测</span>
          </div>
          <div className="guest-cta-row">
            <Link className="btn btn-primary" href="/login">进入内测登录</Link>
          </div>
        </div>

        <div className="hero-visual" aria-hidden="true">
          <div className="orbit orbit-a" />
          <div className="orbit orbit-b" />
          <div className="glass-card card-a" />
          <div className="glass-card card-b" />
          <div className="glass-card card-c" />
          <div className="core-cube" />
          <div className="platform-ring" />
        </div>
      </section>
    </main>
  )
}
