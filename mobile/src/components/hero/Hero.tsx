import { useEffect, useState } from 'react'
import { Pressable, StyleSheet, useWindowDimensions, View, type LayoutChangeEvent } from 'react-native'
import { router } from 'expo-router'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { formatCountdown, plural, PEARLS_NOM, REFRESH_COOLDOWN_SECONDS, sceneFound, sceneMode, type ScanState } from '@scat/shared'
import type { Rect } from '@scat/shared/ocean'
import { Button } from '@/components/ui/Button'
import { Icon, SkatMark } from '@/components/ui/Icon'
import { Em, T } from '@/components/ui/T'
import { WaitingNativeAd } from '@/components/ads/WaitingNativeAd'
import { showsWaitingAd } from '@/lib/ads'
import { scanStore } from '@/lib/scan'
import { useCooldown } from '@/lib/useCooldown'
import { useReducedMotion } from '@/lib/useReducedMotion'
import { OceanView } from '@/ocean/OceanView'
import { colors, fonts, gutter, maxContentWidth } from '@/theme'
import { DiveLog } from './DiveLog'

interface Props {
  state: ScanState
  /** The scene is not on screen (another tab, or scrolled away): stop animating. */
  paused: boolean
  onSeeCatch: () => void
  onPearl: (rank: number) => void
  onHeight: (height: number) => void
}

/** Sand left under the text for the manta and the pearls. */
const SEABED_ROOM = 250

/** The first screen: the ocean, the headline, the call to action and the dive log. */
export function Hero({ state, paused, onSeeCatch, onPearl, onHeight }: Props) {
  const insets = useSafeAreaInsets()
  const window = useWindowDimensions()
  const reducedMotion = useReducedMotion()
  const cooldown = useCooldown('skat:refresh-until', REFRESH_COOLDOWN_SECONDS)
  const [refreshing, setRefreshing] = useState(false)
  const [revealed, setRevealed] = useState(0)
  const [safeZone, setSafeZone] = useState<Rect | null>(null)
  const [contentHeight, setContentHeight] = useState(0)

  const mode = sceneMode(state)
  const found = sceneFound(state)
  // A new dive starts from zero pearls (state adjusted while rendering, not in an effect).
  const [lastFound, setLastFound] = useState(found)
  if (found !== lastFound) {
    setLastFound(found)
    if (found === 0) setRevealed(0)
  }

  const diving = state.journey === 'diving'
  const hasCatch = state.journey === 'surfaced' && state.nodes.length > 0
  const height = Math.max(Math.round(window.height * 0.9), contentHeight + insets.top + SEABED_ROOM)
  useEffect(() => onHeight(height), [height, onHeight])

  async function refresh() {
    setRefreshing(true)
    try {
      const wait = await scanStore.refreshCatch()
      if (wait > 0) cooldown.start(wait)
    } finally {
      setRefreshing(false)
    }
  }

  /** Keep the manta out from behind the headline. */
  const onContentLayout = (e: LayoutChangeEvent) => {
    const { x, y, width, height: h } = e.nativeEvent.layout
    setContentHeight(h)
    setSafeZone({ x, y, width, height: h })
  }

  return (
    <View style={{ height }}>
      <OceanView
        mode={mode}
        found={found}
        reducedMotion={reducedMotion}
        safeZone={safeZone}
        paused={paused}
        onReveal={setRevealed}
        onDrained={() => {
          if (mode === 'replay') scanStore.completeReplay()
        }}
        onPearl={onPearl}
      />

      <View style={[styles.content, { top: insets.top + 8 }]} onLayout={onContentLayout} pointerEvents="box-none">
        <View style={styles.brand}>
          <View style={styles.logo} accessible accessibilityRole="header" accessibilityLabel="Скат">
            <SkatMark size={26} />
            <T style={styles.word}>СКАТ</T>
          </View>
          <Pressable
            onPress={() => router.push('/about')}
            hitSlop={10}
            style={styles.about}
            accessibilityRole="button"
            accessibilityLabel="Как это работает и ответы на вопросы"
            testID="open-about"
          >
            <Icon name="info" size={22} color={colors.text2} />
          </Pressable>
        </View>

        <T variant="eyebrow" style={{ marginTop: 14 }}>Бесплатный VPN · VLESS · Xray</T>
        <T variant="display" style={[styles.title, window.width < 400 && styles.titleNarrow]} accessibilityRole="header">
          Ищет жемчужины{'\n'}<Em>в глубинах интернета</Em>
        </T>
        <T style={styles.lead}>
          Скат ищет бесплатные VPN: просматривает тысячи открытых VLESS-конфигураций, подключается к каждой
          по‑настоящему и поднимает на поверхность только те, что работают быстро и стабильно.
        </T>

        <View style={styles.actions}>
          <Button
            variant="primary"
            label={diving ? 'Скат на глубине' : hasCatch ? 'Смотреть улов' : 'Начать поиск'}
            icon={hasCatch && !diving ? 'arrowDown' : undefined}
            disabled={diving}
            onPress={() => (hasCatch ? onSeeCatch() : void scanStore.startSearch())}
            testID="hero-cta"
          />
          <Button
            label="Очистить кэш"
            icon="refresh"
            disabled={diving || refreshing || cooldown.remaining > 0}
            onPress={refresh}
            accessibilityLabel={cooldown.remaining ? `Очистить кэш, доступно через ${formatCountdown(cooldown.remaining)}` : 'Очистить кэш'}
            accessibilityHint="Забыть сегодняшние результаты и найти свежие"
            trailing={cooldown.remaining ? <T style={styles.timer}>{formatCountdown(cooldown.remaining)}</T> : undefined}
            testID="hero-refresh"
          />
        </View>

        <View style={styles.status}>
          {state.journey !== 'resting' ? (
            <View style={styles.counter} accessibilityLiveRegion="polite" accessible accessibilityLabel={`${revealed} ${plural(revealed, PEARLS_NOM)}`}>
              <View style={styles.pearl} />
              <T style={styles.count}>{revealed}</T>
              <T variant="small">{plural(revealed, PEARLS_NOM)}</T>
            </View>
          ) : null}
          <DiveLog state={state} revealed={revealed} />
        </View>

        {showsWaitingAd(state) ? <WaitingNativeAd /> : null}
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  content: {
    position: 'absolute',
    left: 0,
    right: 0,
    paddingHorizontal: gutter,
    maxWidth: maxContentWidth,
    alignSelf: 'center',
    width: '100%',
  },
  brand: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: 44 },
  logo: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  word: { fontFamily: fonts.uiBold, fontSize: 14, letterSpacing: 4, color: colors.text },
  about: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center', marginRight: -10 },
  title: { marginTop: 8 },
  // Keeps "в глубинах интернета" on one line on a 360–390pt phone.
  titleNarrow: { fontSize: 33, lineHeight: 36 },
  lead: { marginTop: 12, fontFamily: fonts.ui, fontSize: 15, lineHeight: 22, color: colors.text2 },
  actions: { marginTop: 18, flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  timer: { fontFamily: fonts.mono, fontSize: 13, color: colors.sand },
  status: { marginTop: 18, gap: 10 },
  counter: { flexDirection: 'row', alignItems: 'baseline', gap: 8 },
  pearl: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.pearl, alignSelf: 'center' },
  count: { fontFamily: fonts.mono, fontSize: 24, lineHeight: 28, color: colors.pearl },
})
