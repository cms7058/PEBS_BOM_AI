'use client'

import { useEffect } from 'react'
import { clearAdminToken, getAdminToken, getCurrentUser } from '@/lib/api'

export default function AuthGuard() {
  useEffect(() => {
    const user = getCurrentUser()
    const token = getAdminToken()
    const expiresAt = user?.trial_expires_at ? new Date(user.trial_expires_at).getTime() : 0
    const isInternalBetaUser = Boolean(
      token
      && user
      && user.role !== 'super_admin'
      && expiresAt
      && expiresAt > Date.now(),
    )
    if (isInternalBetaUser) return
    clearAdminToken()
    window.location.replace('/guest?reason=unauth')
  }, [])

  return null
}
