import './globals.css'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'PEBS BOM',
  description: '对话式 BOM 生成',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh">
      <body>{children}</body>
    </html>
  )
}
