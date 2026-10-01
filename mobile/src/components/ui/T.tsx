import { Text, type TextProps, type TextStyle } from 'react-native'
import { colors, fonts } from '@/theme'

type Variant = 'body' | 'small' | 'muted' | 'eyebrow' | 'mono' | 'title' | 'display' | 'section' | 'label' | 'strong'

const VARIANTS: Record<Variant, TextStyle> = {
  body: { fontFamily: fonts.ui, fontSize: 16, lineHeight: 24, color: colors.text2 },
  small: { fontFamily: fonts.ui, fontSize: 14, lineHeight: 20, color: colors.text2 },
  muted: { fontFamily: fonts.ui, fontSize: 13, lineHeight: 18, color: colors.text3 },
  strong: { fontFamily: fonts.uiBold, fontSize: 16, lineHeight: 22, color: colors.text },
  label: { fontFamily: fonts.ui, fontSize: 12, lineHeight: 16, color: colors.text3 },
  eyebrow: { fontFamily: fonts.mono, fontSize: 11.5, lineHeight: 16, letterSpacing: 1.6, textTransform: 'uppercase', color: colors.text3 },
  mono: { fontFamily: fonts.mono, fontSize: 13.5, lineHeight: 19, color: colors.text },
  title: { fontFamily: fonts.uiBold, fontSize: 18, lineHeight: 24, color: colors.text },
  section: { fontFamily: fonts.display, fontSize: 32, lineHeight: 35, color: colors.text },
  display: { fontFamily: fonts.display, fontSize: 40, lineHeight: 42, color: colors.text },
}

/**
 * All text in the app. Follows the system font size up to 1.35× (beyond that the cards
 * would stop fitting a phone screen).
 */
export function T({ variant = 'body', style, ...rest }: TextProps & { variant?: Variant }) {
  return <Text maxFontSizeMultiplier={1.35} {...rest} style={[VARIANTS[variant], style]} />
}

/** The italic pearl-coloured half of a headline: "Жемчужины <Em>дня</Em>". */
export function Em(props: TextProps) {
  return <Text {...props} style={[{ fontFamily: fonts.displayItalic, color: colors.pearl }, props.style]} />
}
