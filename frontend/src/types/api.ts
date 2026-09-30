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
  /** Provider reachability of this config's endpoint; empty until providers have probes. */
  reachability?: Reachability[]
}

export interface ResultsResponse extends ScanStatus {
  nodes: PearlNode[]
}

export interface ScanRequestResponse extends ScanStatus {
  outcome: ScanOutcome
}

export interface RefreshResponse extends ScanRequestResponse {
  /** Seconds until anyone may force another refresh. */
  retry_after: number
}

// --- provider probes ----------------------------------------------------------------

/** Per config and provider. Never conflated: each has its own look and wording. */
export type ReachState = 'SUCCESS' | 'FAILED' | 'TIMEOUT' | 'CHECKING' | 'NO_PROBE' | 'OFFLINE' | 'NOT_TESTED'

export interface Reachability {
  provider_id: string
  provider: string
  state: ReachState
  latency_ms: number | null
  checked_at: number | null
  probes: number
}

export interface ProviderSummary {
  id: string
  name: string
  country: string
  region: string | null
  asn: number | null
  regions: string[]
  asns: number[]
  probes_total: number
  probes_online: number
  status: 'online' | 'offline' | 'no_probe'
  last_heartbeat: number | null
}

export interface ProbeSummary {
  id: string
  provider: string | null
  status: 'pending' | 'approved' | 'disabled'
  online: boolean
  last_seen: number | null
  region: string | null
  asn: number | null
  platform: string | null
  version: string | null
}

export interface ProvidersResponse {
  providers: ProviderSummary[]
  probes: ProbeSummary[]
  offline_after_seconds: number
}

export interface ProviderForm {
  name: string
  country: string
  region: string
  asn: string
  info: string
}

export interface EnrollmentCreated {
  enrollment_id: string
  token: string
  expires_at: number
  provider: { id: string; name: string; country: string; region: string | null; asn: number | null }
  install: { script_url: string; curl: string; wget: string; diagnose_only: string; releases_url: string }
}

export interface DiagnosticCheck {
  name: string
  ok: boolean
  value: string
  required: string
}

export interface DiagnosticIssue {
  code: string
  message: string
}

export interface DiagnosticsResult {
  verdict: 'SUPPORTED' | 'UNSUPPORTED'
  profile: string | null
  profile_name: string | null
  service: string | null
  reasons: DiagnosticIssue[]
  warnings: DiagnosticIssue[]
  alternatives: DiagnosticIssue[]
  checks: DiagnosticCheck[]
  summary: Record<string, string | number | boolean | null>
}

export type EnrollmentState = 'waiting' | 'installing' | 'unsupported' | 'registered' | 'online' | 'offline'

export interface EnrollmentStatus {
  enrollment_id: string
  state: EnrollmentState
  expires_at: number
  diagnostics: DiagnosticsResult | null
  probe: ProbeSummary | null
}

export interface ProbeRelease {
  profile: string
  name: string
  sha256: string
  size: number
  url: string
}

export interface ReleasesResponse {
  version: string
  releases: ProbeRelease[]
}
