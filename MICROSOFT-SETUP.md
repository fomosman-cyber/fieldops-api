# Microsoft 365 OAuth Setup — eenmalig (~15 min)

Voor: Outlook Calendar sync · OneDrive/SharePoint upload · (later) Teams chat-notificaties.

---

## Stap 1 — Azure App Registration

1. Open [portal.azure.com](https://portal.azure.com) en log in als admin van je tenant.
2. Ga naar **Microsoft Entra ID** → **App registrations** → **+ New registration**
3. Vul in:
   - **Name:** `FieldOps Production`
   - **Supported account types:** *"Accounts in any organizational directory (Multitenant)"*
     → kies dit als je gemeenten met verschillende tenants wilt bedienen.
     Voor single-tenant kies *"Single tenant"*.
   - **Redirect URI:**
     - Type: **Web**
     - URL: `https://portaal.fieldopsapp.nl/api/microsoft/oauth/callback`
4. Klik **Register**

---

## Stap 2 — Redirect URI's voor dev/test toevoegen

1. In je nieuwe app: **Authentication**
2. **+ Add URI** → voeg toe:
   - `http://localhost:8001/api/microsoft/oauth/callback` (dev)
   - eventueel `https://staging.fieldopsapp.nl/api/microsoft/oauth/callback`
3. **Implicit grant and hybrid flows:** beide UIT laten
4. **Allow public client flows:** NEE
5. **Save**

---

## Stap 3 — API permissions

1. Open **API permissions** → **+ Add a permission**
2. Kies **Microsoft Graph** → **Delegated permissions**
3. Selecteer:
   - `User.Read` (basis profiel)
   - `Calendars.ReadWrite` (Outlook events)
   - `Files.ReadWrite` (OneDrive eigen drive)
   - `Sites.ReadWrite.All` (SharePoint, optioneel)
   - `offline_access` (refresh tokens — verplicht!)
4. **Add permissions**
5. **Grant admin consent for [tenant]** ✓ klikken (alleen tenant-admin)

---

## Stap 4 — Client secret aanmaken

1. Open **Certificates & secrets** → **+ New client secret**
2. Description: `FieldOps Production secret 2026`
3. Expiry: **24 maanden** (langste optie — herinnering noteren)
4. **Add**
5. **Kopieer de "Value" direct** — die zie je na pagina-refresh nooit meer terug

---

## Stap 5 — Render env-vars

Plaats in [Render Dashboard](https://dashboard.render.com) → `fieldops-api` → **Environment**:

```
MS_OAUTH_CLIENT_ID=<de Application (client) ID uit Azure overview-pagina>
MS_OAUTH_CLIENT_SECRET=<de "Value" die je net kopieerde>
MS_OAUTH_REDIRECT_URI=https://portaal.fieldopsapp.nl/api/microsoft/oauth/callback
MS_OAUTH_TENANT=common
```

**Tenant-instelling:**
- `common` = elk Microsoft-account (zakelijk + persoonlijk)
- `organizations` = alleen werk/schoollogins (aanbevolen voor B2G)
- `<tenant-uuid>` = strict alleen jouw eigen tenant (single-tenant SaaS)

Voor B2G NL infra-markt: gebruik **`organizations`** — voorkomt persoonlijke @outlook.com logins.

Save → Render redeploy ~3 min.

---

## Stap 6 — Test in portaal

1. Open `portaal.fieldopsapp.nl/portaal` → log in
2. Ga naar **Instellingen** in side-nav
3. Scroll naar **🪟 Microsoft 365** card
4. Klik **"Verbind Microsoft 365"**
5. Microsoft consent-screen → kies account → toesta
6. Terug op portaal → toast "🎉 Microsoft 365 account verbonden!"
7. Status-card toont nu: ✓ Verbonden als `you@bedrijf.nl`

---

## Stap 7 — Test Outlook Calendar sync

1. Maak een melding aan in portaal
2. Open in browser-DevTools console of Postman:
   ```
   POST /api/microsoft/calendar/sync-melding/{melding_id}
   Headers: Authorization: Bearer <jouw-jwt>
   ```
3. Check je Outlook agenda → er staat nu een event "[FieldOps] {melding-titel}"

---

## Stap 8 — Test OneDrive upload

```bash
curl -X POST https://portaal.fieldopsapp.nl/api/microsoft/onedrive/upload \
  -H "Authorization: Bearer <jouw-jwt>" \
  -F "file=@test.pdf"
```

Response: `{ "id": "...", "name": "test.pdf", "web_url": "https://onedrive.live.com/..." }`

OneDrive auto-creates `FieldOps Rapportages` folder bij eerste upload.

---

## ❌ Troubleshooting

| Fout | Oplossing |
|---|---|
| `AADSTS50011 redirect URI mismatch` | URL in Azure App moet **exact** matchen — controleer trailing slash + https vs http |
| `consent_required` na grant | Tenant-admin moet expliciet "Grant admin consent" klikken |
| `invalid_client` | Client secret expired? Genereer nieuwe + update Render |
| `insufficient_scope` op Graph call | Voegg permissie toe in Azure → admin consent → user moet opnieuw inloggen |
| Token-refresh faalt | `offline_access` scope vergeten? Kijk in MS_OAUTH_SCOPES |

---

## 🔄 Verschillen Google ↔ Microsoft

| Aspect | Google | Microsoft |
|---|---|---|
| Auth-server | accounts.google.com | login.microsoftonline.com |
| Tenant | n.v.t. | common / organizations / specific UUID |
| Calendar SDK | Calendar API v3 | Graph /me/events |
| Drive SDK | Drive API v3 | Graph /me/drive |
| Refresh-token | Standaard | Vereist `offline_access` scope |
| Scope-format | `space-separated URLs` | `space-separated namespaces` (User.Read) |
| Event payload | `start.dateTime` | `start: { dateTime, timeZone }` |
| Console | console.cloud.google.com | portal.azure.com |

Beide systemen werken naast elkaar in FieldOps. Een user kan beide koppelen — events worden naar beide gesynced.
