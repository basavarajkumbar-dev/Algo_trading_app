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

## Zerodha login on the phone

The API secret stays on the backend. The app opens
`GET /api/v1/broker/zerodha/login?client=mobile` in a system browser tab;
the user logs in on kite.zerodha.com; Kite redirects to the **backend**
callback (`KITE_REDIRECT_URL`, e.g. `https://api.your-domain.com/api/v1/broker/zerodha/callback`,
registered at developers.kite.trade); the backend exchanges the token and
returns to `gainzalgo://zerodha?result=…`. Kite tokens expire every day
(≈06:00 IST): use *Reconnect* each trading day.

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

Without the signing secrets the workflow still builds, but signs with a
throw-away key (test installs only; never attached to a release).

Install: copy the APK to the phone, allow "install unknown apps" for the file
manager, open it. Updates must be signed with the same keystore.

Local checks: `npm ci && npx tsc --noEmit && npx eslint . && npx jest`.
API types: `npm run gen:api` after regenerating `src/api/openapi.json`.
