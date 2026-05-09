'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import {
  AppUser,
  clearAdminToken,
  FeatureFlag,
  getAdminOverview,
  getAdminToken,
  SubscriptionPlan,
  Tenant,
  updateAdminFeature,
  updateAdminPlan,
  updateAdminUser,
} from '@/lib/api'

const PLAN_LABEL: Record<string, string> = {
  personal: '个人',
  team: '团队',
  enterprise: '企业',
}

type PlanDraft = {
  price_yuan: string
  currency: string
  duration_days: string
  price_label: string
  seat_limit: string
  bom_limit: string
}

export default function AdminPage() {
  const [plans, setPlans] = useState<SubscriptionPlan[]>([])
  const [features, setFeatures] = useState<FeatureFlag[]>([])
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [users, setUsers] = useState<AppUser[]>([])
  const [editingUserId, setEditingUserId] = useState<string | null>(null)
  const [userDraft, setUserDraft] = useState<Partial<AppUser> & { password?: string }>({})
  const [selectedPlan, setSelectedPlan] = useState('personal')
  const [planDraft, setPlanDraft] = useState<PlanDraft>({
    price_yuan: '',
    currency: 'CNY',
    duration_days: '',
    price_label: '',
    seat_limit: '',
    bom_limit: '',
  })
  const [loading, setLoading] = useState(true)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getAdminToken()) {
      window.location.href = '/bomadmin'
      return
    }
    const controller = new AbortController()
    getAdminOverview(controller.signal)
      .then((data) => {
        setPlans(data.plans)
        setFeatures(data.features)
        setTenants(data.tenants)
        setUsers(data.users || [])
        setSelectedPlan(data.plans[0]?.id || 'personal')
      })
      .catch((err) => setError(err instanceof Error ? err.message : '加载失败'))
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [])

  const selected = useMemo(
    () => plans.find((plan) => plan.id === selectedPlan) || plans[0],
    [plans, selectedPlan],
  )
  const selectedFeatures = useMemo(
    () => features.filter((feature) => feature.plan_id === selected?.id),
    [features, selected?.id],
  )

  useEffect(() => {
    if (!selected) return
    setPlanDraft({
      price_yuan: String(selected.price_cents / 100),
      currency: 'CNY',
      duration_days: String(selected.duration_days),
      price_label: selected.price_label || '',
      seat_limit: selected.seat_limit == null ? '' : String(selected.seat_limit),
      bom_limit: selected.bom_limit == null ? '' : String(selected.bom_limit),
    })
  }, [selected])

  async function applyOverview(next: Promise<{ plans: SubscriptionPlan[]; features: FeatureFlag[]; tenants: Tenant[]; users: AppUser[] }>) {
    const data = await next
    setPlans(data.plans)
    setFeatures(data.features)
    setTenants(data.tenants)
    setUsers(data.users || [])
  }

  function startEditUser(user: AppUser) {
    setEditingUserId(user.id)
    setUserDraft({
      display_name: user.display_name,
      email: user.email,
      phone: user.phone,
      role: user.role,
      status: user.status,
      password: '',
    })
  }

  async function saveUser(user: AppUser) {
    setSavingId(user.id)
    setError(null)
    try {
      await applyOverview(updateAdminUser(user.id, userDraft))
      setEditingUserId(null)
      setUserDraft({})
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSavingId(null)
    }
  }

  async function toggleFeature(feature: FeatureFlag) {
    setSavingId(feature.id)
    setError(null)
    try {
      await applyOverview(updateAdminFeature(feature.id, { enabled: !feature.enabled }))
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSavingId(null)
    }
  }

  async function togglePlan(plan: SubscriptionPlan) {
    setSavingId(plan.id)
    setError(null)
    try {
      await applyOverview(updateAdminPlan(plan.id, { enabled: !plan.enabled }))
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSavingId(null)
    }
  }

  async function savePlanPricing(plan: SubscriptionPlan) {
    setSavingId(plan.id)
    setError(null)
    try {
      await applyOverview(updateAdminPlan(plan.id, {
        price_cents: Math.round(Number(planDraft.price_yuan || 0) * 100),
        currency: 'CNY',
        duration_days: Number(planDraft.duration_days || 0),
        price_label: String(planDraft.price_label || ''),
        seat_limit: planDraft.seat_limit.trim() === '' ? null : Number(planDraft.seat_limit),
        bom_limit: planDraft.bom_limit.trim() === '' ? null : Number(planDraft.bom_limit),
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSavingId(null)
    }
  }

  return (
    <main className="admin-shell">
      <div className="home-topbar">
        <Link href="/" className="brand-mark">
          <span className="brand-cube" />
          <span>PEBS BOM</span>
        </Link>
        <div className="home-meta">
          <button
            className="admin-link nav-button"
            onClick={() => {
              clearAdminToken()
              window.location.href = '/bomadmin'
            }}
            type="button"
          >
            退出登录
          </button>
          <span className="user-dot">♙</span>
          <span>admin</span>
        </div>
      </div>

      <section className="admin-hero">
        <div>
          <div className="hero-kicker">✧ 多租户订阅与功能开关</div>
          <h1>后台管理</h1>
          <p>管理员可以定义个人、团队、企业三个订阅模式的可用能力，并为云服务器版多租户运营预留配置基础。</p>
        </div>
        <div className="admin-summary">
          <span>租户</span>
          <strong>{tenants.length}</strong>
          <span>订阅方案</span>
          <strong>{plans.length}</strong>
          <span>功能开关</span>
          <strong>{features.length}</strong>
        </div>
      </section>

      {error && <div className="admin-alert">{error}</div>}
      {loading ? (
        <div className="admin-loading">正在加载后台配置...</div>
      ) : (
        <section className="admin-grid">
          <aside className="admin-panel plan-list">
            <div className="admin-panel-head">
              <h2>订阅模式</h2>
              <span>个人 / 团队 / 企业</span>
            </div>
            {plans.map((plan) => (
              <button
                key={plan.id}
                className={`plan-row ${selectedPlan === plan.id ? 'active' : ''}`}
                onClick={() => setSelectedPlan(plan.id)}
                type="button"
              >
                <span>
                  <b>{plan.name}</b>
                  <small>{plan.price_label || PLAN_LABEL[plan.tenant_type] || plan.tenant_type}</small>
                </span>
                <i className={plan.enabled ? 'state-on' : 'state-off'}>
                  {plan.enabled ? '启用' : '停用'}
                </i>
              </button>
            ))}
          </aside>

          <section className="admin-panel plan-detail">
            {selected && (
              <>
                <div className="admin-panel-head">
                  <h2>{selected.name}</h2>
                  <button
                    className="switch-button"
                    disabled={savingId === selected.id}
                    onClick={() => togglePlan(selected)}
                    type="button"
                  >
                    {selected.enabled ? '停用方案' : '启用方案'}
                  </button>
                </div>
                <p className="plan-desc">{selected.description}</p>
                <div className="plan-metrics">
                  <div>
                    <span>账号席位</span>
                    <strong>{selected.seat_limit ?? '不限'}</strong>
                  </div>
                  <div>
                    <span>BOM 数量</span>
                    <strong>{selected.bom_limit ?? '不限'}</strong>
                  </div>
                  <div>
                    <span>订阅费用</span>
                    <strong>{(selected.price_cents / 100).toLocaleString('zh-CN')} 元</strong>
                  </div>
                  <div>
                    <span>有效时间</span>
                    <strong>{selected.duration_days} 天</strong>
                  </div>
                </div>
                <div className="plan-editor">
                  <label>
                    <span>费用（元）</span>
                    <input
                      className="admin-input"
                      type="number"
                      value={planDraft.price_yuan}
                      onChange={(e) => setPlanDraft((prev) => ({ ...prev, price_yuan: e.target.value }))}
                    />
                  </label>
                  <label>
                    <span>货币单位</span>
                    <input
                      className="admin-input"
                      value="人民币：元"
                      disabled
                    />
                  </label>
                  <label>
                    <span>有效天数</span>
                    <input
                      className="admin-input"
                      type="number"
                      value={planDraft.duration_days}
                      onChange={(e) => setPlanDraft((prev) => ({ ...prev, duration_days: e.target.value }))}
                    />
                  </label>
                  <label>
                    <span>费用展示</span>
                    <input
                      className="admin-input"
                      value={planDraft.price_label || ''}
                      onChange={(e) => setPlanDraft((prev) => ({ ...prev, price_label: e.target.value }))}
                    />
                  </label>
                  <button
                    className="switch-button"
                    disabled={savingId === selected.id}
                    onClick={() => savePlanPricing(selected)}
                    type="button"
                  >
                    保存订阅配置
                  </button>
                </div>
                <div className="feature-list">
                  {selectedFeatures.map((feature) => (
                    <div key={feature.id} className="feature-row">
                      <div>
                        <b>{feature.feature_name}</b>
                        <p>{feature.description}</p>
                      </div>
                      <button
                        className={`feature-toggle ${feature.enabled ? 'on' : ''}`}
                        disabled={savingId === feature.id}
                        onClick={() => toggleFeature(feature)}
                        type="button"
                        aria-label={`${feature.enabled ? '关闭' : '开启'}${feature.feature_name}`}
                      >
                        <span />
                      </button>
                    </div>
                  ))}
                </div>
              </>
            )}
          </section>

          <section className="admin-panel tenant-table">
            <div className="admin-panel-head">
              <h2>租户</h2>
              <span>当前运行实例</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>租户</th>
                  <th>类型</th>
                  <th>订阅</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {tenants.map((tenant) => (
                  <tr key={tenant.id}>
                    <td>{tenant.name}</td>
                    <td>{PLAN_LABEL[tenant.tenant_type] || tenant.tenant_type}</td>
                    <td>{plans.find((plan) => plan.id === tenant.subscription_plan_id)?.name || tenant.subscription_plan_id}</td>
                    <td>{tenant.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="admin-panel tenant-table user-table">
            <div className="admin-panel-head">
              <h2>用户信息</h2>
              <span>超级管理员可编辑</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>用户名</th>
                  <th>显示名称</th>
                  <th>角色</th>
                  <th>邮箱</th>
                  <th>电话</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => {
                  const editing = editingUserId === user.id
                  return (
                    <tr key={user.id}>
                      <td>{user.username}</td>
                      <td>
                        {editing ? (
                          <input
                            className="admin-input"
                            value={userDraft.display_name || ''}
                            onChange={(e) => setUserDraft((prev) => ({ ...prev, display_name: e.target.value }))}
                          />
                        ) : user.display_name}
                      </td>
                      <td>
                        {editing ? (
                          <select
                            className="admin-input"
                            value={userDraft.role || user.role}
                            onChange={(e) => setUserDraft((prev) => ({ ...prev, role: e.target.value }))}
                          >
                            <option value="super_admin">super_admin</option>
                            <option value="owner">owner</option>
                            <option value="admin">admin</option>
                            <option value="member">member</option>
                          </select>
                        ) : user.role}
                      </td>
                      <td>
                        {editing ? (
                          <input
                            className="admin-input"
                            value={userDraft.email || ''}
                            onChange={(e) => setUserDraft((prev) => ({ ...prev, email: e.target.value }))}
                          />
                        ) : user.email || '-'}
                      </td>
                      <td>
                        {editing ? (
                          <input
                            className="admin-input"
                            value={userDraft.phone || ''}
                            onChange={(e) => setUserDraft((prev) => ({ ...prev, phone: e.target.value }))}
                          />
                        ) : user.phone || '-'}
                      </td>
                      <td>
                        {editing ? (
                          <select
                            className="admin-input"
                            value={userDraft.status || user.status}
                            onChange={(e) => setUserDraft((prev) => ({ ...prev, status: e.target.value }))}
                          >
                            <option value="active">active</option>
                            <option value="disabled">disabled</option>
                          </select>
                        ) : user.status}
                      </td>
                      <td>
                        {editing ? (
                          <div className="admin-row-actions">
                            <input
                              className="admin-input password-input"
                              type="password"
                              value={userDraft.password || ''}
                              onChange={(e) => setUserDraft((prev) => ({ ...prev, password: e.target.value }))}
                              placeholder="新密码"
                            />
                            <button disabled={savingId === user.id} onClick={() => saveUser(user)} type="button">保存</button>
                            <button onClick={() => setEditingUserId(null)} type="button">取消</button>
                          </div>
                        ) : (
                          <button className="switch-button" onClick={() => startEditUser(user)} type="button">
                            编辑
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </section>
        </section>
      )}
    </main>
  )
}
