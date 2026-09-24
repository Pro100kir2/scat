export type CacheState = 'fresh' | 'stale' | 'empty'
export type ScanStage = 'idle' | 'downloading' | 'parsing' | 'testing' | 'rechecking' | 'completed' | 'failed'
export type ScanOutcome = 'cached' | 'started' | 'joined' | 'cooldown' | 'busy'

export interface ScanProgress {
  stage: ScanStage
  progress: number
  total_candidates: number
  processed_candidates: number
  working_candidates: number
  target_candidates: number
  elapsed_seconds: number | null
}

export interface CacheMeta {
  created_at: string
  expires_at: string
  scanner_version: string
  count: number
  total_nodes: number
  tested: number
  working: number
  country: string | null
  duration_seconds: number
}

export interface ScanStatus {
  cache_state: CacheState
  is_scanning: boolean
  queued?: boolean
  progress: ScanProgress | null
  last_scan_failed: boolean
  cache: CacheMeta | null
}

export interface PearlNode {
  id: string
  rank: number
  link: string
  label: string
  country: string | null
  address: string
  port: number
  network: string
  security: string
  sni: string | null
  flow: string | null
  tls_fingerprint: string | null
  latency_ms: number | null
  jitter_ms: number | null
  speed_mbps: number | null
  stable: boolean
  checks_passed: number
  checks_total: number
}

export interface ResultsResponse extends ScanStatus {
  nodes: PearlNode[]
}

export interface ScanRequestResponse extends ScanStatus {
  outcome: ScanOutcome
}
