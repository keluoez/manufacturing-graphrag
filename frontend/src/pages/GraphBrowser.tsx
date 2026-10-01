import { useEffect, useRef, useState } from 'react'
import G6 from '@antv/g6'
import { http, type StoreStats } from '../api'

const COLORS: Record<string, string> = {
  Device: '#4f8ef7',
  Process: '#12b57a',
  Material: '#f5a623',
  Defect: '#e8483d',
  Cause: '#9a63ef',
  Countermeasure: '#27c2bf',
}

type SG = { nodes: any[]; edges: any[] }

export default function GraphBrowser() {
  const [stats, setStats] = useState<StoreStats | null>(null)
  const [graph, setGraph] = useState<SG>({ nodes: [], edges: [] })
  const [query, setQuery] = useState('虚焊')
  const [selected, setSelected] = useState<any>(null)
  const [err, setErr] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<any>(null)

  useEffect(() => {
    http.get<StoreStats>('/graph/stats').then((r) => setStats(r.data)).catch(() => {})
  }, [])

  useEffect(() => {
    if (!containerRef.current) return
    graphRef.current?.destroy()
    const g = new G6.Graph({
      container: containerRef.current,
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: {
        type: 'force',
        preventOverlap: true,
        nodeStrength: -180,
        edgeStrength: 0.15,
        linkDistance: 110,
      },
      defaultNode: {
        size: 26,
        style: { lineWidth: 1.5, stroke: '#fff' },
        labelCfg: { style: { fontSize: 11, fill: '#1b2330', background: { fill: '#fff', opacity: 0.7 } } },
      },
      defaultEdge: {
        style: { stroke: '#c3cad6', endArrow: { path: G6.Arrow.triangle(6, 8), fill: '#c3cad6' } },
        labelCfg: { autoRotate: true, style: { fontSize: 10, fill: '#8a93a5', opacity: 0.85 } },
      },
      modes: { default: ['drag-canvas', 'zoom-canvas', 'drag-node'] },
    })
    g.on('node:click', (e: any) => setSelected(e.item.getModel()))
    graphRef.current = g
    const onResize = () => g.changeSize(containerRef.current!.clientWidth, containerRef.current!.clientHeight)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    const g = graphRef.current
    if (!g || !graph.nodes.length) return
    const data = {
      nodes: graph.nodes.map((n) => ({
        id: n.id,
        label: n.name && n.name.length > 18 ? n.name.slice(0, 18) + '…' : n.name,
        labelRaw: n.name,
        size: n.label === 'Defect' ? 34 : 24,
        style: { fill: COLORS[n.label] || '#999' },
      })),
      edges: graph.edges.map((e) => ({
        source: e.source,
        target: e.target,
        label: e.rel,
      })).slice(0, 220),
    }
    g.data(data)
    g.render()
  }, [graph])

  const search = async () => {
    setErr('')
    try {
      const r = (await http.get<any>('/graph/causal', { params: { name: query } })).data
      if (!r.root) {
        setErr('未找到该实体，换一个规范名/别名试试')
        return
      }
      setGraph({ nodes: r.nodes, edges: r.edges })
      setSelected(r.root)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || '查询失败')
    }
  }

  const sample = async () => {
    setErr('')
    const r = (await http.get<SG>('/graph/sample', { params: { limit: 60 } })).data
    setGraph(r)
  }

  return (
    <div className="graph-page">
      <div className="graph-side">
        <h3>归因子图浏览</h3>
        <p className="sub">输入不良现象（或设备/工序/物料名），展示「现象 → 原因 → 对策 → 工序」2 跳子图。</p>
        <div className="search-row">
          <input value={query} onChange={(e) => setQuery(e.target.value)}
                 onKeyDown={(e) => e.key === 'Enter' && search()} placeholder="如：虚焊 / 连锡 / 注塑缺胶" />
          <button className="btn primary" onClick={search}>查询</button>
        </div>
        <button className="btn" style={{ width: '100%' }} onClick={sample}>随机采样子图</button>
        {err && <div className="hint warn">{err}</div>}

        {stats && (
          <div className="stat-panel">
            <div className="stat-title">全图规模（{stats.backend}）</div>
            <div className="kv"><span>节点</span><b>{stats.nodes.toLocaleString()}</b></div>
            <div className="kv"><span>关系</span><b>{stats.relations.toLocaleString()}</b></div>
            <div className="stat-title" style={{ marginTop: 10 }}>节点分布</div>
            {Object.entries(stats.by_label).map(([k, v]) => (
              <div className="kv" key={k}>
                <span><i className="dot" style={{ background: COLORS[k] }} />{stats.label_cn?.[k] || k}</span>
                <b>{v}</b>
              </div>
            ))}
          </div>
        )}

        {selected && (
          <div className="entity-card">
            <div className="stat-title">实体详情</div>
            <div className="e-name" style={{ color: COLORS[selected.label] }}>{selected.labelRaw || selected.name}</div>
            <div className="kv"><span>类型</span><b>{stats?.label_cn?.[selected.label] || selected.label}</b></div>
            <div className="kv"><span>ID</span><b style={{ fontSize: 11 }}>{selected.id}</b></div>
          </div>
        )}

        <div className="legend">
          {Object.entries(COLORS).map(([k, c]) => (
            <span key={k}><i style={{ background: c }} />{stats?.label_cn?.[k] || k}</span>
          ))}
        </div>
      </div>

      <div className="graph-canvas-wrap">
        <div ref={containerRef} className="g6-canvas" />
        {!graph.nodes.length && (
          <div className="empty-hint">输入一个不良现象开始探索，或点击「随机采样子图」</div>
        )}
      </div>
    </div>
  )
}
