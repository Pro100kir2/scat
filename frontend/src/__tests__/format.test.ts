import { describe, expect, it } from 'vitest'
import { formatUntil, latencyQuality, plural, PEARLS_NOM } from '@scat/shared'

describe('plural', () => {
  it.each([
    [1, 'жемчужина'],
    [2, 'жемчужины'],
    [5, 'жемчужин'],
    [11, 'жемчужин'],
    [21, 'жемчужина'],
    [34, 'жемчужины'],
    [112, 'жемчужин'],
  ])('%i → %s', (n, word) => expect(plural(n, PEARLS_NOM)).toBe(word))
})

describe('formatUntil', () => {
  const now = Date.parse('2026-01-01T12:00:00Z')
  it('hours', () => expect(formatUntil('2026-01-02T11:00:00Z', now)).toBe('через 23 ч'))
  it('minutes', () => expect(formatUntil('2026-01-01T12:40:00Z', now)).toBe('через 40 мин'))
  it('past', () => expect(formatUntil('2026-01-01T11:00:00Z', now)).toBeNull())
})

describe('latencyQuality', () => {
  it('tiers', () => {
    expect(latencyQuality(450)).toBe('excellent')
    expect(latencyQuality(700)).toBe('good')
    expect(latencyQuality(1200)).toBe('fair')
    expect(latencyQuality(3000)).toBe('slow')
    expect(latencyQuality(null)).toBe('slow')
  })
})
