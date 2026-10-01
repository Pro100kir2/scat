import { useState, type ReactNode } from 'react'
import { StyleSheet, useWindowDimensions, View } from 'react-native'
import { countryName, type PearlNode } from '@scat/shared'
import { Button } from '@/components/ui/Button'
import { CopyButton } from '@/components/ui/CopyButton'
import { Sheet } from '@/components/ui/Sheet'
import { T } from '@/components/ui/T'
import { openInApp, shareText } from '@/lib/links'
import { colors, fonts, radius } from '@/theme'
import { NetworkAvailability } from './NetworkAvailability'
import { QrCode } from './QrCode'

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <View style={styles.fact}>
      <T variant="label">{label}</T>
      <T style={styles.factValue}>{children}</T>
    </View>
  )
}

/**
 * Everything about one configuration: the link exactly as the server gave it, copy, the
 * native share sheet, handing it to an installed VPN client, a QR code, and the details.
 */
export function ConfigSheet({ node, onClose }: { node: PearlNode | null; onClose: () => void }) {
  const { width } = useWindowDimensions()
  const [qr, setQr] = useState(false)
  const [noClient, setNoClient] = useState(false)
  // Keep the last node while the sheet slides away.
  const [shown, setShown] = useState<PearlNode | null>(node)
  if (node && node !== shown) {
    setShown(node)
    setQr(false)
    setNoClient(false)
  }

  const n = shown
  if (!n) return null
  const title = countryName(n.country) ?? n.address

  async function open() {
    setNoClient(!(await openInApp(n!.link)))
  }

  return (
    <Sheet visible={!!node} onClose={onClose} title={`Жемчужина № ${n.rank}`}>
      <View>
        <T variant="eyebrow">Жемчужина № {n.rank}</T>
        <T style={styles.title}>{title}</T>
      </View>

      <View style={styles.actions}>
        <CopyButton text={n.link} label="Копировать ссылку" variant="primary" testID="sheet-copy" />
        <Button label="Поделиться" icon="share" onPress={() => void shareText(n.link, `Скат: ${title}`)} testID="sheet-share" />
        <Button label="Открыть в VPN-клиенте" icon="external" onPress={open} testID="sheet-open" />
        <Button label={qr ? 'Скрыть QR-код' : 'QR-код'} icon="qr" onPress={() => setQr((v) => !v)} testID="sheet-qr" />
      </View>
      {noClient ? (
        <T variant="small" style={styles.warn} accessibilityRole="alert">
          На устройстве нет приложения, которое открывает ссылки vless://. Установите VPN-клиент (v2rayNG, Hiddify,
          Streisand) или скопируйте ссылку и добавьте её вручную.
        </T>
      ) : null}

      {qr ? (
        <View style={styles.qr}>
          <QrCode value={n.link} size={Math.min(260, width - 80)} />
          <T variant="muted" style={{ textAlign: 'center' }}>Отсканируйте камерой в v2rayNG, Hiddify, Streisand или другом клиенте.</T>
        </View>
      ) : null}

      <View style={styles.link}>
        <T variant="mono" selectable style={styles.linkText} testID="sheet-link">{n.link}</T>
      </View>

      <View style={styles.facts}>
        <Fact label="Адрес"><T variant="mono">{n.address}:{n.port}</T></Fact>
        <Fact label="Глубокая проверка">
          {n.stable ? 'Стабильна' : 'Нестабильна'} <T variant="mono" style={styles.dim}>{n.checks_passed}/{n.checks_total} проверок</T>
        </Fact>
        {n.probe_total ? (
          <Fact label="Подтверждено провайдерами">
            <T variant="mono">{n.probe_score}/{n.probe_total}</T>
            <T style={styles.dim}> — зонды в сетях провайдеров дотянулись до сервера</T>
          </Fact>
        ) : null}
        {n.latency_ms !== null ? <Fact label="Задержка"><T variant="mono">{n.latency_ms} мс</T></Fact> : null}
        {n.speed_mbps ? <Fact label="Скорость загрузки"><T variant="mono">{n.speed_mbps} Мбит/с</T></Fact> : null}
        {n.jitter_ms !== null ? <Fact label="Разброс задержки"><T variant="mono">± {n.jitter_ms} мс</T></Fact> : null}
        {n.sni ? <Fact label="Маскировка (SNI)"><T variant="mono">{n.sni}</T></Fact> : null}
        {n.flow ? <Fact label="Flow"><T variant="mono">{n.flow}</T></Fact> : null}
        {n.tls_fingerprint ? <Fact label="Отпечаток TLS"><T variant="mono">{n.tls_fingerprint}</T></Fact> : null}
        {n.label ? <Fact label="Метка источника">{n.label}</Fact> : null}
      </View>

      {n.reachability ? <NetworkAvailability rows={n.reachability} /> : null}
    </Sheet>
  )
}

const styles = StyleSheet.create({
  title: { fontFamily: fonts.display, fontSize: 30, lineHeight: 34, color: colors.text, marginTop: 2, paddingRight: 40 },
  actions: { gap: 10 },
  warn: { color: colors.sand },
  qr: { alignItems: 'center', gap: 10 },
  link: { padding: 12, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.deep },
  linkText: { fontSize: 12, lineHeight: 17, color: colors.text2 },
  facts: { gap: 10 },
  fact: { gap: 1 },
  factValue: { fontFamily: fonts.ui, fontSize: 15, lineHeight: 21, color: colors.text },
  dim: { color: colors.text3, fontSize: 13 },
})
