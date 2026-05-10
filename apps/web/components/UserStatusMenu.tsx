'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { clearAdminToken, getAdminToken, getCurrentUser } from '@/lib/api'

export default function UserStatusMenu() {
  const [open, setOpen] = useState(false)
  const [label, setLabel] = useState('未登录')
  const [loggedIn, setLoggedIn] = useState(false)

  useEffect(() => {
    const user = getCurrentUser()
    const expiresAt = user?.trial_expires_at ? new Date(user.trial_expires_at).getTime() : 0
    const hasBetaSession = Boolean(
      getAdminToken()
      && user
      && user.role !== 'super_admin'
      && expiresAt
      && expiresAt > Date.now(),
    )
    setLoggedIn(hasBetaSession)
    setLabel(hasBetaSession ? (user?.display_name || user?.username || '已登录') : '未登录')
  }, [])

  return (
    <div className="user-status">
      <button
        className="workspace-user user-status-trigger"
        onClick={() => setOpen((v) => !v)}
        type="button"
      >
        <span className="user-dot">♙</span>
        <span>{label}</span>
      </button>
      {open && (
        <div className="user-status-menu">
          {loggedIn ? (
            <>
              <Link href="/">进入工作台</Link>
              <button
                onClick={() => {
                  clearAdminToken()
                  window.location.href = '/guest'
                }}
                type="button"
              >
                退出登录
              </button>
            </>
          ) : (
            <>
              <Link href="/login">内测登录</Link>
            </>
          )}
        </div>
      )}
    </div>
  )
}
