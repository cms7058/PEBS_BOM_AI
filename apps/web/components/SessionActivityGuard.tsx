'use client'

import { useEffect } from 'react'
import { clearAdminToken, getAdminToken } from '@/lib/api'

const IDLE_TIMEOUT_MS = 5 * 60 * 1000

export default function SessionActivityGuard() {
  useEffect(() => {
    if (!getAdminToken()) return

    let timeoutId = window.setTimeout(logout, IDLE_TIMEOUT_MS)

    function logout() {
      if (!getAdminToken()) return
      clearAdminToken()
      window.location.replace('/guest?reason=unauth')
    }

    function markActive() {
      window.clearTimeout(timeoutId)
      timeoutId = window.setTimeout(logout, IDLE_TIMEOUT_MS)
    }

    const events = [
      'keydown',
      'mousedown',
      'mousemove',
      'scroll',
      'touchstart',
      'visibilitychange',
    ]
    events.forEach((eventName) => window.addEventListener(eventName, markActive, { passive: true }))

    return () => {
      window.clearTimeout(timeoutId)
      events.forEach((eventName) => window.removeEventListener(eventName, markActive))
    }
  }, [])

  return null
}
