'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { FormEvent, useEffect, useMemo, useState } from 'react'
import type {
  FeatureFlag,
  PaymentOrder,
  SubscriptionPlan,
} from '@/lib/api'
import {
  adminLogin,
  confirmPaymentOrder,
  createPaymentOrder,
  listSubscriptionPlans,
  listPublicFeatures,
  registerTenantUser,
  sendEmailCode,
} from '@/lib/api'

const PLAN_NAME: Record<string, string> = {
  personal: '个人版',
  team: '团队版',
  enterprise: '企业版',
}

export default function LoginForm({ forcedPlan }: { forcedPlan?: string } = {}) {
  const router = useRouter()
  const searchParams = useSearchParams()
  const planId = forcedPlan || searchParams.get('plan') || 'personal'
  const modeParam = searchParams.get('mode')
  const planName = PLAN_NAME[planId] || '个人版'
  const initialMode = planId === 'admin' || modeParam === 'login' ? 'login' : 'register'
  const [mode, setMode] = useState<'login' | 'register'>(initialMode)
  const [username, setUsername] = useState(planId === 'admin' ? 'admin' : '')
  const [password, setPassword] = useState(planId === 'admin' ? 'admin123456' : '')
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [emailCode, setEmailCode] = useState('')
  const [devCode, setDevCode] = useState('')
  const [plans, setPlans] = useState<SubscriptionPlan[]>([])
  const [features, setFeatures] = useState<FeatureFlag[]>([])
  const [selectedPlanId, setSelectedPlanId] = useState(planId === 'admin' ? 'personal' : planId)
  const [paymentOrder, setPaymentOrder] = useState<PaymentOrder | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    listSubscriptionPlans(controller.signal)
      .then((items) => {
        setPlans(items)
        if (!items.some((plan) => plan.id === selectedPlanId) && items[0]) {
          setSelectedPlanId(items[0].id)
        }
      })
      .catch(() => undefined)
    listPublicFeatures(controller.signal)
      .then(setFeatures)
      .catch(() => undefined)
    return () => controller.abort()
  }, [selectedPlanId])

  const selectedPlan = useMemo(
    () => plans.find((plan) => plan.id === selectedPlanId),
    [plans, selectedPlanId],
  )

  const currentPlanName = selectedPlan?.name || PLAN_NAME[selectedPlanId] || planName
  const enabledFeatures = useMemo(
    () => features.filter((feature) => feature.plan_id === selectedPlanId && feature.enabled),
    [features, selectedPlanId],
  )

  const title = useMemo(() => {
    if (mode === 'login') return '登录 PEBS BOM'
    return `开通 ${currentPlanName}`
  }, [mode, currentPlanName])

  function money(plan?: SubscriptionPlan): string {
    if (!plan) return '-'
    const amount = (plan.price_cents / 100).toLocaleString('zh-CN')
    return plan.currency === 'CNY' ? `${amount} 元` : `${plan.currency} ${amount}`
  }

  async function requestCode() {
    setLoading(true)
    setError(null)
    setNotice(null)
    try {
      const data = await sendEmailCode(email)
      setDevCode(data.dev_code || '')
      setNotice(data.dev_code ? `验证码：${data.dev_code}` : data.message)
    } catch (err) {
      setError(err instanceof Error ? err.message : '验证码发送失败')
    } finally {
      setLoading(false)
    }
  }

  async function pay() {
    setLoading(true)
    setError(null)
    setNotice(null)
    try {
      const order = await createPaymentOrder(selectedPlanId, email)
      const paid = await confirmPaymentOrder(order.id)
      setPaymentOrder(paid)
      setNotice('支付已完成，可以继续注册')
    } catch (err) {
      setError(err instanceof Error ? err.message : '支付失败')
    } finally {
      setLoading(false)
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const result = mode === 'login'
        ? await adminLogin(username, password)
        : await registerTenantUser(
          selectedPlanId,
          username,
          password,
          displayName,
          email,
          emailCode,
          paymentOrder?.id,
        )
      if (result.user.role === 'super_admin') {
        router.push('/admin')
      } else {
        router.push('/')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
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
          <span>{mode === 'login' ? 'login' : currentPlanName}</span>
        </div>
      </div>

      <section className="auth-stage">
        <div className="auth-copy">
          <div className="hero-kicker">✧ 多租户账号体系</div>
          <h1>{title}</h1>
          {mode === 'register' ? (
            <div className="auth-feature-copy">
              <p>{selectedPlan?.description || `${currentPlanName}可用功能如下，功能开关由后台管理配置。`}</p>
              <ul>
                {enabledFeatures.length === 0 ? (
                  <li>暂无已启用功能，请联系管理员配置订阅权限。</li>
                ) : enabledFeatures.map((feature) => (
                  <li key={feature.id}>
                    <b>{feature.feature_name}</b>
                    <span>{feature.description}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p>超级管理员登录后可进入后台管理订阅、用户和功能权限。</p>
          )}
        </div>

        <form className="auth-card" onSubmit={submit}>
          <div className="auth-tabs">
            <button
              className={mode === 'register' ? 'active' : ''}
              onClick={() => setMode('register')}
              type="button"
            >
              注册
            </button>
            <button
              className={mode === 'login' ? 'active' : ''}
              onClick={() => setMode('login')}
              type="button"
            >
              登录
            </button>
          </div>
          {mode === 'register' && (
            <>
              <label>
                <span>订阅模式</span>
                <select
                  className="auth-select"
                  value={selectedPlanId}
                  onChange={(e) => {
                    setSelectedPlanId(e.target.value)
                    setPaymentOrder(null)
                  }}
                >
                  {plans.length === 0 ? (
                    <option value={selectedPlanId}>{currentPlanName}</option>
                  ) : plans.map((plan) => (
                    <option key={plan.id} value={plan.id}>
                      {plan.name} · {money(plan)} · {plan.duration_days} 天
                    </option>
                  ))}
                </select>
              </label>
              <div className="subscription-box">
                <div>
                  <span>订阅费用</span>
                  <strong>{money(selectedPlan)}</strong>
                </div>
                <div>
                  <span>有效时间</span>
                  <strong>{selectedPlan?.duration_days || 365} 天</strong>
                </div>
                <div>
                  <span>支付状态</span>
                  <strong>{paymentOrder?.status === 'paid' ? '已支付' : '未支付'}</strong>
                </div>
              </div>
              <label>
                <span>邮箱</span>
                <input value={email} onChange={(e) => {
                  setEmail(e.target.value)
                  setPaymentOrder(null)
                  setDevCode('')
                }} placeholder="用于接收验证码和订阅通知" />
              </label>
              <div className="auth-inline">
                <label>
                  <span>邮箱验证码</span>
                  <input value={emailCode} onChange={(e) => setEmailCode(e.target.value)} placeholder="请输入验证码" />
                </label>
                <button disabled={loading || !email} onClick={requestCode} type="button">
                  获取验证码
                </button>
              </div>
              {devCode && (
                <button className="auth-helper" onClick={() => setEmailCode(devCode)} type="button">
                  填入开发验证码 {devCode}
                </button>
              )}
              <button
                className={`payment-button ${paymentOrder?.status === 'paid' ? 'paid' : ''}`}
                disabled={loading || !email || paymentOrder?.status === 'paid'}
                onClick={pay}
                type="button"
              >
                {paymentOrder?.status === 'paid' ? '支付完成' : `支付订阅费用 ${money(selectedPlan)}`}
              </button>
              <label>
                <span>显示名称</span>
                <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="请输入姓名或团队名" />
              </label>
            </>
          )}
          <label>
            <span>用户名</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="请输入用户名" />
          </label>
          <label>
            <span>密码</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="请输入密码" />
          </label>
          {error && <p className="auth-error">{error}</p>}
          {notice && <p className="auth-notice">{notice}</p>}
          <button className="btn btn-primary auth-submit" disabled={loading} type="submit">
            {loading ? '处理中...' : mode === 'login' ? '登录' : '注册并进入'}
          </button>
        </form>
      </section>
    </main>
  )
}
