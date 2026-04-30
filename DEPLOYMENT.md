# FieldOps — Deployment Checklist

Volg deze stappen één voor één. Vink af wat klaar is.

---

## 1. Render Web Service Check (5 min)

Open [dashboard.render.com](https://dashboard.render.com).

- [ ] Service `fieldops-api` zichtbaar?
- [ ] Klik service → tab **Events** → laatste deploy commit = `3f3706f` of nieuwer?
- [ ] Status = `Live`?

**Als Status ≠ Live of laatste commit oud:**
- Klik rechtsboven **"Manual Deploy"** → **"Deploy latest commit"**
- Wacht 3 min
- Verifieer in nieuw tabblad: <https://fieldops-api-8txr.onrender.com/manifest.webmanifest> moet JSON tonen

**Als build faalt:**
- Klik **Logs** tab → scroll naar einde
- Stuur de laatste 30 regels naar Claude
- Meest waarschijnlijk: `psycopg2-binary` build fout → ik vervang door `psycopg2` of `pg8000`

---

## 2. Postgres Database (5 min)

SQLite verliest data bij elke redeploy. Gebruik de managed Postgres van Render.

### Optie A — Via Blueprint (aanbevolen)
- [ ] Render → **Blueprints** → **New Blueprint Instance**
- [ ] Selecteer GitHub repo `fomosman-cyber/fieldops-api`
- [ ] Branch `main`
- [ ] Klik **Apply**
- [ ] Render leest `render.yaml` → maakt `fieldops-db` Postgres aan + koppelt automatisch `DATABASE_URL`

### Optie B — Handmatig
- [ ] Render → **New +** → **PostgreSQL**
- [ ] Naam: `fieldops-db`, plan: `Basic 256MB` ($6/maand)
- [ ] Wacht 2 min tot status `Available`
- [ ] Kopieer **Internal Database URL**
- [ ] Ga naar service `fieldops-api` → **Environment** → bewerk `DATABASE_URL` → plak waarde

### Eerste login
- [ ] Service Environment → voeg toe:
  - `BOOTSTRAP_OWNER` = `true`
  - `OWNER_EMAIL` = `fomosman@gmail.com`
  - `OWNER_PASSWORD` = sterk wachtwoord (≥12 chars, mix letters/cijfers/symbolen)
- [ ] Klik **Save Changes** → service redeployt automatisch
- [ ] Log in op <https://fieldops-api-8txr.onrender.com/portaal>
- [ ] **Belangrijk:** zet `BOOTSTRAP_OWNER` = `false` na succesvolle login (anders elke deploy = nieuwe owner-poging)

---

## 3. Email (SMTP) Werkend Maken (10 min)

Zonder dit falen uitnodigingen + wachtwoord-resets stil.

### Gmail App Password
- [ ] Ga naar <https://myaccount.google.com/security>
- [ ] **2-Step Verification** moet aan staan (anders kun je geen app-password maken)
- [ ] Ga naar <https://myaccount.google.com/apppasswords>
- [ ] App: `Mail`, Device: `FieldOps Render`
- [ ] Kopieer het 16-tekens wachtwoord (zonder spaties)

### In Render env vars
- [ ] `SMTP_USER` = jouw Gmail adres
- [ ] `SMTP_PASS` = het 16-tekens app-password
- [ ] `FROM_EMAIL` = `noreply@fieldopsapp.nl` (of jouw Gmail)
- [ ] Save → wachten op redeploy
- [ ] Test: in portaal → "Wachtwoord vergeten" → check je inbox

---

## 4. Eigen Domein (15 min)

### Stap A — In Render
- [ ] Service `fieldops-api` → **Settings** → **Custom Domains**
- [ ] Voeg toe: `fieldopsapp.nl`
- [ ] Voeg toe: `www.fieldopsapp.nl`
- [ ] Voeg toe: `app.fieldopsapp.nl`
- [ ] Render toont voor elk domein een DNS-target (bv. `fieldops-api-xxxx.onrender.com`)

### Stap B — Bij je domeinregistrar (TransIP / Hostnet / etc.)
DNS-records toevoegen:

| Type  | Naam | Waarde                                  | TTL |
|-------|------|------------------------------------------|-----|
| ALIAS / ANAME | @ (= fieldopsapp.nl) | `fieldops-api-8txr.onrender.com` | 3600 |
| CNAME | www  | `fieldops-api-8txr.onrender.com` | 3600 |
| CNAME | app  | `fieldops-api-8txr.onrender.com` | 3600 |

> **Geen ALIAS support?** Gebruik **A record** met de IP die Render geeft (in Custom Domains paneel).

- [ ] DNS records toegevoegd
- [ ] Wacht 5-30 min op propagatie
- [ ] In Render → Custom Domains → status moet groen `Verified` worden
- [ ] Test: <https://fieldopsapp.nl> moet werken (Render regelt SSL automatisch via Let's Encrypt)

---

## 5. Test op iPhone (5 min)

- [ ] Open Safari op iPhone
- [ ] Ga naar `https://fieldopsapp.nl/portaal` (of `https://fieldops-api-8txr.onrender.com/portaal` als domein nog niet werkt)
- [ ] Login met owner credentials
- [ ] Tik **deel-icoon** (vierkantje met pijl ↑) onderaan Safari
- [ ] Scroll, tik **"Voeg toe aan beginscherm"**
- [ ] Bevestig met **"Voeg toe"**
- [ ] FieldOps icoon staat nu op je homescreen
- [ ] Tik icoon → opent fullscreen zonder Safari-balk

### Test functies
- [ ] Login werkt
- [ ] Nieuwe melding aanmaken (met foto + GPS)
- [ ] Project zien
- [ ] Offline test: vliegtuigmodus aan → app opent nog steeds

---

## 6. Optioneel — App Store + Play Store via Capacitor

Pas doen als alles hierboven werkt en je echt App Store presence wilt.

Vereisten:
- Apple Developer Account (€99/jaar) → <https://developer.apple.com/programs/>
- Google Play Console (€25 eenmalig) → <https://play.google.com/console>
- Mac (voor iOS build) of EAS Build cloud service

Zie `capacitor/README.md` (als je dit later wilt opzetten).

---

## Snelle troubleshooting

| Probleem | Oplossing |
|---|---|
| `502 Bad Gateway` op Render | Service slaapt (free tier) — eerste request duurt 30s |
| PWA install knop verschijnt niet | Cache leegmaken; iOS = altijd handmatig "Voeg toe aan beginscherm" |
| `503` na database aanmaken | Service moet redeployen na DATABASE_URL wijziging — wacht 2 min |
| Email komt niet aan | Check spam-folder; verifieer SMTP_USER/SMTP_PASS in Render logs |
| `400 Disallowed CORS` | Voeg domein toe aan `CORS_ORIGINS` env var (komma-gescheiden) |
| Login werkt niet na deploy | Database is leeg → zet `BOOTSTRAP_OWNER=true` + `OWNER_PASSWORD` tijdelijk |

---

## Wat is er klaar (door Claude)

✅ Backend code production-secure (geen hardcoded secrets, postgres support, CORS lockdown)
✅ Database migraties (idempotent, faalt veilig)
✅ Auto-fail bij ontbrekende SECRET_KEY in productie
✅ PWA: 19 icons, manifest, service worker, offline support
✅ iOS splash screens voor iPhone SE t/m 15 Pro Max
✅ Install banner met platform-detectie (Android prompt + iOS instructie)
✅ Push notification hooks (klaar voor uitbreiding)
✅ Render Blueprint config met managed Postgres

## Wat moet jij doen

1. Render dashboard openen → Manual Deploy / Apply Blueprint
2. Env vars invullen (SMTP, BOOTSTRAP_OWNER eenmalig)
3. DNS records bij registrar
4. iPhone test
