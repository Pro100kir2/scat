import { StyleSheet, View } from 'react-native'
import { NETWORKS, plural, REACH_STATE, reachSummary, type Reachability, type ReachTone } from '@scat/shared'
import { T } from '@/components/ui/T'
import { colors, fonts, radius } from '@/theme'

const TONE: Record<ReachTone, { light: string; ring: boolean }> = {
  reachable: { light: colors.aqua, ring: false },
  blocked: { light: colors.coral, ring: false },
  checking: { light: colors.sand, ring: false },
  offline: { light: colors.sea3, ring: false },
  none: { light: colors.text3, ring: true },
}

/**
 * "Network availability" in the details sheet: one beacon per provider. Only whether the
 * server is reachable from that ISP; whether the config works is what the card itself says.
 * Every state has its own words, so colour is never the only cue.
 */
export function NetworkAvailability({ rows }: { rows: Reachability[] }) {
  const sorted = [...rows].sort((a, b) => a.provider.localeCompare(b.provider, 'ru'))
  const summary = reachSummary(rows)
  return (
    <View style={styles.box} accessibilityLabel="Доступность в сетях провайдеров">
      <View style={styles.head}>
        <T variant="strong" style={{ fontSize: 15 }}>Доступность в сетях</T>
        {summary.tested ? (
          <T variant="muted">Доступна в <T style={styles.count}>{summary.reachable}</T> из {summary.tested} {plural(summary.tested, NETWORKS)}</T>
        ) : null}
      </View>
      {sorted.length ? (
        <>
          {sorted.map((row) => {
            const state = REACH_STATE[row.state]
            const tone = TONE[state.tone]
            return (
              <View key={row.provider_id} style={styles.row} accessible accessibilityLabel={`${row.provider}: ${state.label}. ${state.hint}`}>
                <View style={[styles.light, tone.ring ? { borderWidth: 1.5, borderColor: tone.light } : { backgroundColor: tone.light }]} />
                <T style={styles.name} numberOfLines={1}>{row.provider}</T>
                <T style={[styles.state, { color: state.tone === 'reachable' ? colors.aqua : state.tone === 'blocked' ? colors.coral : colors.text2 }]}>
                  {state.label}{row.state === 'SUCCESS' && row.latency_ms !== null ? ` · ${row.latency_ms} мс` : ''}
                </T>
              </View>
            )
          })}
          <T variant="muted">Зонды проверяют только, доступен ли сервер из сети провайдера. Работа самой конфигурации проверена выше.</T>
        </>
      ) : (
        <T variant="muted">Пока ни один провайдер не подключил зонд. Добавьте своего на вкладке «Провайдеры», и здесь появится, открывается ли сервер из вашей сети.</T>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  box: { gap: 8, padding: 14, borderRadius: radius.md, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.deep },
  head: { gap: 2, marginBottom: 2 },
  count: { fontFamily: fonts.uiBold, color: colors.text, fontSize: 13 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 28 },
  light: { width: 10, height: 10, borderRadius: 5 },
  name: { flex: 1, fontFamily: fonts.uiMedium, fontSize: 14.5, color: colors.text },
  state: { fontFamily: fonts.ui, fontSize: 13.5 },
})
