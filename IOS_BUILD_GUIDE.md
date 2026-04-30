# FieldOps iOS Build via GitHub Actions (zonder Mac)

Deze setup bouwt iOS `.ipa` builds gratis in de GitHub cloud (macOS runner)
en uploadt automatisch naar App Store Connect — **vanaf je Windows PC**.

## Apple rejection (16 april 2026)

> **Guideline 2.3.8** — "the app icon displayed on the device is blank"

**Fix:** alle 18 iOS icon sizes zijn nu gegenereerd in `ios-app-icons/AppIcon.appiconset/`.
De GitHub Actions workflow kopieert ze automatisch naar de Capacitor iOS build.

## Eenmalige setup (15 minuten)

### Stap 1 — App Store Connect API Key

1. Ga naar <https://appstoreconnect.apple.com/access/integrations/api>
2. Klik **+** naast "Active"
3. Naam: `FieldOps GitHub Actions`, Access: **App Manager**
4. Download de `.p8` file (ÉÉN KEER beschikbaar — bewaar goed!)
5. Noteer:
   - **Key ID** (10 chars, bv. `ABC1234DEF`)
   - **Issuer ID** (UUID, bv. `12345678-...`)

### Stap 2 — App-Specific Password

1. Ga naar <https://appleid.apple.com/account/manage>
2. Sign-In and Security → **App-Specific Passwords**
3. Klik **+**, naam: `FieldOps Upload`
4. Kopieer wachtwoord (formaat: `xxxx-xxxx-xxxx-xxxx`)

### Stap 3 — GitHub Secrets toevoegen

Ga naar <https://github.com/fomosman-cyber/fieldops-api/settings/secrets/actions>

Voeg toe (klik **New repository secret** voor elk):

| Secret naam | Waarde |
|---|---|
| `APPLE_ID` | `fomosman@gmail.com` |
| `APPLE_TEAM_ID` | `DUALB78L9T` |
| `APPLE_APP_SPECIFIC_PASSWORD` | het `xxxx-xxxx-xxxx-xxxx` wachtwoord uit stap 2 |
| `APP_STORE_CONNECT_API_KEY_ID` | de Key ID uit stap 1 |
| `APP_STORE_CONNECT_API_ISSUER_ID` | de Issuer ID uit stap 1 |
| `APP_STORE_CONNECT_API_KEY` | inhoud van `.p8` file (open in Notepad, plak alles) |
| `KEYCHAIN_PASSWORD` | random string, bv. `random-temp-pw-123` |

### Stap 4 — Workflow starten

1. Ga naar <https://github.com/fomosman-cyber/fieldops-api/actions/workflows/ios-build.yml>
2. Klik **Run workflow**
3. Build number: `5` (verhogen bij elke nieuwe upload — 4 was de afgekeurde)
4. Klik groene **Run workflow** knop
5. Wacht ~10-15 min tot build klaar is

## Wat doet de workflow

1. Spint een macOS 14 cloud runner op (gratis, GitHub Actions)
2. Installeert Node.js 20 + Xcode 15
3. Voert `npx cap add ios` + `npx cap sync` uit
4. **Kopieert de 18 nieuwe iOS app icons** (lost de Apple rejection op)
5. Voegt verplichte privacy strings toe (location, camera, photos)
6. Bumpt build number
7. Bouwt met automatic signing (Xcode regelt cert + profile via API key)
8. Exporteert `.ipa`
9. Upload naar App Store Connect via `xcrun altool`

## Na succesvolle upload

1. Ga naar <https://appstoreconnect.apple.com/apps/6761791781/distribution/ios/version/inflight>
2. Onder "Build" — kies de nieuwe build (~5 min na upload zichtbaar)
3. Ga naar **App Review** sectie → bekijk de openstaande submission
4. Klik **Resubmit to App Review** rechtsboven
5. Reageer op Apple's bericht: *"The app icon issue from build 4 has been resolved.
   All required iOS icon sizes are now included in build 5."*

## Verwachte timeline

- **Vandaag:** GitHub Secrets setup (15 min) + workflow draaien (15 min)
- **+5 min:** build verschijnt in App Store Connect
- **+10 min:** resubmit voor review
- **+24-48 uur:** Apple keurt goed (vaak sneller na eerste rejectie fix)
- **+1 uur na approval:** beschikbaar in App Store

## Troubleshooting

| Probleem | Oplossing |
|---|---|
| Workflow faalt op `cap add ios` | Check `capacitor/package.json` — node deps moeten installeren |
| Code signing error | Verifieer alle 7 secrets, vooral APP_STORE_CONNECT_API_KEY (volledige .p8 inhoud incl. BEGIN/END regels) |
| `archive` step hangt | Xcode Cloud quota / probeer opnieuw na 1u |
| `altool` upload faalt | Verifieer APP_SPECIFIC_PASSWORD, app moet "Ready for Submission" zijn |
| `bundle ID mismatch` | Check `capacitor.config.ts` → `appId: 'nl.fieldopsapp.app'` ✓ |

## Wijzigen

Voor toekomstige updates:
1. Code wijzigen op fieldopsapp.nl (geen rebuild nodig — app laadt remote)
2. Voor native wijzigingen: bump build number, run workflow opnieuw
3. App Store review pas opnieuw nodig voor major changes (icons, capabilities, plist)

## Alternatief: handmatig vanuit Xcode (vereist Mac)

Als je later toegang hebt tot een Mac:
```bash
cd capacitor
npm install
npx cap add ios
npx cap sync ios
npx cap open ios
# In Xcode: Product → Archive → Distribute → App Store Connect
```
