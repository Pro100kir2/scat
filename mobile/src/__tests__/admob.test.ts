import ids from '../../admob.json'
import { resolveAdUnits } from '@/config/admob'

const GOOGLE_TEST = /^ca-app-pub-3940256099942544[/~]\d+$/
const SCAT = /^ca-app-pub-9243108413661209[/~]\d+$/

describe('AdMob configuration', () => {
  it('development uses only Google test units', () => {
    const u = resolveAdUnits(true)
    for (const id of Object.values(u)) expect(id).toMatch(GOOGLE_TEST)
    expect(ids.test.androidAppId).toMatch(GOOGLE_TEST)
  })

  it('release uses exactly the configured Scat units', () => {
    expect(resolveAdUnits(false)).toEqual({
      banner: 'ca-app-pub-9243108413661209/3239321635',
      native: 'ca-app-pub-9243108413661209/5482341595',
      appOpen: 'ca-app-pub-9243108413661209/5346610228',
    })
    for (const id of Object.values(resolveAdUnits(false))) expect(id).toMatch(SCAT)
    expect(ids.production.androidAppId).toBe('ca-app-pub-9243108413661209~7454822256')
  })

  it('has no rewarded units', () => {
    expect(Object.keys(resolveAdUnits(false)).sort()).toEqual(['appOpen', 'banner', 'native'])
  })
})
