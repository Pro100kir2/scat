import type { ReactNode } from 'react'
import { Modal, Pressable, ScrollView, StyleSheet, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { colors, gutter, maxContentWidth, radius } from '@/theme'
import { Icon } from './Icon'

interface Props {
  visible: boolean
  onClose: () => void
  children: ReactNode
  /** Announced when the sheet opens. */
  title: string
}

/**
 * A bottom sheet: slides up over a dimmed screen, scrolls when tall, closes by the close
 * button, a tap outside, or the Android back button / gesture.
 */
export function Sheet({ visible, onClose, children, title }: Props) {
  const insets = useSafeAreaInsets()
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent>
      <View style={styles.root}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} accessibilityLabel="Закрыть" accessibilityRole="button" />
        <View style={[styles.sheet, { paddingBottom: Math.max(insets.bottom, 16), maxHeight: '90%' }]} accessibilityViewIsModal accessibilityLabel={title}>
          <View style={styles.grip} />
          <Pressable onPress={onClose} hitSlop={10} style={styles.close} accessibilityRole="button" accessibilityLabel="Закрыть">
            <Icon name="close" size={20} color={colors.text2} />
          </Pressable>
          <ScrollView contentContainerStyle={styles.content} bounces={false} keyboardShouldPersistTaps="handled">
            {children}
          </ScrollView>
        </View>
      </View>
    </Modal>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: 'flex-end', backgroundColor: colors.scrim, alignItems: 'center' },
  sheet: {
    width: '100%',
    maxWidth: maxContentWidth,
    backgroundColor: colors.sea,
    borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg,
    borderWidth: 1,
    borderBottomWidth: 0,
    borderColor: colors.lineStrong,
  },
  grip: { alignSelf: 'center', width: 40, height: 4, borderRadius: 2, backgroundColor: colors.lineStrong, marginTop: 10 },
  close: { position: 'absolute', top: 10, right: 10, width: 44, height: 44, alignItems: 'center', justifyContent: 'center', zIndex: 1 },
  content: { paddingHorizontal: gutter, paddingTop: 14, paddingBottom: 8, gap: 16 },
})
