import { useEffect, useState } from 'react'
import GraphBrowser from './pages/GraphBrowser'
import QaCompare from './pages/QaCompare'
import EvalDashboard from './pages/EvalDashboard'
import PipelineFlow from './pages/PipelineFlow'
import { getHealth, getNodes, type Health, type NodeMeta } from './api'

const PAGES = [
  { id: 'pipeline', idx: '01', name: '流水线编排' },
  { id: 'graph', idx: '02', name: '图谱浏览' },
  { id: 'qa', idx: '03', name: '问答对比' },
  { id: 'eval', idx: '04', name: '评测看板' },
]

export default function App() {
  const [page, setPage] = useState('pipeline')
  const [health, setHealth] = useState<Health | null>(null)
  const [nodes, setNodes] = useState<NodeMeta[]>([])

  const tick = () => {
    getHealth().then(setHealth).catch(() => setHealth(null))
    getNodes().then((d) => setNodes(d.nodes))
  }

  useEffect(() => {
    tick()
    const t = setInterval(tick, 5000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="logo">
          制造工艺质量
          <br />
          知识图谱平台
          <small>KG + GraphRAG · 多跳归因问答</small>
        </div>
        <nav className="nav-item">
          {PAGES.map((p) => (
            <button key={p.id} className={page === p.id ? 'active' : ''} onClick={() => setPage(p.id)}>
              <span className="idx">{p.idx}</span>
              {p.name}
            </button>
          ))}
        </nav>
        <div className="foot">
          <span className={`badge ${health ? '' : 'warn'}`}>
            <span className="dot" />
            LLM：{health?.llm_provider ?? '未连接'}
          </span>
          <span className={`badge ${health?.graph_backend === 'neo4j' ? '' : 'warn'}`}>
            <span className="dot" />
            图库：{health?.graph_backend ?? '未连接'}
          </span>
          <span className="badge">
            <span className="dot" />
            Embedding：{health?.embedding_provider ?? '—'}
          </span>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <h1>{PAGES.find((p) => p.id === page)?.name}</h1>
          <div className="spacer" />
          {nodes.some((m) => m.state === 'running') && (
            <span className="badge warn pulse"><span className="dot" />流水线运行中…</span>
          )}
        </header>
        <div className="content">
          {page === 'pipeline' && <PipelineFlow />}
          {page === 'graph' && <GraphBrowser />}
          {page === 'qa' && <QaCompare />}
          {page === 'eval' && <EvalDashboard />}
        </div>
      </div>
    </div>
  )
}
