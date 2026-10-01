import type { ReactNode } from 'react'
import { RefreshControl, ScrollView, StyleSheet, View, type ScrollViewProps } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { BannerSlot } from '@/components/ads/BannerSlot'
import { colors, gutter, maxContentWidth } from '@/theme'

interface Props extends Pick<ScrollViewProps, 'keyboardShouldPersistTaps' | 'onScroll'> {
  children: ReactNode
  /** Pull to refresh. */
  onRefresh?: () => void
  refreshing?: boolean
  /** false when the first child paints behind the status bar itself (the ocean). */
  padTop?: boolean
  scrollRef?: React.Ref<ScrollView>
}

/** A scrolling screen on the abyss background, content centred and capped on tablets. */
export function Screen({ children, onRefresh, refreshing = false, padTop = true, scrollRef, ...rest }: Props) {
  const insets = useSafeAreaInsets()
  return (
    <View style={styles.screen}>
      <ScrollView
        ref={scrollRef}
        style={styles.scroll}
        contentContainerStyle={{ paddingTop: padTop ? insets.top + 12 : 0, paddingBottom: 32 }}
        refreshControl={onRefresh
          ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.aqua} colors={[colors.aqua]} progressBackgroundColor={colors.sea2} />
          : undefined}
        scrollEventThrottle={64}
        {...rest}
      >
        {children}
      </ScrollView>
      <BannerSlot />
    </View>
  )
}

/** Horizontal gutter plus the tablet width cap, for everything except full-bleed blocks. */
export function Block({ children, gap = 14 }: { children: ReactNode; gap?: number }) {
  return <View style={[styles.block, { gap }]}>{children}</View>
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.abyss },
  scroll: { flex: 1, backgroundColor: colors.abyss },
  block: { width: '100%', maxWidth: maxContentWidth, alignSelf: 'center', paddingHorizontal: gutter },
})
