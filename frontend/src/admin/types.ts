/** Shapes of the /api/admin responses the panel uses. */

export interface Paged<T> {
  items: T[]
  total: number
  page: number
  per_page: number
}

export interface ProbeRow {
  id: string
  provider_id: string
  provider: string | null
  country: string | null
  status: 'pending' | 'approved' | 'disabled'
  lifecycle: string
  online: boolean
  verification: { state: string; reason: string | null; at: number | null; attempts: number }
  profile: string | null
  platform: string | null
  version: string | null
  os: string | null
  arch: string | null
  cpu: string | null
  cpu_cores: number | null
  mem_total_kb: number | null
  mem_available_kb: number | null
  storage_free_kb: number | null
  storage_total_kb: number | null
  region: string | null
  /** Detected from the registration connection when known, else what was typed. */
  asn: number | null
  asn_detected: number | null
  as_name: string | null
  net_prefix: string | null
  asn_claimed: number | null
  network_checked_at: number | null
  network_error: string | null
  public_ip: string | null
  contact_phone: string | null
  created_at: number
  last_seen: number | null
  last_heartbeat: number | null
  last_job_at: number | null
  last_success_at: number | null
  last_activity: number | null
  jobs_done: number
  jobs_success: number
  jobs_failed: number
  jobs_error: number
  jobs_expired: number
  error_count: number
  last_error: string | null
  last_error_at: number | null
  control_ok: number | null
  is_new: boolean
}

export interface ProbeDetail extends ProbeRow {
  enrollment: { id: string | null; info: string | null; ip: string | null }
  diagnostics: { summary?: Record<string, unknown>; checks?: { name: string; ok: boolean; value: string }[] } | null
  open_jobs: number
  score_contribution: number
}

export interface ProbesPage extends Paged<ProbeRow> {
  facets: { profiles: string[]; versions: string[] }
}

export interface JobRow {
  id: string
  key: string
  type: string
  purpose: string
  status: string
  attempts: number
  created_at: number
  leased_at: number | null
  completed_at: number | null
  outcome: string | null
  error: string | null
  http_status: number | null
  latency_ms: number | null
  request_seen: number
}

export interface Verification {
  id: number
  trigger: string
  state: string
  reason: string | null
  reason_text: string | null
  started_at: number
  deadline: number
  finished_at: number | null
  detail: { checks: { purpose: string; type: string; status: string; outcome: string | null; error: string | null;
    http_status: number | null; request_seen: boolean | null; request_ip_matches: boolean | null }[] } | null
}

export interface SourceRow {
  id: number
  name: string
  url: string
  kind: 'subscription' | 'vless'
  enabled: boolean
  priority: number
  refresh_seconds: number
  timeout_seconds: number | null
  created_at: number
  updated_at: number
  last_fetch_at: number | null
  last_success_at: number | null
  last_error: string | null
  last_status: 'never' | 'ok' | 'error'
  fetched_count: number | null
  unique_count: number | null
  valid_count: number | null
  last_result: { bytes?: number; status?: number | null; duration_ms?: number; sample?: string[]; redirects?: string[]; cached?: boolean; at?: number } | null
}

export interface SourceTest {
  ok: boolean
  error: string
  fetched: number
  valid: number
  unique: number
  sample: string[]
  bytes: number
  status: number | null
  duration_ms: number
  redirects: string[]
}

export interface ScanRow {
  id: number
  query: string
  trigger: string
  status: 'running' | 'completed' | 'failed' | 'cancelled'
  started_at: number
  finished_at: number | null
  duration_seconds: number | null
  source_count: number | null
  sources_ok: number | null
  candidate_count: number | null
  unique_count: number | null
  tested_count: number | null
  passed_count: number | null
  failed_count: number | null
  error_count: number | null
  top_count: number | null
  probe_count: number | null
  probe_jobs: number | null
  error: string | null
}

export interface ScanDetail extends ScanRow {
  detail: {
    sources: { url: string; name?: string; success: boolean; vless_count: number; valid_count?: number; unique_count?: number;
      error: string; cached?: boolean }[]
    top: { fingerprint: string; address: string; port: number; latency_ms: number | null; speed_mbps: number | null;
      exit_country: string | null }[]
  } | null
}

export interface ScannerView {
  state: 'idle' | 'scanning' | 'queued'
  live: { stage: string; progress: number; total_candidates: number; processed_candidates: number;
    working_candidates: number; target_candidates: number; elapsed_seconds: number | null; query: string | null }
  queue: { query: string; state: string }[]
  last_error: string | null
  current_scan: ScanRow | null
  last_scan: ScanRow | null
  cache: { cache_state: string; cache: { created_at: string; expires_at: string; count: number } | null }
  stats: Record<string, number>
  settings: Record<string, number>
}

export interface RankingRow {
  rank: number
  in_top: boolean
  fingerprint: string
  address: string
  port: number
  label: string
  probe_score: number
  probe_total: number
  probe_providers: string[]
  backend_rank: number
  latency_ms: number | null
  speed_mbps: number | null
}

export interface CacheView {
  ttl_seconds: number
  files: { query: string; state: string; size_bytes: number | null; valid: boolean; created_ts: number | null;
    expires_ts: number | null; count?: number; scan_id: number | null; scanning: boolean; scanner_version?: string }[]
  last_scan: ScanRow | null
  use_probe_score: boolean
  top_count: number
  ranking: RankingRow[]
}

export interface EventRow {
  id: number
  ts: number
  component: string
  severity: 'info' | 'warning' | 'error'
  kind: string
  message: string
  probe_id: string | null
  source_id: number | null
  scan_id: number | null
  data: Record<string, unknown> | null
}

export interface HealthComponent {
  status: 'ok' | 'warning' | 'error'
  summary: string
  [key: string]: unknown
}

export interface HealthView {
  status: 'ok' | 'warning' | 'error'
  components: Record<string, HealthComponent>
  versions: Record<string, string | boolean>
  uptime_seconds: number
  memory: { rss_bytes: number | null; max_rss_bytes: number | null; total_bytes: number | null; available_bytes: number | null }
  last_successful_scan: ScanRow | null
  last_scan: ScanRow | null
  recent_errors: EventRow[]
}

export interface ProbeSummaryCounts {
  total: number
  approved: number
  pending: number
  disabled: number
  online: number
  online_approved: number
  verifying: number
  verification_failed: number
  new: number
  open_jobs: number
}

export interface DashboardView {
  probes: ProbeSummaryCounts
  sources: { total: number; enabled: number; healthy: number; failed: number; never_fetched: number }
  scanner: { state: string; live: ScannerView['live']; queue: ScannerView['queue']; last_error: string | null;
    last_scan: ScanRow | null; last_success: ScanRow | null }
  cache: { state: string; count: number; created_at: number | null; expires_at: number | null; candidates: number }
  errors_today: number
  warnings_today: number
  recent_events: EventRow[]
}

export interface ProviderRow {
  id: string
  name: string
  country: string
  region: string | null
  asn: number | null
  created_at: number
  probes_total: number
  probes_approved: number
  probes_pending: number
  probes_disabled: number
  probes_online: number
  jobs_success: number
  jobs_failed: number
  errors: number
  last_heartbeat: number | null
  last_activity: number | null
  score_contribution: number
  public: boolean
}

export interface SettingItem {
  key: string
  type: 'int' | 'float' | 'bool'
  category: string
  label: string
  min: number | null
  max: number | null
  value: number | boolean
  default: number | boolean
  overridden: boolean
  restart_required: boolean
}
