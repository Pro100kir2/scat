import { createApi } from '@scat/shared'
import { env } from '@/config/env'

/** The shared Скат API client, bound to this build's API origin. UI never calls fetch itself. */
export const api = createApi({ baseUrl: env.apiUrl })
