import { useCallback, useRef, useState } from 'react'
import { ScrollView, StyleSheet, View, type NativeScrollEvent, type NativeSyntheticEvent } from 'react-native'
import { useIsFocused } from 'expo-router'
import { RESULTS_NOTES, resultsPhase, resultsSubtitle, type PearlNode } from '@scat/shared'
import { Hero } from '@/components/hero/Hero'
import { ConfigSheet } from '@/components/results/ConfigSheet'
import { ResultsList, ResultsSkeleton } from '@/components/results/ResultsList'
import { Button } from '@/components/ui/Button'
import { CopyButton } from '@/components/ui/CopyButton'
import { Block, Screen } from '@/components/ui/Screen'
import { StateNote } from '@/components/ui/StateNote'
import { Em, T } from '@/components/ui/T'
import { scanStore, useScan } from '@/lib/scan'
import { useReducedMotion } from '@/lib/useReducedMotion'
import { colors } from '@/theme'

/** "Улов": the ocean, the search, and today's catch. */
export default function CatchScreen() {
  const state = useScan()
  const focused = useIsFocused()
  const reducedMotion = useReducedMotion()
  const scroll = useRef<ScrollView>(null)
  const heroHeight = useRef(0)
  const [scrolledPast, setScrolledPast] = useState(false)
  const [open, setOpen] = useState<PearlNode | null>(null)
  const [pulling, setPulling] = useState(false)

  const phase = resultsPhase(state)
  const subtitle = resultsSubtitle(state)

  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const past = e.nativeEvent.contentOffset.y > heroHeight.current - 40
    setScrolledPast((prev) => (prev === past ? prev : past))
  }
  const onHeroHeight = useCallback((h: number) => {
    heroHeight.current = h
  }, [])

  const seeCatch = () => scroll.current?.scrollTo({ y: heroHeight.current - 12, animated: !reducedMotion })
  const openRank = (rank: number) => {
    const node = state.nodes.find((n) => n.rank === rank)
    if (node) setOpen(node)
  }

  async function pull() {
    setPulling(true)
    try {
      await scanStore.retry()
    } finally {
      setPulling(false)
    }
  }

  return (
    <View style={styles.root}>
      <Screen scrollRef={scroll} padTop={false} onRefresh={pull} refreshing={pulling} onScroll={onScroll}>
        <Hero state={state} paused={!focused || scrolledPast} onSeeCatch={seeCatch} onPearl={openRank} onHeight={onHeroHeight} />

        <Block>
          <View style={styles.head}>
            <T variant="eyebrow">Улов</T>
            <T variant="section" accessibilityRole="header">Жемчужины <Em>дня</Em></T>
            {subtitle ? <T variant="small">{subtitle}</T> : null}
          </View>

          {phase === 'results' ? (
            <>
              <View style={styles.tools}>
                <CopyButton
                  text={state.nodes.map((n) => n.link).join('\n')}
                  label="Копировать все"
                  copiedLabel="Все скопированы"
                  small
                  testID="copy-all"
                />
                {state.status?.cache_state === 'stale' ? (
                  <View style={styles.stale}>
                    <View style={styles.staleDot} />
                    <T variant="muted" style={{ color: colors.sand }}>Обновляется</T>
                  </View>
                ) : null}
              </View>
              <ResultsList nodes={state.nodes} onOpen={setOpen} />
            </>
          ) : phase === 'loading' ? (
            <ResultsSkeleton still={reducedMotion} />
          ) : phase === 'searching' ? (
            <StateNote tone="searching" {...RESULTS_NOTES.searching} text="Первое погружение займёт пару минут. Жемчужины появятся здесь сами." />
          ) : phase === 'error' ? (
            <StateNote tone="error" {...RESULTS_NOTES.error}>
              <Button small icon="refresh" label="Попробовать снова" onPress={() => void scanStore.retry()} testID="results-retry" />
            </StateNote>
          ) : (
            <StateNote {...RESULTS_NOTES.empty} />
          )}
        </Block>
      </Screen>

      <ConfigSheet node={open} onClose={() => setOpen(null)} />
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.abyss },
  head: { gap: 4, marginTop: 4 },
  tools: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  stale: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  staleDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.sand },
})
