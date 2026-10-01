import { useState } from 'react'
import { Pressable, StyleSheet, TextInput, View } from 'react-native'
import { COUNTRY_RE, countryName, formatNumber, SEARCH_COUNTRIES, type PearlNode } from '@scat/shared'
import { ConfigSheet } from '@/components/results/ConfigSheet'
import { ResultsList, ResultsSkeleton } from '@/components/results/ResultsList'
import { Button } from '@/components/ui/Button'
import { CopyButton } from '@/components/ui/CopyButton'
import { Block, Screen } from '@/components/ui/Screen'
import { StateNote } from '@/components/ui/StateNote'
import { Em, T } from '@/components/ui/T'
import { countryErrorText, useCountrySearch } from '@/lib/useCountrySearch'
import { useReducedMotion } from '@/lib/useReducedMotion'
import { colors, fonts, radius, touch } from '@/theme'

const STAGE: Record<string, string> = {
  downloading: 'загружает открытые списки',
  parsing: 'разбирает находки',
  testing: 'ищет рабочие конфигурации',
  rechecking: 'перепроверяет лучших',
  completed: 'сохраняет улов',
}

/** "Страны": the same search, limited to configs that exit in one country. */
export default function CountriesScreen() {
  const { state, search, reset } = useCountrySearch()
  const reducedMotion = useReducedMotion()
  const [code, setCode] = useState('')
  const [open, setOpen] = useState<PearlNode | null>(null)

  const valid = COUNTRY_RE.test(code.trim())
  const name = state.country ? countryName(state.country) ?? state.country : ''
  const data = state.data
  const progress = data?.progress

  return (
    <View style={styles.root}>
      <Screen keyboardShouldPersistTaps="handled">
        <Block>
          <View style={{ gap: 6 }}>
            <T variant="eyebrow">Страны</T>
            <T variant="section" accessibilityRole="header">Выход <Em>в нужной стране</Em></T>
            <T>
              Скат найдёт конфигурации, трафик которых выходит в интернет в выбранной стране. Страна измеряется
              при проверке, а не берётся из названия сервера.
            </T>
          </View>

          <View style={styles.chips} accessibilityRole="radiogroup" accessibilityLabel="Страна">
            {SEARCH_COUNTRIES.map((c) => {
              const selected = state.country === c
              return (
                <Pressable
                  key={c}
                  onPress={() => {
                    setCode('')
                    search(c)
                  }}
                  accessibilityRole="radio"
                  accessibilityState={{ selected }}
                  accessibilityLabel={countryName(c) ?? c}
                  style={({ pressed }) => [styles.chip, selected && styles.chipOn, pressed && styles.chipPressed]}
                  testID={`country-${c}`}
                >
                  <T style={styles.chipCode}>{c}</T>
                  <T style={[styles.chipName, selected && { color: colors.text }]}>{countryName(c)}</T>
                </Pressable>
              )
            })}
          </View>

          <View style={styles.form}>
            <TextInput
              value={code}
              onChangeText={(v) => setCode(v.replace(/[^A-Za-z]/g, '').slice(0, 2).toUpperCase())}
              placeholder="Код, например DE"
              placeholderTextColor={colors.text3}
              autoCapitalize="characters"
              autoCorrect={false}
              maxLength={2}
              returnKeyType="search"
              onSubmitEditing={() => valid && search(code)}
              style={styles.input}
              accessibilityLabel="Двухбуквенный код страны"
              testID="country-input"
            />
            <Button label="Найти" icon="search" disabled={!valid} onPress={() => search(code)} testID="country-search" />
          </View>

          {state.phase === 'idle' ? (
            <T variant="muted">Первый поиск по стране занимает пару минут; результат хранится сутки и общий для всех.</T>
          ) : null}

          {state.phase === 'searching' ? (
            <View style={{ gap: 12 }}>
              <StateNote
                tone="searching"
                title={`Скат ищет: ${name}`}
                text={data?.queued
                  ? 'Поиск стоит в очереди: Скат заканчивает другое погружение.'
                  : progress
                    ? `Скат ${STAGE[progress.stage] ?? 'на глубине'} · проверено ${formatNumber(progress.processed_candidates)} · найдено ${progress.working_candidates}`
                    : 'Готовится к погружению. Экран обновится сам.'}
              >
                <Button small label="Отменить" onPress={reset} testID="country-cancel" />
              </StateNote>
              <ResultsSkeleton still={reducedMotion} />
            </View>
          ) : null}

          {state.phase === 'error' && state.error ? (
            <StateNote tone="error" {...countryErrorText(state.error)}>
              <Button small icon="refresh" label="Попробовать снова" onPress={() => state.country && search(state.country)} testID="country-retry" />
            </StateNote>
          ) : null}

          {state.phase === 'ready' && data ? (
            data.nodes.length ? (
              <View style={{ gap: 12 }}>
                <View style={styles.resultHead}>
                  <T variant="strong" style={{ flex: 1 }}>{name}: {data.nodes.length} из {data.cache?.working ?? data.nodes.length} рабочих</T>
                  <CopyButton text={data.nodes.map((n) => n.link).join('\n')} label="Копировать все" copiedLabel="Скопированы" small />
                </View>
                {!data.ready ? <T variant="muted" style={{ color: colors.sand }}>Показаны прошлые результаты, Скат обновляет их.</T> : null}
                <ResultsList nodes={data.nodes} onOpen={setOpen} />
              </View>
            ) : (
              <StateNote
                title={`${name}: жемчужин не нашлось`}
                text="Сейчас среди открытых конфигураций нет рабочих с выходом в этой стране. Попробуйте другую страну или загляните позже."
              />
            )
          ) : null}
        </Block>
      </Screen>
      <ConfigSheet node={open} onClose={() => setOpen(null)} />
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.abyss },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    minHeight: 40, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 7,
    borderRadius: radius.pill, borderWidth: 1, borderColor: colors.line,
  },
  chipOn: { borderColor: 'rgba(143, 214, 207, 0.45)', backgroundColor: colors.aquaSoft },
  chipPressed: { backgroundColor: 'rgba(168, 208, 222, 0.08)' },
  chipCode: { fontFamily: fonts.mono, fontSize: 11.5, color: colors.text3 },
  chipName: { fontFamily: fonts.uiMedium, fontSize: 14, color: colors.text2 },
  form: { flexDirection: 'row', gap: 10 },
  input: {
    flex: 1, minHeight: touch, paddingHorizontal: 16, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.line,
    backgroundColor: 'rgba(8, 22, 32, 0.6)', color: colors.text, fontFamily: fonts.mono, fontSize: 15,
  },
  resultHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
})
