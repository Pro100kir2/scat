/**
 * Скат's visual identity for native UI. The same tokens as the site's stylesheet
 * (frontend/src/styles/main.css): water from the sunlit surface down to the abyss,
 * calm aqua for interaction, pearl for discovery, coral for trouble.
 */
export const colors = {
  abyss: '#050d14',
  deep: '#07141d',
  sea: '#0b1c27',
  sea2: '#102634',
  sea3: '#163245',
  line: 'rgba(168, 208, 222, 0.1)',
  lineStrong: 'rgba(168, 208, 222, 0.2)',
  text: '#e6eef1',
  text2: '#a4b8c2',
  text3: '#6f8795',
  aqua: '#8fd6cf',
  aquaSoft: 'rgba(143, 214, 207, 0.12)',
  pearl: '#f3e9d8',
  pearl2: '#dcccb4',
  coral: '#ec9a86',
  coralSoft: 'rgba(236, 154, 134, 0.12)',
  sand: '#dfc189',
  sandSoft: 'rgba(223, 193, 137, 0.1)',
  ink: '#06131b',
  scrim: 'rgba(3, 10, 16, 0.72)',
} as const

/** Loaded in src/app/_layout.tsx; names are the ones expo-google-fonts registers. */
export const fonts = {
  ui: 'Manrope_400Regular',
  uiMedium: 'Manrope_500Medium',
  uiBold: 'Manrope_600SemiBold',
  display: 'CormorantGaramond_500Medium',
  displayItalic: 'CormorantGaramond_500Medium_Italic',
  mono: 'JetBrainsMono_400Regular',
} as const

export const radius = { sm: 10, md: 16, lg: 22, pill: 999 } as const
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const
/** Screen gutter; matches the site's --gutter on narrow screens. */
export const gutter = 18
/** Minimum touch target (Apple HIG 44pt, Material 48dp). */
export const touch = 46
/** Content never grows wider than this on tablets. */
export const maxContentWidth = 640
