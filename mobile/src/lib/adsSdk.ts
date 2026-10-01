/**
 * The Google Mobile Ads module, loaded on first use. It has no native side on iOS (it is left
 * out of that build) and none under Jest, so nothing may import it at the top of a file.
 */
export const adsSdk = () =>
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  require('react-native-google-mobile-ads') as typeof import('react-native-google-mobile-ads')
