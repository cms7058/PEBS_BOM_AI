import Link from 'next/link'
import UserStatusMenu from '@/components/UserStatusMenu'
import { API_BASE, type SubscriptionPlan } from '@/lib/api'

export const dynamic = 'force-dynamic'

const PLAN_TAGS: Record<string, string[]> = {
  personal: ['匿名预览', '基础解析', '个人工作台'],
  team: ['多人协同', '任务指派', '历史追溯'],
  enterprise: ['私有化', '功能开关', '数据治理'],
}

const FALLBACK_PLANS: SubscriptionPlan[] = [
  {
    id: 'personal',
    name: '个人版',
    tenant_type: 'personal',
    description: '个人工程师快速体验 BOM 解析、结构查看和基础物料映射。',
    price_label: '¥99 / 年',
    price_cents: 9900,
    currency: 'CNY',
    duration_days: 365,
    seat_limit: 1,
    bom_limit: 20,
    enabled: true,
    sort_order: 10,
  },
  {
    id: 'team',
    name: '团队版',
    tenant_type: 'team',
    description: '研发小组共享 BOM 数据，支持任务协作、编辑历史和物料确认。',
    price_label: '¥999 / 年',
    price_cents: 99900,
    currency: 'CNY',
    duration_days: 365,
    seat_limit: 10,
    bom_limit: 300,
    enabled: true,
    sort_order: 20,
  },
  {
    id: 'enterprise',
    name: '企业版',
    tenant_type: 'enterprise',
    description: '面向私有化部署与云端生产环境，支持更完整的数据治理能力。',
    price_label: '¥9999 / 年',
    price_cents: 999900,
    currency: 'CNY',
    duration_days: 365,
    seat_limit: null,
    bom_limit: null,
    enabled: true,
    sort_order: 30,
  },
]

async function fetchPlans(): Promise<SubscriptionPlan[]> {
  try {
    const res = await fetch(`${API_BASE}/admin/plans`, { cache: 'no-store' })
    if (!res.ok) return FALLBACK_PLANS
    const items = (await res.json()) as SubscriptionPlan[]
    return items.length > 0 ? items : FALLBACK_PLANS
  } catch {
    return FALLBACK_PLANS
  }
}

function formatPrice(plan: SubscriptionPlan): string {
  const amount = (plan.price_cents / 100).toLocaleString('zh-CN')
  return plan.currency === 'CNY' ? `${amount} 元` : `${plan.currency} ${amount}`
}

export default async function GuestPage({
  searchParams,
}: {
  searchParams?: { reason?: string }
}) {
  const plans = await fetchPlans()
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
        <div className="guest-alert">用户未登录，请先注册或登录后继续访问工作台。</div>
      )}

      <section className="home-hero guest-hero">
        <div className="hero-copy">
          <div className="hero-kicker">✧ 匿名访问 · 先体验，再开通</div>
          <h1>
            <span>对话式 BOM</span>
            <b> · 多租户订阅 · 企业级配置</b>
          </h1>
          <p>
            未登录用户可以查看平台能力、订阅模式和示例工作流。正式使用时，
            系统会根据个人、团队、企业三种租户类型开放对应功能。
          </p>
          <div className="hero-tags">
            <span>▣ BOM 智能解析</span>
            <span>✧ 物料映射</span>
            <span>♙ 租户隔离</span>
            <span>⟳ 功能开关</span>
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

      <section className="guest-plans">
        {plans.map((plan) => (
          <article className="guest-plan" key={plan.name}>
            <h2>{plan.name}</h2>
            <div className="guest-price">
              <strong>{formatPrice(plan)}</strong>
              <span>有效期 {plan.duration_days} 天</span>
            </div>
            <p>{plan.description}</p>
            <div>
              {(PLAN_TAGS[plan.id] || PLAN_TAGS[plan.tenant_type] || ['订阅开通']).map((tag) => (
                <span key={tag}>{tag}</span>
              ))}
            </div>
          </article>
        ))}
      </section>
    </main>
  )
}
