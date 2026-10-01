import type { PearlNode, ResultsResponse, ScanStatus } from '@scat/shared'

export const LINK = 'vless://11111111-2222-3333-4444-555555555555@n1.example:443?type=tcp&security=reality&sni=www.microsoft.com&pbk=Yx0l%2Bk&sid=6ba8#%F0%9F%87%A9%F0%9F%87%AA%20Frankfurt'

export const node = (rank: number, over: Partial<PearlNode> = {}): PearlNode => ({
  id: `fp${rank}`, rank, link: rank === 1 ? LINK : LINK.replace('n1.', `n${rank}.`), label: 'Frankfurt', country: 'DE',
  address: `n${rank}.example`, port: 443, network: 'tcp', security: 'reality', sni: 'www.microsoft.com', flow: null,
  tls_fingerprint: 'chrome', latency_ms: 200 + rank, jitter_ms: 4, speed_mbps: 30, stable: true, checks_passed: 5,
  checks_total: 5, probe_score: 3, probe_total: 4,
  reachability: [
    { provider_id: 'p1', provider: 'МТС', state: 'SUCCESS', latency_ms: 38, checked_at: 1, probes: 1 },
    { provider_id: 'p2', provider: 'Билайн', state: 'TIMEOUT', latency_ms: null, checked_at: 1, probes: 1 },
  ],
  ...over,
})

export const cache = { created_at: '2026-10-01T10:00:00Z', expires_at: '2099-01-01T00:00:00Z', scanner_version: '3',
  count: 5, total_nodes: 9000, tested: 400, working: 25, country: null, duration_seconds: 30 }

export const status = (over: Partial<ScanStatus> = {}): ScanStatus =>
  ({ cache_state: 'fresh', is_scanning: false, progress: null, last_scan_failed: false, cache, ...over })

export const results = (count: number, over: Partial<ScanStatus> = {}): ResultsResponse =>
  ({ ...status(over), nodes: Array.from({ length: count }, (_, i) => node(i + 1)) })
