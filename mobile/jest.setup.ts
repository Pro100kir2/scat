// Native modules that have no implementation under Jest.
jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
)
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(async () => true) }))
