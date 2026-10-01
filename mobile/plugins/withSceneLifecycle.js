// iOS 27 aborts at launch unless the app adopts the UIScene life cycle. Expo SDK 57 ships the
// scene delegate (ExpoAppSceneDelegate) but its prebuild template does not wire it up yet, so
// this plugin does: scene manifest in Info.plist, the app delegate provides the React Native
// factory (React Native starts from the scene), and a SceneDelegate subclass.
// Remove it once the Expo template does this itself.
const fs = require('fs')
const path = require('path')
const { withInfoPlist, withDangerousMod } = require('expo/config-plugins')

const withSceneManifest = (config) =>
  withInfoPlist(config, (c) => {
    c.modResults.UIApplicationSceneManifest = {
      UIApplicationSupportsMultipleScenes: false,
      UISceneConfigurations: {
        UIWindowSceneSessionRoleApplication: [
          {
            UISceneConfigurationName: 'Default Configuration',
            UISceneDelegateClassName: '$(PRODUCT_MODULE_NAME).SceneDelegate',
          },
        ],
      },
    }
    return c
  })

const withSceneAppDelegate = (config) =>
  withDangerousMod(config, [
    'ios',
    (c) => {
      const dir = path.join(c.modRequest.platformProjectRoot, c.modRequest.projectName)
      const file = path.join(dir, 'AppDelegate.swift')
      let src = fs.readFileSync(file, 'utf8')
      if (src.includes('ExpoAppSceneDelegate')) return c
      const start = src.indexOf('#if os(iOS) || os(tvOS)\n    window = UIWindow')
      const end = src.indexOf('    return super.application(application, didFinishLaunchingWithOptions')
      if (!src.includes('class AppDelegate: ExpoAppDelegate {') || start < 0 || end < start) {
        throw new Error('withSceneLifecycle: the generated AppDelegate.swift has an unexpected shape')
      }
      src = src.replace('class AppDelegate: ExpoAppDelegate {', 'class AppDelegate: ExpoAppDelegate, ExpoReactNativeFactoryProvider {')
      src = src.slice(0, src.indexOf('#if os(iOS) || os(tvOS)\n    window = UIWindow')) +
        src.slice(src.indexOf('    return super.application(application, didFinishLaunchingWithOptions'))
      src += '\nclass SceneDelegate: ExpoAppSceneDelegate {}\n'
      fs.writeFileSync(file, src)
      return c
    },
  ])

module.exports = (config) => withSceneAppDelegate(withSceneManifest(config))
