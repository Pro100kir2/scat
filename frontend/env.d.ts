/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend origin, e.g. https://api.example.com. Empty = same origin (dev proxy). */
  readonly VITE_API_URL?: string
  /** Public site origin for canonical and Open Graph URLs. */
  readonly VITE_SITE_URL?: string
  /** Donation page. The support button is hidden while this is empty. */
  readonly VITE_DONATE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
