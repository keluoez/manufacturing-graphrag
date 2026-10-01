import { useState } from 'react'
import { http } from '../api'

type Hit = { doc_id: string; score: number; text: string }
type Ans = { id: string; name: string; label: string }
type Resp = {
  query: string
  links: { id: string; label: string; surface: string; method: string }[]
  bm25: Hit[]
  vector: Hit[]
  graph: {
    answer: Record<string, Ans[]>
    subgraph: { nodes: any[]; edges: any[] }
    triples: { source: string; target: string; rel: string }[]
  }
  hybrid_answer: Ans[]
}

// 取自 150 题金标（questions150.jsonl），保证在当前图谱中有完整因果路径
const EXAMPLES = [
  'W5智能水杯控制板产线上出现飞边，工程上应该采取什么根因对策？',
  'M9指夹血氧仪板批次超声波压裂异常怎么处理？给出可闭环的永久对策。',
  '贴片工序有哪些常见不良现象？',
  '与片式电容相关的不良现象有哪些？',
]

const REL_CN: Record<string, string> = {
  CAUSED_BY: '归因于', ADDRESSED_BY: '对策为', OCCURS_AT: '发生于',
  INDUCED_BY_MATERIAL: '物料诱发', TARGETS: '作用于',
}

function Channel({ title, tone, hits, empty }: { title: string; tone: string; hits: Hit[]; empty: string }) {
  return (
    <div className="qa-channel">
      <div className={`qa-channel-head ${tone}`}>{title}</div>
      <div className="qa-channel-body">
        {!hits.length && <div className="hint">{empty}</div>}
        {hits.map((h, i) => (
          <div className="chunk-card" key={i}>
            <div className="chunk-meta">
              <span className={`genre ${h.doc_id.split('-')[0]}`}>{h.doc_id}</span>
              <span className="score">score {h.score.toFixed(2)}</span>
            </div>
            <pre className="chunk-text">{h.text}</pre>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function QaCompare() {
  const [query, setQuery] = useState(EXAMPLES[0])
  const [r, setR] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(false)

  const ask = async () => {
    setLoading(true)
    try {
      const data = (await http.post<Resp>('/qa/ask', { query })).data
      setR(data)
    } finally {
      setLoading(false)
    }
  }

  const a = r?.graph.answer
  const nameOf = (id: string) =>
    r?.graph.subgraph.nodes.find((n) => n.id === id)?.name || id

  return (
    <div className="qa-page">
      <div className="qa-search">
        <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && ask()} />
        <button className="btn primary" disabled={loading} onClick={ask}>{loading ? '检索中…' : '三路并行检索'}</button>
      </div>
      <div className="qa-examples">
        {EXAMPLES.map((x) => <span key={x} onClick={() => setQuery(x)}>{x}</span>)}
      </div>

      {r && (
        <>
          <div className="qa-links">
            实体链接：
            {r.links.length ? r.links.map((l) => (
              <span className="link-tag" key={l.id}>{l.label}: {l.surface}
                <em>{l.method === 'alias' ? '别名表' : '语义向量'}</em>
              </span>
            )) : <span className="hint">未链接到图谱实体（纯开放语义问题）</span>}
          </div>

          {a && (a.countermeasures.length > 0 || a.causes.length > 0) && (
            <div className="qa-graph-answer">
              <div className="qa-graph-title">GraphRAG 结构化归因路径</div>
              <div className="causal-flow">
                {a.processes[0] && <span className="flow-node process">{a.processes[0].name}</span>}
                {a.processes[0] && <span className="arrow">责任工序</span>}
                {a.causes.slice(0, 3).map((c, i) => (
                  <span key={c.id}>
                    <span className="flow-node cause">{c.name.length > 26 ? c.name.slice(0, 26) + '…' : c.name}</span>
                    {i < Math.min(a.causes.length, 3) - 1 && <span className="arrow" />}
                  </span>
                ))}
                <span className="arrow">→ 根因对策 →</span>
                <span className="flow-node cm">
                  {a.countermeasures.filter((c) => c.id.startsWith('CM_')).slice(0, 1)
                    .map((c) => c.name.length > 30 ? c.name.slice(0, 30) + '…' : c.name)
                    .join('') || `${a.countermeasures.length} 条对策`}
                </span>
              </div>
              <div className="triple-list">
                {r.graph.triples.filter((t) => REL_CN[t.rel]).slice(0, 8).map((t, i) => (
                  <div className="triple" key={i}>
                    {nameOf(t.source)} <b>—{REL_CN[t.rel]}→</b> {nameOf(t.target)}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="qa-grid">
            <Channel title="① BM25 关键词检索" tone="bm" hits={r.bm25} empty="关键词路未召回" />
            <Channel title="② 向量语义检索" tone="vec" hits={r.vector} empty="语义路未召回" />
            <div className="qa-channel">
              <div className="qa-channel-head graph">③ GraphRAG 图谱检索（子图证据）</div>
              <div className="qa-channel-body">
                <div className="chunk-meta">
                  <span>子图节点 {r.graph.subgraph.nodes.length} / 边 {r.graph.subgraph.edges.length}</span>
                </div>
                {Object.entries(a || {}).filter(([, v]) => (v as Ans[]).length)
                  .map(([k, v]) => (
                    <div key={k} className="graph-answer-group">
                      <b>{k}</b>
                      <ul>{(v as Ans[]).slice(0, 6).map((x) => <li key={x.id}>{x.name}</li>)}</ul>
                    </div>
                  ))}
                {!r.graph.subgraph.nodes.length && <div className="hint">图谱未命中该问题的实体与路径</div>}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
