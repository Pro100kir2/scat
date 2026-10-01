import Svg, { Path } from 'react-native-svg'
import { colors } from '@/theme'

/**
 * The site's icon set (frontend/src/components/ui/AppIcon.vue): 24-unit stroked glyphs.
 * Decorative: every icon sits inside a button or row that carries the accessible label.
 */
const PATHS = {
  copy: 'M9 9h10v10H9z M5 15V5h10',
  check: 'M5 12.5l4.2 4.2L19 7',
  chevron: 'M6 9l6 6 6-6',
  chevronRight: 'M9 6l6 6-6 6',
  arrowDown: 'M12 5v14 M6 13l6 6 6-6',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z M20 20l-4-4',
  qr: 'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h2v2h-2z M18 18h2v2h-2z M18 14h2 M14 18v2',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7 M20 5v6h-6',
  close: 'M6 6l12 12 M18 6L6 18',
  link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1 M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
  external: 'M14 5h5v5 M19 5l-8 8 M17 14v5H5V7h5',
  plus: 'M12 5v14 M5 12h14',
  share: 'M12 15V4 M8 8l4-4 4 4 M5 12v7h14v-7',
  globe: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M3 12h18 M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 11v6 M12 7.5v.5',
  pearl: 'M12 19a7 7 0 1 0 0-14 7 7 0 0 0 0 14z M9.5 9.5a3.5 3.5 0 0 1 2.5-1.3',
  signal: 'M5 18v-3 M10 18v-7 M15 18V8 M20 18V5',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 18, color = colors.text }: { name: IconName; size?: number; color?: string }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      <Path d={PATHS[name]} stroke={color} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </Svg>
  )
}

/** The manta seen from above: the Скат mark (frontend/src/components/brand/SkatMark.vue). */
export function SkatMark({ size = 28, color = colors.pearl }: { size?: number; color?: string }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 32 32">
      <Path
        fill={color}
        d="M16 8.4c-1.1 0-1.7-.9-2.1-2.4-.8.5-1.1 1.7-1.1 2.8C10 10.4 5.6 14 2.4 17.7c3.8-.3 7.8.5 10.5 2.7 1 .8 2.1 1.2 3.1 1.2s2.1-.4 3.1-1.2c2.7-2.2 6.7-3 10.5-2.7C26.4 14 22 10.4 19.2 8.8c0-1.1-.3-2.3-1.1-2.8-.4 1.5-1 2.4-2.1 2.4Z"
      />
      <Path d="M16 21.6c0 3 .2 5.6.9 8.4" fill="none" stroke={color} strokeWidth={1.1} strokeLinecap="round" />
    </Svg>
  )
}
