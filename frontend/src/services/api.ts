/**
 * The site's API client: the shared client (@scat/shared) bound to this build's API origin.
 * Everything else about requests, errors and payload checks lives in the shared package.
 */
import { createApi } from '@scat/shared'
import { env } from '@/config/env'

export { ApiError, type ApiErrorKind } from '@scat/shared'

export const api = createApi({ baseUrl: () => env.apiUrl })

export const {
  fetchResults,
  fetchStatus,
  requestScan,
  requestRefresh,
  searchCountry,
  fetchProviders,
  addProvider,
  fetchEnrollment,
  fetchReleases,
} = api
