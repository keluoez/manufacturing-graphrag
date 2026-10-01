import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { http } from '../api'

type Summary = {
  extraction_sample300: {
    entity: { precision: number; recall: number; f1: number }
    relation: { precision: number; recall: number; f1: number }
  }
  retrieval_detail: Record<string, Record<string, { hit_at_3: number; mrr: number; coverage_at_3: number; n: number }>>
  end_to_end_accuracy: Record<string, Record<string, { accuracy: number; n: number; exact?: number }>>
  ablation_graph_channel: Record<string, Record<string, { hit_at_3: number; accuracy: number }>>
}
type RowKey = 'bm25_hit3' | 'vector_hit3' | 'graph_hit3' | 'graph_correct' | 'hybrid_correct'
type Row = {
  qid: string; qtype: string; intent: string; query: string; gold_number: string
  bm25_hit3: string; vector_hit3: string; graph_hit3: string
  graph_correct: string; hybrid_correct: string
} & Record<RowKey, string>

const QT_CN = { single: '单跳查询', multi: '多跳归因', agg: '统计聚合' }

function Chart({ option, height = 320 }: { option: any; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const c = echarts.init(ref.current)
    c.setOption(option)
    const r = () => c.resize()
    window.addEventListener('resize', r)
    return () => { window.removeEventListener('resize', r); c.dispose() }
  }, [option])
  return <div ref={ref} style={{ width: '100%', height }} />
}

export default function EvalDashboard() {
  const [s, setS] = useState<Summary | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    http.get<Summary>('/eval/summary').then((r) => setS(r.data)).catch(() => {})
    http.get<{ items: Row[] }>('/eval/per-question').then((r) => setRows(r.data.items)).catch(() => {})
  }, [])

  if (!s) return <div className="page-placeholder"><div className="big">等待评测产物…</div><p>请先在编排页运行节点 9「评测与消融」</p></div>

  const qts = ['single', 'multi', 'agg']
  const channels = ['bm25', 'vector', 'graph']
  const chName: Record<string, string> = { bm25: 'BM25', vector: '向量RAG', graph: 'GraphRAG' }

  const hitOption = {
    tooltip: { trigger: 'axis' },
    legend: { data: channels.map((c) => chName[c]), top: 0 },
    grid: { left: 40, right: 16, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: qts.map((q) => QT_CN[q as keyof typeof QT_CN]) },
    yAxis: { type: 'value', min: 0, max: 1, axisLabel: { formatter: '{value}' } },
    series: channels.map((c, i) => ({
      name: chName[c], type: 'bar', barMaxWidth: 36,
      itemStyle: { color: ['#8a93a5', '#2b6cb0', '#0a9f5c'][i], borderRadius: [4, 4, 0, 0] },
      label: { show: true, position: 'top', formatter: (p: any) => (p.value * 100).toFixed(0) + '%' },
      data: qts.map((q) => s.retrieval_detail[q][c].hit_at_3),
    })),
  }

  const f1Option = {
    tooltip: {},
    grid: { left: 90, right: 30, top: 20, bottom: 30 },
    xAxis: { type: 'value', min: 0, max: 1 },
    yAxis: { type: 'category', data: ['实体抽取', '关系抽取'] },
    series: [{
      type: 'bar', barMaxWidth: 26,
      itemStyle: { color: '#6a3ec9', borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', formatter: (p: any) => (p.value * 100).toFixed(1) + '%' },
      data: [
        s.extraction_sample300.entity.f1,
        s.extraction_sample300.relation.f1,
      ],
    }],
  }

  const e2eOption = {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, data: ['BM25', '向量RAG', 'GraphRAG', '双路融合'] },
    grid: { left: 40, right: 16, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: qts.map((q) => QT_CN[q as keyof typeof QT_CN]) },
    yAxis: { type: 'value', min: 0, max: 1 },
    series: ['bm25', 'vector', 'graph', 'hybrid'].map((c, i) => ({
      name: { bm25: 'BM25', vector: '向量RAG', graph: 'GraphRAG', hybrid: '双路融合' }[c]!,
      type: c === 'hybrid' ? 'line' : 'bar',
      barMaxWidth: 26,
      itemStyle: { color: ['#8a93a5', '#2b6cb0', '#0a9f5c', '#e8a12e'][i] },
      lineStyle: { width: 3 }, symbolSize: 9,
      label: { show: c === 'hybrid', formatter: (p: any) => (p.value * 100).toFixed(0) + '%' },
      data: qts.map((q) => s.end_to_end_accuracy[q][c].accuracy),
    })),
  }

  const abl = s.ablation_graph_channel
  const ablOption = {
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    grid: { left: 40, right: 16, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: ['多跳归因', '统计聚合'] },
    yAxis: { type: 'value', min: 0, max: 1 },
    series: [
      { name: '完整 GraphRAG', type: 'bar', barMaxWidth: 40, itemStyle: { color: '#0a9f5c', borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', formatter: (p: any) => (p.value * 100).toFixed(0) + '%' },
        data: [abl.multi_hop.graph_on_hybrid.hit_at_3, abl.aggregation.graph_on_hybrid.hit_at_3] },
      { name: '关闭图谱（仅向量）', type: 'bar', barMaxWidth: 40, itemStyle: { color: '#d84848', borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', formatter: (p: any) => (p.value * 100).toFixed(0) + '%' },
        data: [abl.multi_hop.graph_off_vector_only.hit_at_3, abl.aggregation.graph_off_vector_only.hit_at_3] },
    ],
  }

  const filtered = filter === 'all' ? rows : rows.filter((r) => r.qtype === filter)

  return (
    <div className="eval-page">
      <div className="metric-strip">
        <div className="ms-card">
          <div className="ms-v">{(s.extraction_sample300.entity.f1 * 100).toFixed(1)}%</div>
          <div className="ms-l">实体抽取 F1</div>
        </div>
        <div className="ms-card">
          <div className="ms-v">{(s.extraction_sample300.relation.f1 * 100).toFixed(1)}%</div>
          <div className="ms-l">关系抽取 F1</div>
        </div>
        <div className="ms-card">
          <div className="ms-v">{(s.retrieval_detail.multi.graph.hit_at_3 * 100).toFixed(0)}%</div>
          <div className="ms-l">多跳 GraphRAG Hit@3</div>
        </div>
        <div className="ms-card">
          <div className="ms-v">{(s.retrieval_detail.multi.bm25.hit_at_3 * 100).toFixed(0)}%</div>
          <div className="ms-l">多跳 BM25 Hit@3</div>
        </div>
        <div className="ms-card">
          <div className="ms-v">{(s.end_to_end_accuracy.multi.graph.accuracy * 100).toFixed(0)}%</div>
          <div className="ms-l">多跳端到端准确率（列表题）</div>
        </div>
        <div className="ms-card">
          <div className="ms-v">{((s.end_to_end_accuracy.agg_number?.graph?.accuracy ?? 0) * 100).toFixed(0)}%</div>
          <div className="ms-l">数值题图谱准确率（n={s.end_to_end_accuracy.agg_number?.graph?.n ?? 15}，单列）</div>
        </div>
      </div>

      <div className="chart-row">
        <div className="chart-card span2">
          <h4>检索 Hit@3：三路 × 三题型（聚合为 35 道列表题，数值题单列；top10 预算，静态排序）</h4>
          <Chart option={hitOption} />
        </div>
        <div className="chart-card">
          <h4>抽取层 F1（300 条抽检）</h4>
          <Chart option={f1Option} />
        </div>
      </div>
      <div className="chart-row">
        <div className="chart-card span2">
          <h4>端到端答案准确率（列表题覆盖≥70%且精确≥50%；agg n=35，数值题见上方卡片）</h4>
          <Chart option={e2eOption} />
        </div>
        <div className="chart-card">
          <h4>消融实验：图谱通道不可替代性</h4>
          <Chart option={ablOption} />
        </div>
      </div>

      <div className="chart-card">
        <h4 style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          逐题判分明细（{filtered.length}）
          <span>
            {['all', 'single', 'multi', 'agg'].map((f) => (
              <button key={f} className={`btn ${filter === f ? 'primary' : ''}`} style={{ padding: '2px 10px', marginLeft: 6 }}
                      onClick={() => setFilter(f)}>
                {f === 'all' ? '全部' : QT_CN[f as keyof typeof QT_CN]}
              </button>
            ))}
          </span>
        </h4>
        <div className="table-wrap">
          <table className="eval-table">
            <thead>
              <tr>
                <th>ID</th><th>类型</th><th>问题</th>
                <th>BM25 Hit</th><th>向量 Hit</th><th>图谱 Hit</th>
                <th>图谱正确</th><th>融合正确</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.qid}>
                  <td>{r.qid}</td>
                  <td>{QT_CN[r.qtype as keyof typeof QT_CN]}</td>
                  <td className="q-cell">{r.query}</td>
                  {(['bm25_hit3', 'vector_hit3', 'graph_hit3', 'graph_correct', 'hybrid_correct'] as RowKey[]).map((k) => (
                    <td key={k} className={r[k] === '1' ? 'good' : 'bad'}>{r[k] === '1' ? '✓' : '—'}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
