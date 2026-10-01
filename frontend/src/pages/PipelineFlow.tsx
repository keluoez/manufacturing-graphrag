import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  getNodes,
  getLogs,
  runNode,
  runAll,
  getStoreStats,
  type NodeMeta,
  type LogLine,
  type StoreStats,
} from '../api'

const POS: Record<string, { x: number; y: number }> = {
  schema: { x: 40, y: 30 },
  corpus: { x: 300, y: 30 },
  extract: { x: 560, y: 30 },
  align: { x: 820, y: 30 },
  ingest: { x: 1080, y: 30 },
  index: { x: 1080, y: 290 },
  retrieve: { x: 820, y: 290 },
  qa: { x: 560, y: 290 },
  evaluate: { x: 560, y: 550 },
}

const EDGE_DEF: [string, string, string, string, boolean?][] = [
  ['schema', 'corpus', 'r', 'l'],
  ['corpus', 'extract', 'r', 'l'],
  ['extract', 'align', 'r', 'l'],
  ['align', 'ingest', 'r', 'l'],
  ['ingest', 'index', 'b', 't'],
  ['ingest', 'retrieve', 'b', 't', true],
  ['index', 'retrieve', 'l', 'r'],
  ['retrieve', 'qa', 'l', 'r'],
  ['qa', 'evaluate', 'b', 't'],
]

const EDGES: Edge[] = EDGE_DEF.map(([s, t, sh, th, dashed]) => ({
  id: `${s}-${t}-${sh}${th}`,
  source: s,
  target: t,
  sourceHandle: sh,
  targetHandle: th,
  animated: !!dashed,
  label: dashed ? '子图供给' : undefined,
  style: dashed
    ? { strokeDasharray: '5 4', stroke: '#0a9f5c', strokeWidth: 1.4 }
    : { stroke: '#9aa3af', strokeWidth: 1.6 },
  labelStyle: { fontSize: 11, fill: '#0a9f5c' },
  labelBgStyle: { fill: '#ffffff' },
  labelBgPadding: [4, 2] as [number, number],
  labelBgBorderRadius: 4,
}))

type PNData = Node<{ meta: NodeMeta }>

function PipelineNode({ data, selected }: NodeProps<PNData>) {
  const { meta } = data
  return (
    <div className={`pn ${meta.state} ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Top} id="t" style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Left} id="l" style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} id="r" style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} id="b" style={{ opacity: 0 }} />
      <div className="pn-head">
        <span className="pn-step">{String(meta.step).padStart(2, '0')}</span>
        <span className="pn-name">{meta.name}</span>
        <span className={`pn-state ${meta.state}`} />
      </div>
      <div className="pn-sub">{meta.subtitle}</div>
    </div>
  )
}

const nodeTypes = { pipeline: PipelineNode }

const STATE_TEXT: Record<string, string> = {
  pending: '待运行',
  running: '运行中…',
  success: '成功',
  failed: '失败',
}

function FlowInner() {
  const [metas, setMetas] = useState<NodeMeta[]>([])
  const [running, setRunning] = useState(false)
  const [logs, setLogs] = useState<LogLine[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [stats, setStats] = useState<StoreStats | null>(null)
  const logBoxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getNodes()
      .then((d) => {
        setRunning(d.running)
        setMetas(d.nodes)
      })
      .catch(() => undefined)
    getLogs(0)
      .then(setLogs)
      .catch(() => undefined)
    const es = new EventSource('/api/events')
    es.addEventListener('state', (e) => {
      const p = JSON.parse((e as MessageEvent).data)
      setRunning(p.running)
      setMetas(p.nodes)
    })
    es.addEventListener('log', (e) => {
      const line = JSON.parse((e as MessageEvent).data) as LogLine
      setLogs((prev) => [...prev.slice(-799), line])
    })
    return () => es.close()
  }, [])

  useEffect(() => {
    const tick = () => getStoreStats().then(setStats).catch(() => undefined)
    tick()
    const t = setInterval(tick, 3000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (logBoxRef.current) logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight
  }, [logs, selectedId])

  const nodes: Node[] = useMemo(
    () =>
      metas.map((m) => ({
        id: m.id,
        type: 'pipeline',
        position: POS[m.id] ?? { x: 0, y: 0 },
        data: { meta: m },
        selected: m.id === selectedId,
        draggable: false,
      })),
    [metas, selectedId],
  )

  const selected = metas.find((m) => m.id === selectedId) ?? null
  const selectedLogs = useMemo(
    () => logs.filter((l) => l.node_id === selectedId),
    [logs, selectedId],
  )

  return (
    <div style={{ display: 'flex', height: '100%' }}>
      <div className="flow-wrap" style={{ position: 'relative' }}>
        <div
          style={{
            padding: '10px 16px',
            borderBottom: '1px solid var(--border)',
            background: 'var(--surface)',
            display: 'flex',
            gap: 10,
            alignItems: 'center',
          }}
        >
          <button className="btn primary" disabled={running} onClick={() => runAll()}>
            ▶ 运行全部 9 个节点
          </button>
          <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
            {running ? '流水线运行中，节点状态实时刷新…' : '空闲：可整体运行，或点击单个节点单独运行'}
          </span>
        </div>
        <div className="flow-canvas">
          <ReactFlow
            nodes={nodes}
            edges={EDGES}
            nodeTypes={nodeTypes}
            onNodeClick={(_, n) => setSelectedId(n.id)}
            nodesDraggable={false}
            fitView
            minZoom={0.3}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={22} color="#e3e6ea" />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <div className="flow-hint">
          {stats
            ? `图库后端：${stats.backend} · 节点 ${stats.nodes.toLocaleString()} · 关系 ${stats.relations.toLocaleString()}`
            : '图库统计加载中…'}
          {'　|　虚线 = GraphRAG 子图数据供给'}
        </div>
      </div>

      <aside className="drawer">
        <div className="drawer-head">
          {selected ? (
            <>
              <h3>
                {String(selected.step).padStart(2, '0')} · {selected.name}
              </h3>
              <div className="sub">{selected.subtitle}</div>
            </>
          ) : (
            <h3>节点详情</h3>
          )}
        </div>
        <div className="drawer-body">
          {!selected && <div className="empty-hint">点击流程图中的任一节点，查看产物说明、运行结果与实时日志。</div>}
          {selected && (
            <>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  marginBottom: 4,
                }}
              >
                <span className={`badge ${selected.state === 'failed' ? 'warn' : ''}`}>
                  <span className={`dot pn-state ${selected.state}`} style={{ position: 'static' }} />
                  {STATE_TEXT[selected.state]}
                </span>
                <button
                  className="btn primary"
                  disabled={running}
                  onClick={() => runNode(selected.id)}
                >
                  运行此节点
                </button>
              </div>

              <div className="section-title">节点说明</div>
              <div style={{ fontSize: 13 }}>{selected.desc}</div>

              <div className="section-title">预期产物</div>
              <ul>
                {selected.outputs.map((o) => (
                  <li key={o}>{o}</li>
                ))}
              </ul>

              {selected.latest?.stats && Object.keys(selected.latest.stats).length > 0 && (
                <>
                  <div className="section-title">本次运行产物统计</div>
                  <div className="stats-box">
                    {JSON.stringify(selected.latest.stats, null, 2)}
                  </div>
                </>
              )}
              {selected.latest?.message && (
                <>
                  <div className="section-title">错误信息</div>
                  <div className="stats-box" style={{ color: 'var(--danger)' }}>
                    {selected.latest.message}
                  </div>
                </>
              )}

              <div className="section-title">运行日志</div>
              <div className="log-box" ref={logBoxRef}>
                {selectedLogs.length === 0 && (
                  <span style={{ color: '#6b7482' }}>暂无日志，点击「运行此节点」开始。</span>
                )}
                {selectedLogs.map((l) => (
                  <div key={l.id}>
                    <span className="log-ts">[{l.ts.slice(11)}]</span>{' '}
                    <span className={l.level === 'warning' ? 'log-warn' : l.level === 'error' ? 'log-error' : ''}>
                      {l.message}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </aside>
    </div>
  )
}

export default function PipelineFlow() {
  return (
    <ReactFlowProvider>
      <FlowInner />
    </ReactFlowProvider>
  )
}
