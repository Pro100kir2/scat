/**
 * Getting a configuration out of the app: clipboard, the native share sheet, and handing a
 * link to another app. Links are passed on byte for byte: never re-encoded, trimmed or
 * rewritten here.
 */
import { Linking, Share } from 'react-native'
import * as Clipboard from 'expo-clipboard'

export async function copyText(text: string): Promise<boolean> {
  try {
    await Clipboard.setStringAsync(text)
    return true
  } catch {
    return false
  }
}

/** Native share sheet. Resolves false when the sheet could not be shown. */
export async function shareText(text: string, title?: string): Promise<boolean> {
  try {
    await Share.share({ message: text, title }, { dialogTitle: title, subject: title })
    return true
  } catch {
    return false
  }
}

/**
 * Hand a vless:// link (or any app link) to whichever installed app claims it.
 * false = nothing on this device opens such links.
 */
export async function openInApp(link: string): Promise<boolean> {
  try {
    await Linking.openURL(link)
    return true
  } catch {
    return false
  }
}

export const openExternal = (url: string) => openInApp(url)

/**
 * Import links of VPN clients for a subscription URL. These are the clients' own URL
 * schemes; if the client is not installed, opening fails and the UI offers copy/share.
 */
export const SUBSCRIPTION_IMPORTERS: { name: string; platforms: ('ios' | 'android')[]; link: (url: string) => string }[] = [
  { name: 'Hiddify', platforms: ['ios', 'android'], link: (url) => `hiddify://import/${url}` },
  { name: 'v2rayNG', platforms: ['android'], link: (url) => `v2rayng://install-sub?url=${encodeURIComponent(url)}&name=${encodeURIComponent('Скат')}` },
  { name: 'Streisand', platforms: ['ios'], link: (url) => `streisand://import/${url}` },
]
