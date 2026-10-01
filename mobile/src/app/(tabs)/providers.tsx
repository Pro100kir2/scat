import { useCallback, useEffect, useRef, useState } from 'react'
import { AppState, StyleSheet, View } from 'react-native'
import { useIsFocused } from 'expo-router'
import { countryName, formatAgo, plural, type ProvidersResponse } from '@scat/shared'
import { Button } from '@/components/ui/Button'
import { Block, Screen } from '@/components/ui/Screen'
import { StateNote } from '@/components/ui/StateNote'
import { Em, T } from '@/components/ui/T'
import { env } from '@/config/env'
import { api } from '@/lib/api'
import { openExternal } from '@/lib/links'
import { colors, fonts, radius } from '@/theme'

const REFRESH_MS = 30_000
const PROBES = ['зонд', 'зонда', 'зондов'] as const
const STATUS = { online: 'На связи', offline: 'Не на связи', no_probe: 'Нет зондов' } as const
const LIGHT = { online: colors.aqua, offline: colors.sea3, no_probe: colors.text3 } as const

/** "Провайдеры": which ISP networks probes check from. Read-only; never starts any checks. */
export default function ProvidersScreen() {
  const focused = useIsFocused()
  const [data, setData] = useState<ProvidersResponse | null>(null)
  const [failed, setFailed] = useState(false)
  const [pulling, setPulling] = useState(false)
  const controller = useRef<AbortController | null>(null)

  const load = useCallback(async () => {
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    try {
      setData(await api.fetchProviders(abort.signal))
      setFailed(false)
    } catch (err) {
      if ((err as { kind?: string }).kind !== 'aborted') setFailed(true)
    }
  }, [])

  // Refresh while the tab is on screen and the app is in the foreground.
  useEffect(() => {
    if (!focused) return
    // Fetching on focus is what this effect is for; state is set when the reply arrives.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
    const timer = setInterval(() => {
      if (AppState.currentState === 'active') void load()
    }, REFRESH_MS)
    return () => {
      clearInterval(timer)
      controller.current?.abort()
    }
  }, [focused, load])

  const providers = [...(data?.providers ?? [])].sort((a, b) => a.name.localeCompare(b.name, 'ru'))
  const probes = data?.probes ?? []
  const online = probes.filter((p) => p.online).length

  return (
    <Screen
      refreshing={pulling}
      onRefresh={async () => {
        setPulling(true)
        await load()
        setPulling(false)
      }}
    >
      <Block gap={16}>
        <View style={{ gap: 6 }}>
          <T variant="eyebrow">Провайдеры</T>
          <T variant="section" accessibilityRole="header">Как видно <Em>из разных сетей</Em></T>
          <T>
            Скат сам проверяет, что конфигурация работает. Но один и тот же сервер может открываться у одного
            провайдера и быть заблокирован у другого. Поэтому лучшие найденные серверы дополнительно проверяют
            зонды: маленькие программы на устройствах добровольцев внутри сетей провайдеров.
          </T>
        </View>

        <View style={{ gap: 10 }}>
          <Button
            variant="primary"
            icon="plus"
            label="Добавить своего провайдера"
            accessibilityHint="Откроется сайт Ската: зонд ставится на роутер или компьютер"
            onPress={() => void openExternal(`${env.siteUrl}/providers/add/`)}
            testID="providers-add"
          />
          <Button variant="quiet" icon="external" label="Как устроен зонд" onPress={() => void openExternal(`${env.siteUrl}/providers/install/`)} />
        </View>

        <View style={{ gap: 4 }}>
          <T style={styles.h2}>Доступные <Em>провайдеры</Em></T>
          {data ? <T variant="muted">{online} {plural(online, PROBES)} на связи из {probes.length}</T> : null}
        </View>

        {!data && !failed ? (
          <View style={{ gap: 10 }} accessibilityLabel="Загружаем провайдеров" testID="providers-loading">
            {[0, 1, 2].map((i) => <View key={i} style={styles.skeleton} />)}
          </View>
        ) : failed && !data ? (
          <StateNote tone="error" title="Не удалось загрузить провайдеров" text="Сервер не ответил. Попробуйте ещё раз чуть позже.">
            <Button small icon="refresh" label="Попробовать снова" onPress={() => void load()} testID="providers-retry" />
          </StateNote>
        ) : !providers.length ? (
          <StateNote title="Пока ни одного провайдера" text="Здесь появятся сети, из которых зонды проверяют доступность серверов. Станьте первым." />
        ) : (
          providers.map((p) => (
            <View
              key={p.id}
              style={styles.provider}
              accessible
              accessibilityLabel={`${p.name}, ${countryName(p.country) ?? p.country}. ${STATUS[p.status]}. Зонды: ${p.probes_online} из ${p.probes_total} онлайн`}
            >
              <View style={[styles.light, { backgroundColor: LIGHT[p.status] }]} />
              <View style={{ flex: 1, gap: 4 }}>
                <T style={styles.name}>{p.name}</T>
                <T variant="small">
                  <T style={styles.cc}>{p.country}  </T>
                  {countryName(p.country)}{p.regions.length ? ` · ${p.regions.join(', ')}` : ''}
                </T>
                <View style={styles.facts}>
                  <Fact label="Статус" value={STATUS[p.status]} />
                  <Fact label="Зонды" value={`${p.probes_online} из ${p.probes_total} онлайн`} />
                  {p.asns.length ? <Fact label="ASN" value={p.asns.map((a) => `AS${a}`).join(', ')} mono /> : null}
                  <Fact label="Последний сигнал" value={formatAgo(p.last_heartbeat)} />
                </View>
              </View>
            </View>
          ))
        )}
      </Block>
    </Screen>
  )
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <View style={styles.fact}>
      <T variant="label">{label}</T>
      <T style={[styles.factValue, mono && { fontFamily: fonts.mono, fontSize: 12.5 }]}>{value}</T>
    </View>
  )
}

const styles = StyleSheet.create({
  h2: { fontFamily: fonts.display, fontSize: 26, lineHeight: 30, color: colors.text },
  skeleton: { height: 110, borderRadius: radius.md, backgroundColor: colors.sea, borderWidth: 1, borderColor: colors.line },
  provider: { flexDirection: 'row', gap: 12, padding: 14, borderRadius: radius.md, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.sea },
  light: { width: 10, height: 10, borderRadius: 5, marginTop: 7 },
  name: { fontFamily: fonts.uiBold, fontSize: 17, color: colors.text },
  cc: { fontFamily: fonts.mono, fontSize: 12, color: colors.text3 },
  facts: { flexDirection: 'row', flexWrap: 'wrap', columnGap: 22, rowGap: 8, marginTop: 6 },
  fact: { gap: 1 },
  factValue: { fontFamily: fonts.ui, fontSize: 14, color: colors.text },
})
