# Android app (React Native + Expo + TypeScript)

Source: `mobile/`. The app is a client of the Python backend; it holds no
strategy logic, no broker credentials and no risk logic. Every action
(execute, approve, mode change, kill switch) goes to the backend, which
re-runs its own risk checks — a button in the app cannot bypass them.

- App id `com.basavarajkumbar.gainzalgo`, name "GainzAlgo Pro Max", version
  1.0.0 (versionCode 1), deep-link scheme `gainzalgo`.
- Only permission: `INTERNET` (camera, location, storage, audio, overlay and
  vibrate are explicitly removed; verified in the prebuilt manifest).
- Sign-in tokens are kept in Android's encrypted keystore (`expo-secure-store`),
  never in plain storage; nothing is logged.
- Backend address: built in from `EXPO_PUBLIC_API_BASE_URL` (HTTPS) or entered on
  first start; no development IP is hard-coded. Release Android builds refuse
  plain-HTTP traffic.

## Screens

Splash · Login (with backend-address setup) · Command center · Scanner ·
Paper trading · Backtests · More · Market overview (with universe counts) ·
Stock universe · Signals · Signal detail · Open positions · Orders ·
Trade history · Backtest detail · Strategy analytics · Performance ·
Risk management · Notifications/approvals · System health ·
Strategy configuration · Settings · Zerodha connection — 23 screens, all
wired to real backend endpoints. Data is shown as the backend returns it;
simulated data is labelled "SIMULATED DATA"; an unreachable backend shows
"Backend unreachable" instead of numbers; nothing is invented on the phone.

## Zerodha on the phone

*Settings → Broker connections → Zerodha Kite Connect*: enter your own Kite
Connect API key and API secret (empty on a fresh install), save, then
**Connect to Zerodha**. The secret goes once over HTTPS to the backend, which
encrypts it; the app never stores or shows it again. Login happens on
Zerodha's own page; the backend exchanges the token and the app reopens via
`gainzalgo://zerodha`. Sessions expire daily: tap **Reconnect to Zerodha** —
credentials are not asked again. Details: [`zerodha-credentials.md`](./zerodha-credentials.md).

## Building the APK

This build environment cannot reach Google/Maven/Gradle/Expo hosts, so the
APK is built by GitHub Actions: `.github/workflows/android-apk.yml`
(manual run, pull requests touching `mobile/`, and `v*` tags).

1. Create a release keystore once, on your own computer, and keep it safe:
   `keytool -genkeypair -v -keystore gainzalgo-release.jks -alias gainzalgo -keyalg RSA -keysize 2048 -validity 10000`
2. In GitHub → Settings → Secrets and variables → Actions add secrets
   `ANDROID_KEYSTORE_BASE64` (`base64 -w0 gainzalgo-release.jks`),
   `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`,
   and the variable `EXPO_PUBLIC_API_BASE_URL` (your HTTPS backend).
3. Actions → android-apk → Run workflow. The artifact contains
   `GainzAlgo-Pro-Max.apk` and its SHA-256. Pushing a tag `v1.0.0` also
   attaches a release-signed APK to a GitHub Release.

Every push to `main` that changes `mobile/` (or the workflow) also builds
the APK. Without the signing secrets it is signed with a throw-away key and
published as a **pre-release** named `v1.0.0-build.N (test-signed)` — fine for
installing and trying the app, but a later release-signed APK cannot update it
(uninstall it first). With the secrets set, the build is release-signed.

Built APK (verified after download): package `com.basavarajkumbar.gainzalgo`,
version 1.0.0 (1), Android 7.0+ (minSdk 24, targetSdk 36), ARM 64/32-bit,
permission INTERNET only, APK signature schemes v1+v2+v3.

Install: copy the APK to the phone, allow "install unknown apps" for the file
manager, open it. Updates must be signed with the same keystore.

Local checks: `npm ci && npx tsc --noEmit && npx eslint . && npx jest`.
API types: `npm run gen:api` after regenerating `src/api/openapi.json`.
