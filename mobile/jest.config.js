// Jest for the app: jest-expo's preset, plus the shared package next to this project.
const preset = require('jest-expo/jest-preset')

// Packages that ship untranspiled ESM, added to the ones jest-expo already transforms.
const ESM = ['uqr', '@shopify/react-native-skia', 'standard-navigation']

module.exports = {
  preset: 'jest-expo',
  setupFiles: ['@shopify/react-native-skia/jestSetup.js'],
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  roots: ['<rootDir>/src'],
  // Files in ../shared have no node_modules of their own (Babel helpers come from here).
  modulePaths: ['<rootDir>/node_modules'],
  moduleNameMapper: {
    '^@scat/shared$': '<rootDir>/../shared/src/index.ts',
    '^@scat/shared/(.*)$': '<rootDir>/../shared/src/$1',
    '^@/(.*)$': '<rootDir>/src/$1',
  },
  transformIgnorePatterns: preset.transformIgnorePatterns.map((pattern) =>
    pattern.replace('node_modules/(?!', `node_modules/(?!${ESM.join('|')}|`)),
}
