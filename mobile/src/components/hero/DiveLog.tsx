import { StyleSheet, View } from 'react-native'
import { diveLogView, type ScanState } from '@scat/shared'
import { Button } from '@/components/ui/Button'
import { T } from '@/components/ui/T'
import { scanStore } from '@/lib/scan'
import { colors, fonts } from '@/theme'

const DOT = { quiet: colors.text3, live: colors.sand, done: colors.aqua, error: colors.coral } as const

/** The hero's "logbook": one calm line of narrative plus the real numbers behind it. */
export function DiveLog({ state, revealed }: { state: ScanState; revealed: number }) {
  const view = diveLogView(state, revealed)
  return (
    <View style={styles.log} accessibilityLiveRegion="polite" accessible accessibilityLabel={`${view.title}. ${view.detail}`}>
      <View style={styles.head}>
        <View style={[styles.dot, { backgroundColor: DOT[view.tone] }]} />
        <T style={styles.title}>{view.title}</T>
      </View>
      {view.detail ? <T style={styles.detail}>{view.detail}</T> : null}
      {view.progress !== undefined ? (
        <View style={styles.track}>
          <View style={[styles.fill, { width: `${Math.max(2, Math.min(100, view.progress * 100))}%` }]} />
        </View>
      ) : null}
      {view.retry ? (
        <Button
          small
          icon="refresh"
          label="Попробовать снова"
          onPress={() => void (view.retry === 'reload' ? scanStore.retry() : scanStore.startSearch())}
          style={{ alignSelf: 'flex-start', marginTop: 6, marginLeft: 16 }}
          testID="log-retry"
        />
      ) : null}
    </View>
  )
}

const styles = StyleSheet.create({
  log: { gap: 4 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  title: { fontFamily: fonts.uiBold, fontSize: 15, lineHeight: 20, color: colors.text, flexShrink: 1 },
  detail: { fontFamily: fonts.mono, fontSize: 12, lineHeight: 17, color: colors.text2, paddingLeft: 16 },
  track: { height: 2, marginLeft: 16, marginTop: 4, borderRadius: 1, backgroundColor: colors.line, overflow: 'hidden' },
  fill: { height: 2, backgroundColor: colors.aqua },
})
