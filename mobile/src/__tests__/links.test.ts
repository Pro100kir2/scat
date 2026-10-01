import { Linking, Share } from 'react-native'
import * as Clipboard from 'expo-clipboard'
import { encode } from 'uqr'
import { copyText, openInApp, shareText, SUBSCRIPTION_IMPORTERS } from '@/lib/links'
import { qrPath } from '@/lib/qr'

// Percent-encoding, an emoji fragment, "+" and "=": everything that naive handling would damage.
const LINK = 'vless://11111111-2222-3333-4444-555555555555@example.com:443?type=ws&security=tls&sni=a.b&path=%2Fws%3Fed%3D2048&host=a%2Bb.c#%F0%9F%87%A9%F0%9F%87%AA%20Frankfurt+1'

afterEach(() => jest.restoreAllMocks())

describe('getting a config out of the app', () => {
  it('copies the link byte for byte', async () => {
    expect(await copyText(LINK)).toBe(true)
    expect(Clipboard.setStringAsync).toHaveBeenCalledWith(LINK)
  })

  it('reports a failed copy instead of throwing', async () => {
    ;(Clipboard.setStringAsync as jest.Mock).mockRejectedValueOnce(new Error('denied'))
    expect(await copyText(LINK)).toBe(false)
  })

  it('shares the exact link through the native sheet', async () => {
    const share = jest.spyOn(Share, 'share').mockResolvedValue({ action: 'sharedAction' })
    expect(await shareText(LINK, 'Скат')).toBe(true)
    expect(share.mock.calls[0]![0]).toMatchObject({ message: LINK })
  })

  it('hands the untouched vless:// link to the OS, and says when nothing opens it', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValueOnce(true)
    expect(await openInApp(LINK)).toBe(true)
    expect(open).toHaveBeenCalledWith(LINK)
    open.mockRejectedValueOnce(new Error('No Activity found to handle Intent'))
    expect(await openInApp(LINK)).toBe(false)
  })

  it('builds client import links that carry the subscription URL', () => {
    const url = 'https://api.scat-search.ru/api/vless/sub'
    const links = Object.fromEntries(SUBSCRIPTION_IMPORTERS.map((i) => [i.name, i.link(url)]))
    expect(links.Hiddify).toBe(`hiddify://import/${url}`)
    expect(links.v2rayNG).toContain(encodeURIComponent(url))
    expect(links.Streisand).toBe(`streisand://import/${url}`)
  })
})

describe('QR code', () => {
  it('draws exactly the dark modules of the encoder, for the exact link', () => {
    const { d, size } = qrPath(LINK)
    const matrix = encode(LINK, { ecc: 'M', border: 2 })
    expect(size).toBe(matrix.size)
    // Sum of the run lengths in the path = number of dark modules.
    const drawn = [...d.matchAll(/h(\d+)v1/g)].reduce((n, m) => n + Number(m[1]), 0)
    expect(drawn).toBe(matrix.data.flat().filter(Boolean).length)
    expect(qrPath(LINK + 'x').d).not.toBe(d) // a different link is a different code
  })

  it('keeps the quiet zone empty', () => {
    const { d } = qrPath(LINK, 2)
    expect(d).not.toMatch(/M[01] |M\d+ [01]h/)
  })
})
