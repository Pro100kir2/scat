import { ApiError } from '@scat/shared'
import { describeFailure } from '@/lib/diagnostics'

describe('describeFailure', () => {
  it('names the failure type, host, endpoint and status', () => {
    const line = describeFailure('results', new ApiError('server', 'HTTP 502 for /api/results', { status: 502 }))
    expect(line).toContain('server status=502')
    expect(line).toContain('host=api.scat-search.ru')
    expect(line).toContain('/api/results')
  })

  it('tells a network failure from a timeout and a malformed answer', () => {
    expect(describeFailure('x', new ApiError('network', 'Network error: /api/results: TypeError'))).toContain('network host=')
    expect(describeFailure('x', new ApiError('timeout', 'Timed out: /api/results'))).toContain('timeout host=')
    expect(describeFailure('x', new ApiError('malformed', 'Invalid JSON from /api/results'))).toContain('malformed host=')
  })

  it('handles errors that are not ApiError', () => {
    expect(describeFailure('x', new Error('boom'))).toContain('unexpected Error: boom')
  })
})
