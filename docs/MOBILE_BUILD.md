# Eternal mobile build workflow

Build Android/iOS binaries from a dedicated git worktree. Do not build from the mixed WIP checkout at `C:\Projects\eternal-packaging`.

Dedicated build worktree:

`C:\Projects\eternal-packaging-mobile-build`

The app is linked to the existing EAS project `22d68768-64c1-4f3e-ab6a-a217157fabb5` on account `eternal-packaging`, slug `prasetya17`. Do not create a new Expo/EAS project. The on-device app name stays Eternal Packaging.

Preview and production binaries use:

`EXPO_PUBLIC_API_URL=https://produksi.eternalpackaging.id`

Android package and iOS bundle identifier stay `com.eternalpackaging.mobile`. App version stays in `mobile/app.json` (`1.0.0`). Android `versionCode` and iOS `buildNumber` are managed by EAS remote versioning and auto-increment on the `production` profile.

Credentials (keystore, Apple certs, tokens) stay on EAS remote. Never commit them.

## A. First time on a PC

1. Log in to EAS once (do not store the password in git):

   ```powershell
   cd C:\Projects\eternal-packaging-mobile-build\mobile
   npx eas-cli@latest login
   ```

2. Create the dedicated build worktree once from the main repo:

   ```powershell
   cd C:\Projects\eternal-packaging
   git fetch origin
   git worktree add C:\Projects\eternal-packaging-mobile-build origin/main
   ```

   If that folder already exists as a worktree, skip this step.

## B. Every later build

From any directory:

```powershell
cd C:\Projects\eternal-packaging-mobile-build
.\scripts\mobile-build-android.ps1
```

The script fetches `origin`, requires a clean tree on latest `origin/main`, runs `npm ci` and `expo-doctor`, checks Expo SDK 54 and the production API URL, then starts:

`npx eas-cli@latest build -p android --profile preview`

Optional iOS preview (Apple credentials remain on EAS; the script will not ask you to save them in the repo):

```powershell
cd C:\Projects\eternal-packaging-mobile-build
.\scripts\mobile-build-ios.ps1
```

If EAS is not logged in, the script prints the login command and stops.

## C. Do not build from the mixed WIP repo

Never run these scripts in `C:\Projects\eternal-packaging` while feature work is in progress. That checkout is not the release source.

## Profiles (`mobile/eas.json`)

| Profile | Use |
| --- | --- |
| `development` | Internal development client |
| `preview` | Internal APK/IPA against production API |
| `production` | Store build, remote auto-increment |

## Local Expo Go

Local development still uses `mobile/.env` (gitignored). Copy `mobile/.env.example` and point `EXPO_PUBLIC_API_URL` at your machine. Do not put secrets in that file.
