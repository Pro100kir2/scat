# Скат for iOS and Android

One React Native + TypeScript codebase (Expo SDK 57, Expo Router, Skia) for both platforms. It is
a client of the existing Скат public API: it shows the catch, runs a search, searches by country,
gives the subscription link and shows provider reachability. It is **not** a VPN client, contains
no VPN/tunnel code, and has nothing of the admin panel.

- [Architecture](#architecture)
- [Local development](#local-development)
- [Environment configuration](#environment-configuration)
- [Testing and checks](#testing-and-checks)
- [The ocean](#the-ocean)
- [Before the first release](#before-the-first-release)
- [Android release](#android-release)
- [iOS release](#ios-release)
- [Store submission](#store-submission)
- [Submission risk: VLESS / VPN content](#submission-risk-vless--vpn-content)
- [Known limitations](#known-limitations)

## Architecture

```
scat/
├── shared/            framework-independent TypeScript, used by BOTH clients
│   └── src/
│       ├── api.ts         the public API client (createApi), ApiError, payload checks
│       ├── types.ts       API types
│       ├── scan.ts        the scan state machine (createScanStore)
│       ├── scanView.ts    wording: dive log, result phases, how-it-works, cooldown helpers
│       ├── format.ts      numbers, dates, Russian plurals, country names, quality tiers
│       ├── reach.ts       provider reachability states
│       ├── probe.ts       probe verification wording
│       ├── content/faq.ts FAQ
│       └── ocean/         the ocean simulation: manta, pearls, seascape, behaviour (no drawing)
├── frontend/          the Vue site: Vue adapters + a Canvas 2D renderer over shared/
└── mobile/            this app: React hooks + a Skia renderer over shared/
    ├── app.config.ts      name, identifiers, icons, splash, permissions, URL scheme
    ├── eas.json           build profiles (development / preview / production)
    ├── metro.config.js    lets Metro bundle ../shared
    └── src/
        ├── app/           screens (Expo Router): (tabs)/index, countries, subscription, providers; about
        ├── components/    ui kit, result card and sheet, hero
        ├── lib/           api binding, scan hook, links (copy/share/open), QR, cooldown, country search
        ├── ocean/         SkiaRenderer + OceanCanvas (the frame loop)
        ├── config/env.ts  public configuration with the release guard
        └── theme.ts       the site's colours, fonts and spacing
```

Rules that keep it maintainable:

- **Business rules live in `shared/`**, once. Screens never call `fetch`; they use `src/lib/api.ts`
  (the shared client bound to this build's API origin) and `useScan()` (the shared state machine).
- **UI is platform-specific.** Nothing in `shared/` imports Vue, React or React Native.
- **State**: two small external stores read with `useSyncExternalStore` (scan) and local hooks
  (country search, cooldown). No Redux or other state framework.
- The shared code is imported as `@scat/shared` (and `@scat/shared/ocean`); the alias is defined in
  `tsconfig.json`, `metro.config.js` and `jest.config.js`. `shared/` has no dependencies.

Screens (four tabs and one pushed screen):

| Screen | What |
|---|---|
| **Улов** | the ocean, «Начать поиск», «Очистить кэш» with its countdown, the dive log, today's configs; pull to reload; tap a card or a pearl in the ocean for details |
| details sheet | the link exactly as the server sent it, copy, native share, «Открыть в VPN-клиенте», QR code, facts, reachability per provider |
| **Страны** | configs that exit in one country: quick picks or an ISO code; waits while the server searches |
| **Подписка** | the subscription URL: copy, share, open in Hiddify / v2rayNG / Streisand |
| **Провайдеры** | providers and their probes' status; adding a provider opens the website |
| О Скате | how it works and the FAQ (the ⓘ button on the first screen) |

API endpoints used (all public, all existing; nothing was added to the backend):
`GET /api/results`, `GET /api/scan/status`, `POST /api/scan`, `POST /api/cache/refresh`,
`GET /api/vless/search/{country}`, `GET /api/providers`, and the URL `GET /api/vless/sub` (given to
VPN clients, not fetched by the app).

## Local development

Requirements: Node 20+, and for devices/simulators Xcode (iOS) and/or Android Studio (Android).

```bash
cd mobile
npm install
npm start            # Metro; press i / a for a simulator, or scan the QR with a development build
```

The app uses native modules that Expo Go does not include (Skia), so use a **development build**:

```bash
npx expo run:ios        # needs Xcode
npx expo run:android    # needs Android Studio / SDK
# or in the cloud:  npx eas-cli@latest build --profile development --platform all
```

Browser preview (development only; handy for layout work, the ocean runs on CanvasKit):

```bash
npm run web
```

## Environment configuration

Only public, client-safe values exist; everything prefixed `EXPO_PUBLIC_` is compiled into the
bundle. There are no secrets in this app and none must be added.

| Variable | Default | Meaning |
|---|---|---|
| `EXPO_PUBLIC_API_URL` | `https://api.scat-search.ru` | Скат API origin |
| `EXPO_PUBLIC_SITE_URL` | `https://scat-search.ru` | the site (adding a provider, the probe guide) |

For a backend on your machine, copy `.env.example` to `.env.local` and use your computer's LAN
address (a phone cannot reach `localhost`).

**A release build cannot point at a development host**: `src/config/env.ts` accepts only a public
`https://` origin in release mode and otherwise falls back to production (tested in
`src/__tests__/env.test.ts`). The EAS profiles in `eas.json` set the production URLs explicitly.

## Testing and checks

```bash
npm test              # Jest: 32 tests, no network, no production backend
npm run typecheck     # tsc --noEmit (also type-checks ../shared)
npm run lint          # expo lint
npm run doctor        # expo-doctor: dependency and config checks
```

| Tests | Cover |
|---|---|
| `screens.test.tsx` | the real router and screens with a mocked API: loading, results, empty, error + retry, a scan in progress, starting a search, tabs, about/FAQ, subscription, providers, country search (202 → 200, busy server) |
| `ConfigSheet.test.tsx` | the link shown exactly, copy and share of the untouched link, opening in a client and the "no client" message, QR on request, the card's accessibility summary |
| `links.test.ts` | copy / share / open pass the link byte for byte; client import links; the QR path equals the encoder's modules |
| `env.test.ts` | the release guard for API origins |
| `ocean.test.ts` | the Skia renderer drawing a whole dive with real Skia (CanvasKit) |

The shared logic (API client, scan state machine, formatting, ocean behaviour) is tested from the
web project, which also uses it: `cd ../frontend && npm test` (89 tests).

## The ocean

The site draws the ocean with the browser's Canvas 2D API, which does not exist in React Native.
The scene was split: its **behaviour** (the manta's swimming model and plans, digging pearls out,
sonar waves, particles, the rule that the manta never leaves the screen) moved to
`shared/src/ocean` and is the same code on both clients; each client has its own **renderer**.
Here it is `src/ocean/SkiaRenderer.ts`:

- static layers (water gradient, light shafts, far rocks, seabed with rocks) are recorded once
  per size as Skia pictures and replayed with a parallax offset;
- glows (pearls, sparkles, sand clouds, the manta's shadow) reuse a few unit-sized gradient
  shaders placed with canvas transforms, instead of a new gradient per object per frame;
- the manta's body, shoulder patches, rim light and tail, kelp, marine snow, bubbles, the far
  school of fish, sonar rings, the pearl's nacre/bloom/burial and the vignette are all drawn.

Battery and smoothness: the scene runs at 30 fps (`OCEAN_FPS`), and stops completely when the app
is in the background, another tab is open, or the scene is scrolled out of view. With the system
"Reduce Motion" setting it becomes a still, composed scene (pearls appear at once).

Differences from the site, on purpose:

| Site | App | Why |
|---|---|---|
| layers shift with the mouse pointer | the layers sway slowly on their own | a phone has no pointer |
| hovering a pearl highlights it | tapping a pearl opens that config | no hover on touch |
| 60 fps | 30 fps | battery; the motion is slow and stays fluid |
| full scene on wide screens | the lighter variant on phones (fewer particles), the full one on tablets | same rule the site uses for small screens |

## Before the first release

1. **Identifiers.** `app.config.ts` uses `ru.scatsearch.app` for both the iOS bundle identifier
   and the Android application ID. They are **permanent once published**. Change them now if you
   want others.
2. **Expo account.** `npm i -g eas-cli` (or use `npx eas-cli@latest`), `eas login`, then in
   `mobile/`: `eas init` (creates the project and writes its ID into the config).
3. **Icons and splash** are generated from the Скат mark: `npm run assets` rewrites
   `assets/*.png`. Replace them with your own artwork if you prefer (icon 1024×1024 without
   transparency; Android foreground inside the central 66 %).
4. **Version.** `version` in `app.config.ts` is what users see. Build numbers are managed by EAS
   (`appVersionSource: remote`, `autoIncrement` in the production profile).

Nothing that signs the app is in the repository, and `.gitignore` blocks keystores, certificates,
provisioning profiles, `.p8`/`.p12` keys and `credentials.json`.

## Android release

Recommended: EAS Build (no Android Studio needed).

```bash
cd mobile
eas build --platform android --profile production     # → an .aab (Android App Bundle)
```

- **Signing.** On the first build EAS offers to generate an upload keystore and keeps it in your
  Expo account. To use your own: `eas credentials` → Android → *Set up a new keystore* / upload.
  Download a backup with `eas credentials`; never commit it. In Play Console enable **Play App
  Signing** (default for new apps): Google holds the app signing key, your keystore is the upload key.
- **Test build** you can install directly: `eas build --platform android --profile preview` (APK).
- **Local alternative** (needs Android Studio + JDK 17): `npx expo prebuild --platform android`,
  create a keystore with `keytool`, put its path and passwords in `~/.gradle/gradle.properties`
  (not in the project), reference them in `android/app/build.gradle` `signingConfigs.release`,
  then `cd android && ./gradlew bundleRelease`. `android/` is generated and git-ignored.
- Config (from `app.config.ts`): application ID `ru.scatsearch.app`, min SDK 24, target/compile
  SDK 36 (Expo SDK 57 defaults), adaptive icon with a monochrome layer, `scat://` deep links.
- **Permissions:** the app itself needs only `INTERNET`. Camera, microphone, storage, overlay and
  vibration are explicitly blocked in the config. The AdMob SDK (see "AdMob") adds
  `ACCESS_NETWORK_STATE`, `WAKE_LOCK`, `FOREGROUND_SERVICE`, `com.google.android.gms.permission.AD_ID`
  and the `ACCESS_ADSERVICES_*` ones; declare the advertising ID in Play Console. Debug builds also
  merge Android's standard debug manifest (`SYSTEM_ALERT_WINDOW`, cleartext traffic for Metro);
  that never reaches a release build.

## AdMob (Android only, no ads shown yet)

`react-native-google-mobile-ads` is linked on Android only (`react-native.config.js` keeps it out of
the iOS build until iOS IDs exist). `admob.json` is the single source for the IDs: Scat's App ID and
banner / native / app-open units, and Google's official sample IDs. Debug builds get the sample App
ID (a Gradle manifest placeholder per build type, `plugins/withAdMobAppId.js`) and the sample units
(`src/config/admob.ts`, by `__DEV__`); release builds get Scat's. `initializeAds()` (`src/lib/ads.ts`)
only starts the SDK at launch; nothing requests or shows an ad, and there are no rewarded ads.
Before showing ads: add the UMP consent flow for the EEA/UK (`AdsConsent`), the Play Data safety and
Advertising ID declarations, and an `app-ads.txt` on `scat-search.ru`.

## iOS release

Requires an Apple Developer Program membership.

```bash
cd mobile
eas build --platform ios --profile production      # signs and builds the .ipa in the cloud
eas submit --platform ios --profile production     # uploads it to App Store Connect
```

- **Signing.** `eas build` asks to sign in with your Apple ID and creates the distribution
  certificate and provisioning profile for you (or use `eas credentials` to supply your own).
  They are stored in your Expo account, not in the repository.
- **App Store Connect.** Create the app record first (My Apps → +) with bundle ID
  `ru.scatsearch.app`; `eas submit` asks for the app if it cannot find it. For unattended submits
  create an App Store Connect API key (Users and Access → Integrations) and give it to
  `eas credentials`.
- **Local alternative** (needs a Mac with full Xcode): `npx expo prebuild --platform ios`,
  `cd ios && pod install`, open `ios/*.xcworkspace` in Xcode, select your Team under Signing &
  Capabilities, then Product → Archive → Distribute App → App Store Connect.
- Config: bundle identifier `ru.scatsearch.app`, deployment target iOS 16.4 (Expo SDK 57 default),
  iPad supported, dark interface, URL schemes `scat://` and `ru.scatsearch.app://` (the latter added by Expo Router), `ITSAppUsesNonExemptEncryption = false`
  (the app only uses standard HTTPS, which is exempt: answer the export-compliance question
  accordingly, and review this if you ever add other cryptography).
- **Permissions:** none. The app has no usage-description strings because it requests nothing.

## Store submission

What you have to provide (none of it can be invented here):

**Both stores**

- App name, short and full description, keywords/category, support URL or e-mail.
- **A privacy policy URL** (required by both stores). It must truthfully describe: that the app
  itself asks for no account and no device permissions; what your **server** logs when the app
  calls the API (at minimum IP addresses and request times in web-server logs, and how long you
  keep them); that copied/shared configurations lead to third-party servers you do not operate.
  Write it from how your deployment actually handles logs.
- Screenshots: take them from a simulator or device (`npx expo run:ios` / `run:android`).
  App Store: 6.9" and 6.5" iPhone sets (and 13" iPad because `supportsTablet` is on; turn it off
  in `app.config.ts` if you do not want to ship for iPad). Google Play: at least 2 phone
  screenshots, a 512×512 icon and a 1024×500 feature graphic.
- Age rating questionnaires.

**Google Play**

1. Play Console → Create app (default language, free).
2. App content: privacy policy, ads declaration (the app has none), **Data safety** form
   (declare what the server collects, e.g. IP address in logs, if you consider it collected),
   target audience, content rating.
3. Testing → Internal testing → create a release → upload the `.aab` (or `eas submit --platform android`
   with a Google service-account key configured through `eas credentials`).
4. Promote to closed/open testing or production. New personal developer accounts must run a
   closed test with testers before production access: check the current requirement in the console.

**App Store**

1. App Store Connect → the app → fill App Information, Pricing (free), App Privacy
   ("nutrition labels": declare what the server collects).
2. Upload the build (`eas submit` or Xcode), attach it to a version, fill "What's New".
3. App Review information: notes for the reviewer explaining what the app does (see the next
   section), contact details. No demo account is needed (there is no login).
4. Submit for review.

## Submission risk: VLESS / VPN content

No approval can be promised. The app is not a VPN client (no tunnel, no Network Extension, no VPN
entitlement, no VpnService), but it lists third-party proxy configurations, which reviewers may
treat as VPN-related content. Check before submitting:

- **Apple, App Review Guideline 5.4 (VPN Apps)** and **5.2.x / 1.x** on content you do not
  control. 5.4 is written for apps that provide VPN service; decide with the guideline text in
  front of you how you describe the app, and say plainly in the review notes that it does not
  create VPN connections and only shows publicly available configuration links.
- **Regional availability.** Apple has removed VPN-related apps from the storefronts of some
  countries (Russia among them) at the request of local regulators. Decide which storefronts
  you list in, and expect that availability may change.
- **Google Play, "VpnService" and "Device and Network Abuse" policies.** The app does not use
  `VpnService`; the policy on facilitating access to third-party services and the Data safety
  declarations still apply.
- **Local law** on distributing tools or information for circumventing blocking in the countries
  you publish in. This needs a lawyer's view, not a developer's.
- **Claims in the listing.** Describe the servers as third-party and unaudited (the app's own FAQ
  does); do not promise privacy, anonymity or availability you cannot guarantee.
- The app has **no donation link** (the site's optional one was left out): both stores restrict
  external payment links.

## Known limitations

- **Native builds.** Verified on an iPhone 18 Pro / iOS 27 simulator with Xcode 27, from a clean
  `expo prebuild` of the repository files: the **Debug** build (Metro) and the **Release** build
  (embedded JS bundle, unsigned, no Metro) compile, launch and draw the first screen with the
  ocean. iOS 27 aborts apps that do not use the UIScene life cycle; Expo SDK 57's template does not
  wire it up yet, so `plugins/withSceneLifecycle.js` (listed in `app.config.ts`) adds the scene
  manifest and a `SceneDelegate` at prebuild. Remove it when the Expo template does this itself.
  **Not built:** Android (no SDK/JDK was available), signed/App Store iOS builds, any EAS build.
- **The yellow "Open debugger to view warnings" box** in a Debug build is React Native's LogBox
  showing the app's own development-only `console.warn` (`src/lib/diagnostics.ts`) when an API call
  fails. It names the failure type, host, endpoint and HTTP status, never request bodies or links;
  release builds neither log nor show it. If the app says «Скат не отвечает» during development,
  read that line in the Metro output: a `network` failure with a DNS/host-not-found text means the
  machine running the simulator cannot reach the API (a VPN/proxy tunnel on the Mac is the usual
  cause), not that the app or the backend is broken.
- **Ocean performance is unmeasured on real phones.** The frame is recorded on the JS thread
  (a few hundred draw calls); if a low-end Android device struggles, lower `OCEAN_FPS` in
  `src/ocean/types.ts`.
- **Client import links** (`hiddify://`, `v2rayng://`, `streisand://` in `src/lib/links.ts`) follow
  those apps' documented URL schemes but were not exercised on devices. If a client is not
  installed or rejects the link, the app says so and the user can copy or share instead.
- **Adding a provider** opens the website: a probe is installed on a router or computer, not a phone.
- The site's search/sort/filter toolbar is not in the app: the site shows it only for more than 8
  results, and the API serves 5.
- Russian only, like the site.
- Universal/App Links for `https://scat-search.ru` are not configured (they need association files
  on the site); `scat://` links work.
