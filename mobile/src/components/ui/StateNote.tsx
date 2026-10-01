import type { ReactNode } from 'react'
import { StyleSheet, View } from 'react-native'
import { colors, fonts } from '@/theme'
import { SkatMark } from './Icon'
import { T } from './T'

/** Empty, searching and error states: the manta resting over an empty patch of sand. */
export function StateNote({ title, text, tone = 'calm', children }: { title: string; text: string; tone?: 'calm' | 'error' | 'searching'; children?: ReactNode }) {
  return (
    <View style={styles.note} accessibilityRole={tone === 'error' ? 'alert' : undefined}>
      <View style={styles.scene}>
        <SkatMark size={56} color={tone === 'error' ? colors.coral : tone === 'searching' ? colors.sand : colors.text3} />
        <View style={styles.sand} />
      </View>
      <T style={styles.title}>{title}</T>
      <T variant="small" style={styles.text}>{text}</T>
      {children ? <View style={styles.actions}>{children}</View> : null}
    </View>
  )
}

const styles = StyleSheet.create({
  note: { alignItems: 'center', paddingVertical: 36, paddingHorizontal: 12, gap: 8 },
  scene: { alignItems: 'center', marginBottom: 6 },
  sand: { width: 120, height: 1, backgroundColor: colors.lineStrong, marginTop: 6 },
  title: { fontFamily: fonts.display, fontSize: 26, lineHeight: 30, color: colors.text, textAlign: 'center' },
  text: { textAlign: 'center', maxWidth: 420 },
  actions: { marginTop: 10, flexDirection: 'row', gap: 10, flexWrap: 'wrap', justifyContent: 'center' },
})
