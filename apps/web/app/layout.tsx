import './globals.css'
import type { Metadata } from 'next'
import AppDialogProvider from '@/components/AppDialog'

export const metadata: Metadata = {
  title: 'PEBS BOM',
  description: '对话式 BOM 生成',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body>
        <AppDialogProvider>{children}</AppDialogProvider>
      </body>
    </html>
  )
}
