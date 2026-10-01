import { useState } from 'react'
import { Platform, StyleSheet, View } from 'react-native'
import { SUBSCRIPTION_CLIENTS } from '@scat/shared'
import { Button } from '@/components/ui/Button'
import { CopyButton } from '@/components/ui/CopyButton'
import { Block, Screen } from '@/components/ui/Screen'
import { Em, T } from '@/components/ui/T'
import { api } from '@/lib/api'
import { openInApp, shareText, SUBSCRIPTION_IMPORTERS } from '@/lib/links'
import { colors, fonts, radius } from '@/theme'

/** "Подписка": one link a VPN client keeps refreshing by itself. */
export default function SubscriptionScreen() {
  const url = api.subscriptionUrl()
  const [missing, setMissing] = useState<string | null>(null)
  const importers = SUBSCRIPTION_IMPORTERS.filter((i) => Platform.OS === 'web' || i.platforms.includes(Platform.OS as 'ios' | 'android'))

  return (
    <Screen>
      <Block gap={16}>
        <View style={{ gap: 6 }}>
          <T variant="eyebrow">Подписка</T>
          <T variant="section" accessibilityRole="header">Одна ссылка — <Em>всегда свежий</Em> улов</T>
          <T>
            Добавьте ссылку в VPN-клиент как подписку. Он будет сам забирать пять лучших жемчужин после каждого погружения.
          </T>
        </View>

        <View style={styles.box}>
          <T variant="mono" selectable style={styles.url} testID="sub-url">{url}</T>
          <CopyButton text={url} label="Копировать ссылку" variant="primary" testID="sub-copy" />
          <Button label="Поделиться" icon="share" onPress={() => void shareText(url, 'Подписка Ската')} testID="sub-share" />
        </View>

        <View style={{ gap: 10 }}>
          <T variant="strong">Добавить в клиент</T>
          {importers.map((client) => (
            <Button
              key={client.name}
              label={`Открыть в ${client.name}`}
              icon="external"
              onPress={async () => setMissing((await openInApp(client.link(url))) ? null : client.name)}
              testID={`sub-open-${client.name}`}
            />
          ))}
          {missing ? (
            <T variant="small" style={{ color: colors.sand }} accessibilityRole="alert">
              {missing} не установлен или не принимает такие ссылки. Скопируйте ссылку и добавьте её в клиенте вручную
              как подписку.
            </T>
          ) : null}
        </View>

        <View style={styles.clients}>
          <T variant="strong">Подходящие клиенты</T>
          {SUBSCRIPTION_CLIENTS.map((client) => (
            <View key={client.name} style={styles.client}>
              <T style={styles.clientName}>{client.name}</T>
              <T variant="muted">{client.where}</T>
            </View>
          ))}
          <T variant="muted">Скат не является VPN-клиентом: он только находит и отдаёт конфигурации.</T>
        </View>
      </Block>
    </Screen>
  )
}

const styles = StyleSheet.create({
  box: { gap: 10, padding: 14, borderRadius: radius.md, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.sea },
  url: { fontSize: 13, lineHeight: 19, color: colors.pearl },
  clients: { gap: 8, padding: 14, borderRadius: radius.md, borderWidth: 1, borderColor: colors.line },
  client: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 },
  clientName: { fontFamily: fonts.uiMedium, fontSize: 15, color: colors.text },
})
