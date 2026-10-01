// Metro for a project that shares code with the web app: ../shared holds the
// framework-independent logic (API client, scan state machine, ocean simulation).
const path = require('node:path')
const { getDefaultConfig } = require('expo/metro-config')

const shared = path.resolve(__dirname, '../shared')
const config = getDefaultConfig(__dirname)

config.watchFolders = [shared]
// Shared files have no node_modules of their own: Babel helpers come from this project's.
config.resolver.nodeModulesPaths = [path.resolve(__dirname, 'node_modules')]

const resolveDefault = config.resolver.resolveRequest
config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (moduleName === '@scat/shared' || moduleName.startsWith('@scat/shared/')) {
    const rest = moduleName.slice('@scat/shared'.length) || '/index'
    return context.resolveRequest(context, path.join(shared, 'src', rest), platform)
  }
  return (resolveDefault ?? context.resolveRequest)(context, moduleName, platform)
}

module.exports = config
