'use client'

import { useEffect } from 'react'
import { getAdminToken } from '@/lib/api'

export default function AuthGuard() {
  useEffect(() => {
    if (getAdminToken()) return
    window.location.replace('/guest?reason=unauth')
  }, [])

  return null
}
