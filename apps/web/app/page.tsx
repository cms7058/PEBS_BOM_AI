import Link from 'next/link'
import Uploader from '@/components/Uploader'
import { API_BASE } from '@/lib/api'

export const dynamic = 'force-dynamic'

async function fetchList() {
  try {
    const res = await fetch(`${API_BASE}/boms`, { cache: 'no-store' })
    if (!res.ok) return []
    return (await res.json()) as { id: string; name: string; node_count: number }[]
  } catch {
    return []
  }
}

export default async function HomePage() {
  const boms = await fetchList()
  return (
    <>
      <div className="topbar">
        <h1>PEBS BOM</h1>
        <span style={{ color: '#8b949e', fontSize: 13 }}>P0 · MiniMax M2.7</span>
      </div>
      <div className="container">
        <div className="card">
          <h2 style={{ marginTop: 0 }}>上传表格生成 BOM</h2>
          <p style={{ color: '#6b7280' }}>
            支持 .xlsx / .xls / .csv，上传后由 MiniMax M2.7 自动映射字段并推断层级。
          </p>
          <Uploader mode="spreadsheet" />
        </div>

        <div className="card">
          <h2 style={{ marginTop: 0 }}>上传 3D 文件生成 BOM</h2>
          <p style={{ color: '#6b7280' }}>
            支持 .step / .stp / .iges / .igs，仅解析装配树
            （STEP: PRODUCT/NAUO；IGES: Subfigure 308/408），不渲染几何，避免大零件卡顿。
          </p>
          <Uploader mode="cad" />
        </div>

        <div className="card">
          <h3 style={{ marginTop: 0 }}>历史 BOM</h3>
          {boms.length === 0 && <p style={{ color: '#8b949e' }}>暂无数据</p>}
          <ul style={{ paddingLeft: 18 }}>
            {boms.map((b) => (
              <li key={b.id} style={{ marginBottom: 6 }}>
                <Link href={`/bom/${b.id}`}>{b.name}</Link>
                <span style={{ color: '#8b949e', marginLeft: 8 }}>({b.node_count} 节点)</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  )
}
