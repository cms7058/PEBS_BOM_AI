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
    <main className="home-shell">
      <div className="home-topbar">
        <div className="brand-mark">
          <span className="brand-cube" />
          <span>PEBS BOM</span>
        </div>
        <div className="home-meta">
          <span className="user-dot">♙</span>
          <span>anonymous</span>
        </div>
      </div>

      <section className="home-hero">
        <div className="hero-copy">
          <div className="hero-kicker">✧ AI 驱动的 BOM 智能生成与管理平台</div>
          <h1>
            <span>智能解析</span>
            <b> · 自动建模 · 高效协同</b>
          </h1>
          <p>
            融合 AI 能力与工程数据，支持多种数据源智能解析，
            自动生成结构化 BOM，赋能产品研发与制造全流程。
          </p>
          <div className="hero-tags">
            <span>▣ 多源数据解析</span>
            <span>✧ AI 智能生成</span>
            <span>♙ 结构化管理</span>
            <span>⟳ 协同与追溯</span>
          </div>
        </div>

        <div className="hero-visual" aria-hidden="true">
          <div className="orbit orbit-a" />
          <div className="orbit orbit-b" />
          <div className="glass-card card-a" />
          <div className="glass-card card-b" />
          <div className="glass-card card-c" />
          <div className="core-cube" />
          <div className="platform-ring" />
        </div>
      </section>

      <section className="home-actions">
        <article className="home-card upload-card">
          <div className="card-icon blue">▤</div>
          <div>
            <h2>上传表格生成 BOM</h2>
            <p>支持 .xlsx / .xls / .csv</p>
            <p>上传后由 AI 自动映射字段并推断层级。</p>
          </div>
          <Uploader mode="spreadsheet" />
        </article>

        <article className="home-card cad-card">
          <div className="card-icon violet">⬡</div>
          <div>
            <h2>上传 3D 文件生成 BOM</h2>
            <p>支持 .step / .stp / .iges / .igs</p>
            <p>仅解析装配树，不渲染几何，避免大零件卡顿。</p>
          </div>
          <Uploader mode="cad" />
        </article>

        <article className="home-card history-card">
          <div className="card-icon teal">◴</div>
          <div>
            <h2>历史 BOM</h2>
            <p>快速访问最近生成的 BOM，支持预览、复用与版本管理。</p>
          </div>
          {boms.length === 0 ? (
            <p className="empty-history">暂无数据</p>
          ) : (
            <ul className="history-list">
              {boms.slice(0, 6).map((b) => (
                <li key={b.id}>
                  <Link href={`/bom/${b.id}`}>{b.name}</Link>
                  <span>{b.node_count} 节点</span>
                </li>
              ))}
            </ul>
          )}
          <Link className="history-all" href="/">查看全部历史</Link>
        </article>
      </section>

      <section className="home-stats">
        <div>
          <span className="stat-icon">▰</span>
          <strong>20+</strong>
          <small>支持数据格式</small>
        </div>
        <div>
          <span className="stat-icon">AI</span>
          <strong>98.6%</strong>
          <small>解析准确率</small>
        </div>
        <div>
          <span className="stat-icon">↯</span>
          <strong>10x</strong>
          <small>建模效率提升</small>
        </div>
        <div>
          <span className="stat-icon">◆</span>
          <strong>100%</strong>
          <small>数据安全保障</small>
        </div>
      </section>

      <footer className="home-footer">© 2024 PEBS BOM. All rights reserved.</footer>
    </main>
  )
}
