# Google Workspace OAuth Setup — eenmalig (~20 min)

Voor: Google Calendar event-sync · Google Drive upload van rapportages.

> **Wie doet dit?** Eenmalig door een platform-admin. Daarna kan **iedere gebruiker** via *Instellingen → Verbind Google account* z'n eigen Google-account koppelen — geen extra config nodig per user.

---

## Stap 1 — Google Cloud project aanmaken

1. Open [console.cloud.google.com](https://console.cloud.google.com)
2. Klik linksboven op de project-dropdown → **New Project**
3. Vul in:
   - **Project name:** `FieldOps Production`
   - **Organization:** kies je werk-organisatie (of "No organization" voor solo-account)
4. **Create**
5. Wacht ~10s → switch naar het nieuwe project (project-dropdown linksboven)

---

## Stap 2 — APIs activeren

1. **APIs & Services** → **Library**
2. Zoek + activeer:
   - **Google Calendar API** → klik → **Enable**
   - **Google Drive API** → klik → **Enable**

---

## Stap 3 — OAuth consent screen

1. **APIs & Services** → **OAuth consent screen**
2. **User Type:**
   - **Internal** = alleen accounts binnen je Google Workspace-tenant (aanbevolen voor B2G/SaaS-eigen-team)
   - **External** = elk Google-account (nodig als je klanten met willekeurige `@gmail.com` of andere Workspace-tenants bedient — Voor FieldOps B2B: kies dit)
3. **Create**
4. Vul in:
   - **App name:** `FieldOps`
   - **User support email:** je eigen e-mail
   - **App logo:** optioneel — upload het FieldOps-logo (120×120 PNG)
   - **Application home page:** `https://portaal.fieldopsapp.nl`
   - **Authorized domains:** `fieldopsapp.nl`
   - **Developer contact:** je eigen e-mail
5. **Save and Continue**

### Scopes
1. **+ Add or Remove Scopes**
2. Voeg toe (gebruik de zoekbalk):
   - `https://www.googleapis.com/auth/calendar.events` — Calendar events lezen/schrijven
   - `https://www.googleapis.com/auth/drive.file` — alleen door deze app gemaakte bestanden
   - `https://www.googleapis.com/auth/userinfo.email` — gebruiker-e-mailadres
   - `openid` — basis-identificatie
3. **Update** → **Save and Continue**

### Test users (alleen bij External + niet-published)
1. Voeg jouw eigen e-mail toe als test-user
2. **Save and Continue**

> Voor productie-rollout zet je later de app op **Published** status. Dan kunnen alle gebruikers koppelen zonder als test-user te zijn toegevoegd. Google review nodig als je sensitive scopes hebt — `calendar.events` en `drive.file` zijn meestal restricted maar niet sensitive (geen review-cycle nodig).

---

## Stap 4 — OAuth Client ID aanmaken

1. **APIs & Services** → **Credentials** → **+ Create Credentials** → **OAuth client ID**
2. **Application type:** Web application
3. **Name:** `FieldOps Production Web Client`
4. **Authorized JavaScript origins:**
   - `https://portaal.fieldopsapp.nl`
   - `http://localhost:8001` (dev)
5. **Authorized redirect URIs:**
   - `https://portaal.fieldopsapp.nl/api/google/oauth/callback`
   - `http://localhost:8001/api/google/oauth/callback` (dev)
6. **Create**
7. **Kopieer:**
   - **Client ID** (~72 chars, eindigt op `.apps.googleusercontent.com`)
   - **Client secret** (~24 chars)

> Dialoog sluiten ≠ secret kwijt — je kunt later op de credential klikken om de secret weer te zien.

---

## Stap 5 — Render env-vars

Ga naar [Render Dashboard](https://dashboard.render.com) → `fieldops-api` → **Environment**.

Voeg toe (of update als ze al bestaan):

```
GOOGLE_OAUTH_CLIENT_ID=<Client ID uit stap 4>
GOOGLE_OAUTH_CLIENT_SECRET=<Client secret uit stap 4>
GOOGLE_OAUTH_REDIRECT_URI=https://portaal.fieldopsapp.nl/api/google/oauth/callback
```

**Save** → Render redeploy ~3 min.

---

## Stap 6 — Test in portaal

1. Open `portaal.fieldopsapp.nl/portaal` → log in
2. Side-nav: **Instellingen**
3. Scroll naar **📅 Google Workspace** kaart
4. De waarschuwing "niet geconfigureerd" zou moeten verdwijnen — als die nog staat: hard refresh (Ctrl+F5) of wacht tot Render redeploy klaar is
5. Klik **"Verbind Google account"**
6. Google consent-screen → kies account → "Doorgaan" / "Toesta"
7. Terug op portaal → toast "🎉 Google-account verbonden"
8. Status-card toont nu: **✓ Verbonden als you@bedrijf.nl**

---

## Stap 7 — Test calendar-event

1. In de Google-card op Instellingen: klik **"Test event in agenda"**
2. Controle: open je Google Agenda voor vandaag → er staat een event "FieldOps: testkoppeling werkt"
3. Veilig om weer te verwijderen

---

## Stap 8 — Auto-sync van meldingen (optioneel)

Standaard maakt FieldOps geen events automatisch — alleen als je expliciet een melding naar agenda stuurt. Voor auto-sync bij elke nieuwe melding: zie `orchestration_router.py` `enable_auto_calendar_sync` (kan org-breed aangezet via Instellingen → Organisatie).

---

## ❌ Troubleshooting

| Fout | Oplossing |
|---|---|
| `redirect_uri_mismatch` | URI in Google Console moet **exact** matchen (incl. https vs http, geen trailing slash). Voeg beide dev + prod toe. |
| `access_denied` | User klikte "Annuleren" op consent-screen. Geen actie. |
| `Token has been expired or revoked` | User heeft koppeling op myaccount.google.com ingetrokken. Vraag om opnieuw te verbinden. |
| `403 The user must approve...` | App nog niet "Published" en user is geen test-user. Voeg toe via OAuth consent screen → Test users. |
| `invalid_client` op token-exchange | Client secret expired? Genereer nieuwe in Console → Credentials → klik op je OAuth client → Add Secret → update Render env-var. |

---

## 🔄 Verschillen met Microsoft 365

Beide systemen werken naast elkaar. Een user kan beide koppelen — events worden naar beide kalenders gesynced als hij dat wil.

| Aspect | Google | Microsoft |
|---|---|---|
| Console | console.cloud.google.com | portal.azure.com |
| App-name | OAuth Client ID | Azure App Registration |
| Tenant | n.v.t. (per Workspace-account) | common / organizations / specific UUID |
| Refresh-token | Standaard `access_type=offline` | Vereist `offline_access` scope |
| Drive scope | `drive.file` (alleen eigen bestanden) | `Files.ReadWrite` (hele OneDrive) |

Voor **NL infra B2G-markt** is Microsoft 365 vaak relevanter (gemeenten draaien op MS); Google is pulluur in private-sector aannemers + agile teams. Beide aanbieden = breder bereikbaar.

---

## 📊 Coverage-monitoring

Org-admin kan via `GET /api/admin/integrations/coverage` zien hoeveel van het team Google/Microsoft heeft gekoppeld — handig voor follow-up. Of via UI: **Instellingen → Integraties** (admin-only sectie).
