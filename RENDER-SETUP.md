# 🔧 Render env-vars setup — alles wat nog ingesteld moet worden

Dit document is **één keer doorlopen, klaar**. Neem ~30 minuten — daarna zijn push-notificaties, Google Maps en Google Calendar/Drive integratie live.

---

## ✅ Wat al gedaan is

- `ANTHROPIC_API_KEY` (Claude vision werkt al — verified live)
- `DATABASE_URL` (Postgres auto-injected door Render)
- `SECRET_KEY` (JWT signing)
- `RESEND_API_KEY` (transactional email)
- `BOOTSTRAP_OWNER` + `OWNER_EMAIL` + `OWNER_PASSWORD`

---

## ⏭️ Wat nog moet — 3 blokken

### Blok 1 — Web Push (VAPID) — 2 minuten

VAPID-keys zijn **al gegenereerd**, plak deze drie env-vars in Render:

```
VAPID_PUBLIC_KEY=<publieke sleutel uit het genereer-commando hierboven>
VAPID_PRIVATE_KEY=<private sleutel uit het genereer-commando hierboven — nooit in git>
VAPID_SUBJECT=mailto:info@fieldopsapp.nl
```

**Hoe:** Render Dashboard → `fieldops-api` service → **Environment** → **Add Environment Variable** (3×) → **Save Changes** (triggert redeploy).

> ⚠️ De `VAPID_PRIVATE_KEY` is **secret** — niet in git committen, niet delen, niet in screenshots laten zien. Als hij ooit lekt: re-run hetzelfde commando (`python -c "..."` in fieldops-api venv) en update Render — alle bestaande push-subscriptions moeten dan wel opnieuw subscriben.

**Test:** na redeploy → portaal openen → 🔔-knop in topbar → toestemming geven → check in DevTools console: `await navigator.serviceWorker.ready` → geen errors → push werkt.

---

### Blok 2 — Google Maps — 10 minuten

Voor: GPS-autocomplete in melding-formulier, Street View in melding-detail, kaart-pins op asset-pagina.

#### Stap 1 — API key genereren

1. Open [console.cloud.google.com](https://console.cloud.google.com/)
2. **Create Project** → naam: `FieldOps Production`
3. **APIs & Services** → **Library** → enable deze 3:
   - **Maps JavaScript API**
   - **Places API** (nieuwe versie)
   - **Geocoding API**
4. **Credentials** → **Create Credentials** → **API key**
5. Klik op de nieuwe key → **Edit API key**:
   - **Application restrictions:** HTTP referrers
   - Voeg toe:
     ```
     https://portaal.fieldopsapp.nl/*
     https://www.fieldopsapp.nl/*
     https://*.fieldopsapp.nl/*
     ```
   - **API restrictions:** Restrict key → vink alleen Maps JS, Places, Geocoding aan
6. **Save**

#### Stap 2 — Plak in Render

```
GOOGLE_MAPS_API_KEY=<Maps API key uit de Google Cloud Console>
```

#### Stap 3 — Billing

Google Maps vereist een billing-account, maar je krijgt **$200/maand gratis** quota — meer dan genoeg voor honderden gebruikers. Nooit kosten betaalt in praktijk voor SaaS jouw schaal.

**Test:** portaal → Nieuwe melding → in adres-veld typen → Places-suggestions verschijnen.

---

### Blok 3 — Google OAuth (Calendar + Drive) — 15 minuten

Voor: inspectie-deadlines automatisch in Google Calendar, foto's auto-upload naar Drive-folder per project.

#### Stap 1 — OAuth consent screen

1. Cloud Console → **APIs & Services** → **OAuth consent screen**
2. **User Type:** External → Create
3. Vul in:
   - **App name:** FieldOps
   - **User support email:** info@fieldopsapp.nl
   - **App logo:** upload `frontend/public/icon-512.png` (heb je al)
   - **App domain:** `fieldopsapp.nl`
   - **Authorized domains:** `fieldopsapp.nl`
   - **Developer contact:** info@fieldopsapp.nl
4. **Save and Continue**
5. **Scopes:** Add or Remove Scopes → kies:
   - `.../auth/calendar.events` (Calendar — events maken/lezen)
   - `.../auth/drive.file` (Drive — alleen files die jouw app maakt)
   - `.../auth/userinfo.email` (basic identity)
6. **Save and Continue**
7. **Test users:** voeg jezelf + 2-3 pilot-klant emails toe (zolang je in test-mode bent kunnen alleen test-users authoriseren)
8. **Save**

#### Stap 2 — OAuth Client ID

1. **Credentials** → **Create Credentials** → **OAuth client ID**
2. **Application type:** Web application
3. **Name:** FieldOps Production
4. **Authorized JavaScript origins:**
   ```
   https://portaal.fieldopsapp.nl
   https://www.fieldopsapp.nl
   ```
5. **Authorized redirect URIs:**
   ```
   https://portaal.fieldopsapp.nl/api/google/oauth/callback
   ```
6. **Create**
7. Kopieer de **Client ID** + **Client Secret** uit het pop-up

#### Stap 3 — Plak in Render

```
GOOGLE_OAUTH_CLIENT_ID=<Client ID uit de Google Cloud Console>
GOOGLE_OAUTH_CLIENT_SECRET=<Client secret uit de Google Cloud Console>
GOOGLE_OAUTH_REDIRECT_URI=https://portaal.fieldopsapp.nl/api/google/oauth/callback
```

#### Stap 4 — Publishing (later, niet nu)

Tijdens **test-mode** (eerste 100 users) hoef je niets meer te doen. Bij groei →
- **Publish App** in OAuth consent screen → vraagt Google-verification (~1-2 weken proces)
- Tot dan: nieuwe gebruikers krijgen "Google has not verified this app" warning — gewoon doorklikken

**Test:** portaal → Beheer → Instellingen → "Verbind Google Workspace" knop → Google login → toestemming → komt terug op portaal met groen vinkje "Verbonden".

---

## 🎯 Quick-paste samenvatting (voor in Render)

Dit is wat je in totaal moet plakken — kopieer-paste in Render Environment:

```
VAPID_PUBLIC_KEY=<publieke sleutel uit het genereer-commando hierboven>
VAPID_PRIVATE_KEY=<private sleutel uit het genereer-commando hierboven — nooit in git>
VAPID_SUBJECT=mailto:info@fieldopsapp.nl
GOOGLE_MAPS_API_KEY=<vul in na Blok 2>
GOOGLE_OAUTH_CLIENT_ID=<vul in na Blok 3>
GOOGLE_OAUTH_CLIENT_SECRET=<vul in na Blok 3>
GOOGLE_OAUTH_REDIRECT_URI=https://portaal.fieldopsapp.nl/api/google/oauth/callback
```

---

## 🔄 Volgorde

1. **Vandaag** — Blok 1 (VAPID) → 2 min
2. **Vandaag of morgen** — Blok 2 (Maps) → 10 min
3. **Deze week** — Blok 3 (OAuth) → 15 min

Na elke env-var save **wacht 2-3 min** op auto-redeploy in Render. Status: groen = klaar.

---

## 🐛 Troubleshooting

| Probleem | Check |
|---|---|
| Push-knop doet niets | Browser-DevTools → Application → Service Workers → controleer of `sw.js` registered is |
| Maps-veld blijft leeg | DevTools → Network → zoek `maps.googleapis.com` request → check status (403 = key restrictions klopt niet) |
| Google OAuth → "redirect_uri_mismatch" | URL in Google Console moet **exact** matchen — incl. trailing slash, `https://`, geen typos |
| OAuth → "App not verified" warning | Normal in test-mode, klik "Advanced" → "Go to FieldOps (unsafe)" → werkt |

---

## 📚 Referenties

- VAPID-keys regenereren: zie `push.py` regel 25-50 of run het Python-snippet uit deze setup
- Google Maps quota: [console.cloud.google.com → Maps Platform → Quotas](https://console.cloud.google.com/google/maps-apis/quotas)
- OAuth scopes uitbreiden: zie `google_integration.py` regel 60-80
