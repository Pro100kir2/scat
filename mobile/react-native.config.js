// AdMob is Android-only for now (no iOS AdMob IDs exist yet): keep the library out of the iOS
// build entirely. Remove this block when the iOS App ID and ad units are created.
module.exports = {
  dependencies: {
    'react-native-google-mobile-ads': { platforms: { ios: null } },
  },
}
