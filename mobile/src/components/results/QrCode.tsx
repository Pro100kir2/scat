import { useMemo } from 'react'
import { View } from 'react-native'
import Svg, { Path, Rect } from 'react-native-svg'
import { qrPath } from '@/lib/qr'
import { colors } from '@/theme'

/** A QR code drawn on the device, in the site's pearl-on-ink colours (high contrast for cameras). */
export function QrCode({ value, size = 240 }: { value: string; size?: number }) {
  const { d, size: modules } = useMemo(() => qrPath(value), [value])
  return (
    <View accessible accessibilityRole="image" accessibilityLabel="QR-код конфигурации" style={{ borderRadius: 14, overflow: 'hidden' }} testID="qr-code">
      <Svg width={size} height={size} viewBox={`0 0 ${modules} ${modules}`}>
        <Rect x={0} y={0} width={modules} height={modules} fill={colors.pearl} />
        <Path d={d} fill={colors.ink} />
      </Svg>
    </View>
  )
}
