import axios from 'axios'

export const http = axios.create({ baseURL: '/api' })

export type RunState = 'pending' | 'running' | 'success' | 'failed'

export interface NodeMeta {
  id: string
  step: number
  name: string
  subtitle: string
  phase: string
  desc: string
  outputs: string[]
  state: RunState
  latest: {
    id: number
    status: string
    started_at: string
    finished_at: string | null
    stats: Record<string, unknown> | null
    message: string | null
  } | null
}

export interface Health {
  status: string
  llm_provider: string
  embedding_provider: string
  graph_backend: string
}

export interface StoreStats {
  backend: string
  nodes: number
  relations: number
  by_label: Record<string, number>
  by_rel: Record<string, number>
  label_cn?: Record<string, string>
  rel_cn?: Record<string, string>
  orphan_nodes?: number
}

export interface LogLine {
  id: number
  node_id: string | null
  ts: string
  level: string
  message: string
}

export const getHealth = () => http.get<Health>('/health').then((r) => r.data)
export const getNodes = () =>
  http.get<{ running: boolean; nodes: NodeMeta[] }>('/nodes').then((r) => r.data)
export const runNode = (id: string) => http.post(`/nodes/${id}/run`, {})
export const runAll = (startFrom?: string) =>
  http.post('/pipeline/run-all', { start_from: startFrom ?? null })
export const getLogs = (after = 0, nodeId?: string) =>
  http
    .get<{ logs: LogLine[] }>('/logs', { params: { after, node_id: nodeId } })
    .then((r) => r.data.logs)
export const getStoreStats = () =>
  http.get<StoreStats>('/store/stats').then((r) => r.data)
export const resetStore = () => http.post('/store/reset', {})
