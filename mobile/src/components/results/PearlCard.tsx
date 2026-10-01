import { Pressable, StyleSheet, View } from 'react-native'
import {
  countryName,
  latencyQuality,
  networkLabel,
  QUALITY_LABEL,
  QUALITY_LEVEL,
  securityLabel,
  type PearlNode,
  type Quality,
} from '@scat/shared'
import { CopyButton } from '@/components/ui/CopyButton'
import { Icon } from '@/components/ui/Icon'
import { T } from '@/components/ui/T'
import { colors, fonts, radius } from '@/theme'

const QUALITY_COLOR: Record<Quality, string> = {
  excellent: colors.aqua,
  good: colors.aqua,
  fair: colors.sand,
  slow: colors.coral,
}

/** Four bars, like the site's meter: how good the latency is at a glance. */
function Meter({ quality }: { quality: Quality }) {
  const level = QUALITY_LEVEL[quality]
  return (
    <View style={styles.meter}>
      {[1, 2, 3, 4].map((i) => (
        <View key={i} style={[styles.bar, { height: 5 + i * 3, backgroundColor: i <= level ? QUALITY_COLOR[quality] : colors.sea3 }]} />
      ))}
    </View>
  )
}

/** One configuration in the catch. Tapping it opens the details sheet. */
export function PearlCard({ node, onOpen }: { node: PearlNode; onOpen: (node: PearlNode) => void }) {
  const quality = latencyQuality(node.latency_ms)
  const place = countryName(node.country) ?? 'Страна неизвестна'
  const summary = [
    `Место ${node.rank}`,
    place,
    node.latency_ms !== null ? `${node.latency_ms} миллисекунд` : null,
    node.speed_mbps ? `${node.speed_mbps} мегабит в секунду` : null,
    QUALITY_LABEL[quality],
    securityLabel(node.security),
  ].filter(Boolean).join(', ')

  return (
    <View style={[styles.card, node.rank === 1 && styles.top]}>
      <Pressable
        onPress={() => onOpen(node)}
        accessibilityRole="button"
        accessibilityLabel={summary}
        accessibilityHint="Открыть подробности, QR-код и ссылку"
        style={({ pressed }) => [styles.main, pressed && styles.pressed]}
        testID={`pearl-${node.rank}`}
      >
        <View style={styles.rank}>
          <View style={[styles.pearl, { backgroundColor: node.rank === 1 ? colors.pearl : colors.pearl2 }]} />
          <T style={styles.rankNum}>{String(node.rank).padStart(2, '0')}</T>
        </View>

        <View style={styles.where}>
          <T style={styles.place} numberOfLines={1}>
            {node.country ? <T style={styles.cc}>{node.country}  </T> : null}
            {place}
          </T>
          <T variant="mono" style={styles.host} numberOfLines={1}>{node.address}</T>
          <View style={styles.tags}>
            <View style={[styles.tag, node.security === 'none' && styles.tagWarn]}>
              <T style={[styles.tagText, node.security === 'none' && { color: colors.coral }]}>{securityLabel(node.security)}</T>
            </View>
            <View style={styles.tag}><T style={styles.tagText}>{networkLabel(node.network)}</T></View>
            {node.probe_total ? (
              <View style={styles.tag}>
                <T style={styles.tagText}>сети {node.probe_score}/{node.probe_total}</T>
              </View>
            ) : null}
          </View>
        </View>

        <View style={styles.speed}>
          <Meter quality={quality} />
          <T style={styles.ms}>{node.latency_ms ?? '—'}<T style={styles.unit}> мс</T></T>
          {node.speed_mbps ? <T style={styles.mbps}>{node.speed_mbps}<T style={styles.unit}> Мбит/с</T></T> : null}
          <T style={[styles.quality, { color: QUALITY_COLOR[quality] }]}>
            {QUALITY_LABEL[quality]}{node.stable ? '' : ` · ${node.checks_passed}/${node.checks_total}`}
          </T>
        </View>
      </Pressable>

      <View style={styles.actions}>
        <CopyButton text={node.link} small testID={`copy-${node.rank}`} />
        <Pressable
          onPress={() => onOpen(node)}
          accessibilityRole="button"
          accessibilityLabel="Подробнее"
          hitSlop={8}
          style={({ pressed }) => [styles.more, pressed && styles.pressed]}
        >
          <T style={styles.moreText}>Подробнее</T>
          <Icon name="chevronRight" size={16} color={colors.text2} />
        </Pressable>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.md, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.sea, overflow: 'hidden' },
  top: { borderColor: 'rgba(243, 233, 216, 0.28)' },
  main: { flexDirection: 'row', gap: 12, padding: 14, paddingBottom: 10 },
  pressed: { backgroundColor: 'rgba(168, 208, 222, 0.06)' },
  rank: { alignItems: 'center', width: 26, gap: 6, paddingTop: 3 },
  pearl: { width: 12, height: 12, borderRadius: 6 },
  rankNum: { fontFamily: fonts.mono, fontSize: 12, color: colors.text3 },
  where: { flex: 1, minWidth: 0, gap: 3 },
  place: { fontFamily: fonts.uiBold, fontSize: 16, lineHeight: 22, color: colors.text },
  cc: { fontFamily: fonts.mono, fontSize: 12, color: colors.text3 },
  host: { color: colors.text2, fontSize: 12.5 },
  tags: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 5 },
  tag: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.line },
  tagWarn: { borderColor: 'rgba(236, 154, 134, 0.45)' },
  tagText: { fontFamily: fonts.uiMedium, fontSize: 11.5, lineHeight: 16, color: colors.text2 },
  speed: { alignItems: 'flex-end', gap: 1 },
  meter: { flexDirection: 'row', alignItems: 'flex-end', gap: 2.5, height: 18, marginBottom: 3 },
  bar: { width: 3.5, borderRadius: 2 },
  ms: { fontFamily: fonts.mono, fontSize: 16, lineHeight: 20, color: colors.text },
  mbps: { fontFamily: fonts.mono, fontSize: 12.5, lineHeight: 17, color: colors.text2 },
  unit: { fontFamily: fonts.mono, fontSize: 11, color: colors.text3 },
  quality: { fontFamily: fonts.uiMedium, fontSize: 12, lineHeight: 16 },
  actions: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 10, paddingBottom: 10 },
  more: { flexDirection: 'row', alignItems: 'center', gap: 4, minHeight: 38, paddingHorizontal: 10, borderRadius: radius.pill },
  moreText: { fontFamily: fonts.uiMedium, fontSize: 14, color: colors.text2 },
})
