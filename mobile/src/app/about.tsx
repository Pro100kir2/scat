import { useState } from 'react'
import { Pressable, StyleSheet, View } from 'react-native'
import { FAQ, howItWorks, scanCache } from '@scat/shared'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'
import { Block, Screen } from '@/components/ui/Screen'
import { Em, T } from '@/components/ui/T'
import { env } from '@/config/env'
import { openExternal } from '@/lib/links'
import { useScan } from '@/lib/scan'
import { colors, fonts, radius } from '@/theme'

/** "Как это работает" and the questions people ask before using Скат. */
export default function AboutScreen() {
  const state = useScan()
  const how = howItWorks(scanCache(state)?.total_nodes)
  const [open, setOpen] = useState<string | null>(null)

  return (
    <Screen padTop={false}>
      <Block gap={16}>
        <View style={{ gap: 6, marginTop: 16 }}>
          <T variant="eyebrow">Как это работает</T>
          <T variant="section" accessibilityRole="header">Три движения <Em>плавника</Em></T>
        </View>
        {how.steps.map((step, i) => (
          <View key={step.title} style={styles.step}>
            <T style={styles.num}>0{i + 1}</T>
            <View style={{ flex: 1, gap: 4 }}>
              <T variant="title">{step.title}</T>
              <T>{step.text}</T>
            </View>
          </View>
        ))}
        <View style={styles.notes}>
          {how.notes.map((note) => (
            <T key={note.lead}><T variant="strong">{note.lead}</T> {note.text}</T>
          ))}
        </View>

        <View style={{ gap: 6, marginTop: 12 }}>
          <T variant="eyebrow">Вопросы</T>
          <T variant="section" accessibilityRole="header">Прежде чем <Em>нырнуть</Em></T>
          <T>Коротко о том, что такое Скат, как подключить найденный VPN и чего от него ждать.</T>
        </View>
        <View style={styles.faq}>
          {FAQ.map((item) => {
            const expanded = open === item.id
            return (
              <View key={item.id} style={styles.item}>
                <Pressable
                  onPress={() => setOpen(expanded ? null : item.id)}
                  accessibilityRole="button"
                  accessibilityState={{ expanded }}
                  style={({ pressed }) => [styles.question, pressed && { opacity: 0.7 }]}
                  testID={`faq-${item.id}`}
                >
                  <T style={styles.q}>{item.question}</T>
                  <View style={{ transform: [{ rotate: expanded ? '180deg' : '0deg' }] }}>
                    <Icon name="chevron" color={colors.text2} />
                  </View>
                </Pressable>
                {expanded ? (
                  <View style={styles.answer}>
                    {item.answer.map((para, i) => <T key={i}>{para}</T>)}
                  </View>
                ) : null}
              </View>
            )
          })}
        </View>

        <Button icon="external" label="Открыть сайт Ската" onPress={() => void openExternal(env.siteUrl)} />
      </Block>
    </Screen>
  )
}

const styles = StyleSheet.create({
  step: { flexDirection: 'row', gap: 14 },
  num: { fontFamily: fonts.mono, fontSize: 13, color: colors.aqua, paddingTop: 4 },
  notes: { gap: 12, padding: 14, borderRadius: radius.md, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.sea },
  faq: { borderTopWidth: 1, borderColor: colors.line },
  item: { borderBottomWidth: 1, borderColor: colors.line },
  question: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, minHeight: 56, paddingVertical: 10 },
  q: { flex: 1, fontFamily: fonts.uiBold, fontSize: 16, lineHeight: 22, color: colors.text },
  answer: { gap: 10, paddingBottom: 16 },
})
