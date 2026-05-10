import './globals.css'
import type { Metadata } from 'next'
import AppDialogProvider from '@/components/AppDialog'
import SessionActivityGuard from '@/components/SessionActivityGuard'

export const metadata: Metadata = {
  title: 'PEBS BOM',
  description: '对话式 BOM 生成',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body>
        <SessionActivityGuard />
        <AppDialogProvider>{children}</AppDialogProvider>
      </body>
    </html>
  )
}
