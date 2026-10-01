import type { ReactNode } from 'react'
import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import { colors, fonts, radius, touch } from '@/theme'
import { Icon, type IconName } from './Icon'
import { T } from './T'

type Variant = 'default' | 'primary' | 'quiet'

interface Props {
  label: string
  onPress: () => void
  variant?: Variant
  icon?: IconName
  small?: boolean
  disabled?: boolean
  /** Read by screen readers instead of the label, when the label alone is not enough. */
  accessibilityLabel?: string
  accessibilityHint?: string
  style?: StyleProp<ViewStyle>
  trailing?: ReactNode
  testID?: string
}

/** The site's pill button: outlined by default, pearl-white when primary, bare when quiet. */
export function Button({ label, onPress, variant = 'default', icon, small, disabled, accessibilityLabel, accessibilityHint, style, trailing, testID }: Props) {
  const fg = variant === 'primary' ? colors.ink : variant === 'quiet' ? colors.text2 : colors.text
  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: !!disabled }}
      hitSlop={small ? 6 : 0}
      style={({ pressed }) => [
        styles.base,
        small && styles.small,
        variant === 'primary' && styles.primary,
        variant === 'quiet' && styles.quiet,
        pressed && (variant === 'primary' ? styles.primaryPressed : styles.pressed),
        disabled && styles.disabled,
        style,
      ]}
    >
      {icon ? <Icon name={icon} size={small ? 16 : 18} color={fg} /> : null}
      <T style={[styles.label, small && styles.labelSmall, { color: fg }]} numberOfLines={1}>{label}</T>
      {trailing ? <View>{trailing}</View> : null}
    </Pressable>
  )
}

const styles = StyleSheet.create({
  base: {
    minHeight: touch,
    paddingHorizontal: 20,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 9,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.lineStrong,
  },
  small: { minHeight: 38, paddingHorizontal: 14 },
  primary: { backgroundColor: colors.text, borderColor: 'transparent' },
  quiet: { borderColor: 'transparent', paddingHorizontal: 12 },
  pressed: { backgroundColor: 'rgba(168, 208, 222, 0.1)', borderColor: 'rgba(168, 208, 222, 0.38)' },
  primaryPressed: { backgroundColor: '#ffffff', transform: [{ scale: 0.98 }] },
  disabled: { opacity: 0.55 },
  label: { fontFamily: fonts.uiBold, fontSize: 15, lineHeight: 20 },
  labelSmall: { fontSize: 14 },
})
