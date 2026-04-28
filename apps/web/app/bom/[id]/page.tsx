import BOMWorkspace from '@/components/BOMWorkspace'
import { API_BASE } from '@/lib/api'
import type { BOM } from '@/lib/api'

export const dynamic = 'force-dynamic'

async function fetchBOM(id: string): Promise<BOM | null> {
  try {
    const res = await fetch(`${API_BASE}/boms/${id}`, { cache: 'no-store' })
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

export default async function BOMPage({ params }: { params: { id: string } }) {
  const bom = await fetchBOM(params.id)
  if (!bom) {
    return (
      <div className="container">
        <p>未找到 BOM（后端可能未启动或 ID 无效）</p>
      </div>
    )
  }
  return <BOMWorkspace bom={bom} />
}
