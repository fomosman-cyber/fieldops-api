# FieldOps — Capacitor Mobile Build

Wraps the existing FieldOps PWA into a native iOS + Android app for App Store / Play Store distribution.

## Vereisten

| Item | Hoe verkrijgen | Kosten |
|---|---|---|
| Apple Developer Account | <https://developer.apple.com/programs/> | €99/jaar |
| Google Play Console | <https://play.google.com/console> | €25 eenmalig |
| Mac met Xcode 15+ | Voor iOS build (verplicht) — alternatief: <https://expo.dev/eas> cloud build | gratis |
| Android Studio | <https://developer.android.com/studio> — voor lokale Android build | gratis |
| Node.js 20+ | <https://nodejs.org> | gratis |

## Setup (eenmalig)

```bash
cd capacitor

# 1. Installeer dependencies
npm install

# 2. Maak www/ folder met basis HTML (Capacitor vereist een web-dir)
mkdir www
cat > www/index.html << 'EOF'
<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta http-equiv="refresh" content="0; url=https://fieldopsapp.nl/portaal">
</head><body>Laden...</body></html>
EOF

# 3. Initialiseer Capacitor (alleen als capacitor.config.ts nog niet bestaat in root)
# (config staat al klaar — skip init)

# 4. Voeg native platforms toe
npx cap add ios
npx cap add android

# 5. Sync alles
npx cap sync
```

## iOS Build (op Mac)

```bash
npx cap open ios
```

In Xcode:
1. Selecteer team (jouw Apple Developer Account)
2. Bundle Identifier: `nl.fieldopsapp.app`
3. **Info.plist** — voeg toe:
   - `NSLocationWhenInUseUsageDescription` = "FieldOps gebruikt je locatie voor GPS-registratie van meldingen"
   - `NSCameraUsageDescription` = "FieldOps gebruikt de camera voor foto's bij meldingen"
   - `NSPhotoLibraryUsageDescription` = "FieldOps slaat foto's op van meldingen"
   - `NSPhotoLibraryAddUsageDescription` = "FieldOps slaat foto's op van meldingen"
4. **Signing & Capabilities** → voeg toe:
   - Push Notifications
   - Background Modes: Location updates, Background fetch, Remote notifications
5. **Product → Archive** → Distribute App → App Store Connect → Upload

In App Store Connect:
- Maak nieuwe app aan met dezelfde bundle ID
- Vul metadata in (NL screenshots, beschrijving, keywords, privacybeleid URL)
- Privacy Policy verplicht: <https://fieldopsapp.nl/privacy> (moet bestaan!)
- Submit for Review (1-3 dagen wachten)

## Android Build

```bash
npx cap open android
```

In Android Studio:
1. **app/src/main/AndroidManifest.xml** — voeg toe:
   ```xml
   <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />
   <uses-permission android:name="android.permission.CAMERA" />
   <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />
   <uses-permission android:name="android.permission.INTERNET" />
   ```
2. **Build → Generate Signed Bundle / APK** → Android App Bundle (`.aab`)
3. Maak nieuw keystore aan (bewaar `.jks` veilig — bij verlies kun je nooit meer updaten!)
4. Upload `.aab` naar Google Play Console
5. Vul Store listing, screenshots, privacy policy
6. Release → Internal testing → Production (review duurt 1-3 dagen)

## Belangrijke commando's

```bash
# Na elke wijziging in webcontent op fieldopsapp.nl:
npx cap sync          # geen rebuild nodig — app laadt remote URL

# Native code wijziging:
npx cap copy ios      # kopieer assets
npx cap update ios    # update plugins
npx cap open ios      # open Xcode opnieuw
```

## Wat zit waar

```
capacitor/
├── capacitor.config.ts    # main config (server URL, plugins, splash)
├── package.json            # npm dependencies
├── www/                    # Capacitor verplichte web-dir (alleen redirect-stub)
├── ios/                    # Xcode project (gegenereerd)
└── android/                # Android Studio project (gegenereerd)
```

## Troubleshooting

| Probleem | Oplossing |
|---|---|
| `xcodebuild: command not found` | Installeer Xcode + accepteer license: `sudo xcodebuild -license accept` |
| iOS app toont blank scherm | Check `capacitor.config.ts` server.url — moet bereikbaar zijn |
| Android: `INSTALL_FAILED_VERSION_DOWNGRADE` | Verhoog `versionCode` in `app/build.gradle` |
| Apple rejectie "App is just a webview" | Voeg native features toe: push notifications, biometric login, share sheet |
| Push notifications werken niet | Apple: APNS cert in App Store Connect; Android: Firebase project + `google-services.json` |

## Geschatte timeline

- **Setup + lokale builds:** 1-2 dagen
- **App Store review:** 1-7 dagen (eerste keer vaak rejected — fix + resubmit)
- **Play Store review:** 1-3 dagen
- **Privacy policy schrijven + hosten:** 1 dag

## Tips

- Begin met **Internal Testing** (TestFlight voor iOS, Internal Track Android) — krijg feedback van vrienden voordat publiek
- Apple verwerpt vaak "alleen webview" apps. Zorg voor minimaal:
  - Native push notifications
  - Native camera met fallback
  - Native GPS met achtergrond-tracking
  - Native share sheet
  - Splash screen
- Privacy Policy moet duidelijk vermelden: locatie, foto's, contact info, dataopslag
