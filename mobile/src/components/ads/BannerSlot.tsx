import { StyleSheet, View } from 'react-native'
import { adUnits } from '@/config/admob'
import { useAdsReady } from '@/lib/ads'
import { adsSdk } from '@/lib/adsSdk'

/** One adaptive banner above the tab bar. It has no height until Google has an ad for it. */
export function BannerSlot() {
  const ready = useAdsReady()
  if (!ready) return null
  const { BannerAd, BannerAdSize } = adsSdk()
  return (
    <View style={styles.wrap} testID="banner-slot">
      <BannerAd
        unitId={adUnits.banner}
        size={BannerAdSize.ANCHORED_ADAPTIVE_BANNER}
        onAdFailedToLoad={(err) => {
          if (__DEV__) console.log(`[Скат] ads: banner failed: ${err.code} ${err.message}`)
        }}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  wrap: { alignItems: 'center', width: '100%' },
})
