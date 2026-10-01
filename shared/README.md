# @scat/shared

Framework-independent TypeScript used by both Скат clients: the Vue site (`frontend/`) and the
React Native app (`mobile/`). No dependencies, nothing from Vue, React or the DOM beyond `fetch`.

| Module | What |
|---|---|
| `api.ts` | `createApi({ baseUrl })`: the public API client, `ApiError` kinds, payload checks, retries |
| `types.ts` | API types |
| `scan.ts` | `createScanStore`: the scan state machine (resting / diving / surfaced, live or replay) |
| `scanView.ts` | wording both clients show: dive log, result phases, how-it-works, cooldown helpers |
| `format.ts` | numbers, dates, Russian plurals, country names, latency quality |
| `reach.ts`, `probe.ts` | provider reachability states, probe verification wording |
| `content/faq.ts` | FAQ (also emitted as structured data by the site's build) |
| `ocean/` | the ocean scene's behaviour: manta, pearls, seascape, `OceanSim`. No drawing: the site renders it with Canvas 2D, the app with Skia |

Imported as `@scat/shared` and `@scat/shared/ocean` through a path alias in each client
(Vite + tsconfig in `frontend/`; Metro, tsconfig and Jest in `mobile/`). It is not published and
has no build step: each client compiles it with its own toolchain and type-checks it with its own
`tsc`.

Tests live with the clients: `frontend/src/__tests__` (API client, scan state machine, formatting,
ocean behaviour; `cd frontend && npm test`) and `mobile/src/__tests__`.

When changing an API type or rule here, run both: `cd frontend && npm run typecheck && npm test`
and `cd mobile && npm run typecheck && npm test`.
