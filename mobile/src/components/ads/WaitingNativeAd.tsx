import { useEffect, useState } from 'react'
import { Image, StyleSheet, View } from 'react-native'
import type { NativeAd } from 'react-native-google-mobile-ads'
import { adUnits } from '@/config/admob'
import { useAdsReady } from '@/lib/ads'
import { adsSdk } from '@/lib/adsSdk'
import { T } from '@/components/ui/T'
import { colors, fonts } from '@/theme'

/**
 * The native ad shown under the dive log while a live scan runs. The parent mounts it only for
 * that, so each search loads a fresh ad and it leaves with the waiting state. Nothing is drawn
 * until Google has an ad.
 */
export function WaitingNativeAd() {
  const ready = useAdsReady()
  const [ad, setAd] = useState<NativeAd | null>(null)

  useEffect(() => {
    if (!ready) return
    let cancelled = false
    let loaded: NativeAd | null = null
    adsSdk()
      .NativeAd.createForAdRequest(adUnits.native)
      .then((a) => {
        if (cancelled) a.destroy()
        else {
          loaded = a
          setAd(a)
        }
      })
      .catch((err) => {
        if (__DEV__) console.log(`[Скат] ads: native failed: ${String(err)}`)
      })
    return () => {
      cancelled = true
      loaded?.destroy()
    }
  }, [ready])

  if (!ad) return null
  const { NativeAdView, NativeAsset, NativeAssetType, NativeMediaView } = adsSdk()
  return (
    <NativeAdView nativeAd={ad} style={styles.card} testID="waiting-native-ad">
      <View style={styles.row}>
        <View style={styles.badge}>
          <T style={styles.badgeText}>Реклама</T>
        </View>
        {ad.icon ? (
          <NativeAsset assetType={NativeAssetType.ICON}>
            <Image source={{ uri: ad.icon.url }} style={styles.icon} />
          </NativeAsset>
        ) : null}
        <View style={styles.text}>
          <NativeAsset assetType={NativeAssetType.HEADLINE}>
            <T style={styles.headline} numberOfLines={2}>{ad.headline}</T>
          </NativeAsset>
          {ad.body ? (
            <NativeAsset assetType={NativeAssetType.BODY}>
              <T style={styles.body} numberOfLines={2}>{ad.body}</T>
            </NativeAsset>
          ) : null}
        </View>
      </View>
      {ad.mediaContent ? <NativeMediaView style={styles.media} resizeMode="cover" /> : null}
      {ad.callToAction ? (
        <NativeAsset assetType={NativeAssetType.CALL_TO_ACTION}>
          <T style={styles.cta}>{ad.callToAction}</T>
        </NativeAsset>
      ) : null}
    </NativeAdView>
  )
}

const styles = StyleSheet.create({
  card: {
    marginTop: 16,
    gap: 10,
    padding: 12,
    borderRadius: 14,
    backgroundColor: 'rgba(8, 28, 40, 0.72)',
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  badge: { position: 'absolute', top: -2, right: 0, zIndex: 1, paddingHorizontal: 5, borderRadius: 4, backgroundColor: colors.line },
  badgeText: { fontFamily: fonts.mono, fontSize: 10, color: colors.text2 },
  icon: { width: 40, height: 40, borderRadius: 8 },
  text: { flex: 1, gap: 2, paddingRight: 52 },
  headline: { fontFamily: fonts.uiBold, fontSize: 14, lineHeight: 19, color: colors.text },
  body: { fontFamily: fonts.ui, fontSize: 12.5, lineHeight: 17, color: colors.text2 },
  media: { width: '100%', maxHeight: 160, borderRadius: 8, overflow: 'hidden' },
  cta: { alignSelf: 'flex-start', overflow: 'hidden', paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999, backgroundColor: colors.pearl, color: colors.abyss, fontFamily: fonts.uiBold, fontSize: 13 },
})
