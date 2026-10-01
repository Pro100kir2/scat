// The AdMob application ID is a manifest value, fixed at build time. Debug builds get Google's
// sample App ID and release builds the real one, through a Gradle manifest placeholder per build
// type (the manifest holds ${admobAppId}, see app.config.ts). Ad unit IDs are chosen in JS
// (src/config/admob.ts) by the same rule.
const { withAppBuildGradle } = require('expo/config-plugins')
const ids = require('../admob.json')

const MARK = '// admob-app-id'

module.exports = (config) =>
  withAppBuildGradle(config, (c) => {
    let gradle = c.modResults.contents
    if (gradle.includes(MARK)) return c
    const add = (type, appId) => {
      const re = new RegExp(`(buildTypes\\s*\\{[\\s\\S]*?\\n\\s{8}${type}\\s*\\{\\n)`)
      if (!re.test(gradle)) throw new Error(`withAdMobAppId: build type "${type}" not found in app/build.gradle`)
      gradle = gradle.replace(re, `$1            manifestPlaceholders = [admobAppId: "${appId}"] ${MARK}\n`)
    }
    add('debug', ids.test.androidAppId)
    add('release', ids.production.androidAppId)
    c.modResults.contents = gradle
    return c
  })
