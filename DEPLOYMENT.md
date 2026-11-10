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

## 7. Controleren dat productie ook echt uitrolt

Een groene merge is geen uitgerolde merge. Op 9 november 2026 stond de live
service nog op de commit van 24 september -- elf commits en zeven weken achter,
terwijl CI al die tijd groen was. Er was niets kapot; er keek alleen niemand.

`/api/version` meldt welke commit er draait. Met de hand:

```bash
curl https://portaal.fieldopsapp.nl/api/version
```

Automatisch doet de workflow `.github/workflows/deploy-drift.yml` dit twee keer
per dag (07:00 en 16:00 UTC) en bij een rode run stuurt GitHub een e-mail. Na
een merge zelf controleren: Actions -> Deploy-drift -> Run workflow.

Hetzelfde lokaal, tegen de commit die je net hebt gemerged:

```bash
python scripts/deploy_drift.py
```

Afloopcodes: `0` productie is bij (of de commit is nog jong genoeg om uit te
rollen), `1` productie loopt achter, `2` niet vast te stellen -- endpoint
onbereikbaar, of de service meldt geen commit. Die laatste twee zijn bewust
verschillend: "ik kon het niet zien" is geen "het klopt niet".

Instelbaar via omgevingsvariabelen (of `--url` / `--respijt`):

- `DRIFT_BASIS_URL` -- welke omgeving je controleert; standaard
  `https://portaal.fieldopsapp.nl`.
- `DRIFT_RESPIJT_UUR` -- hoeveel uur een verse commit mag uitrollen voordat het
  achterstand heet; standaard `2`. Lager betekent eerder melden, maar ook meer
  valse meldingen vlak na een merge.

Meldt het script achterstand, dan zit de oorzaak in Render, niet in de code:

1. De service -> Settings -> **Auto-Deploy**. Staat die uit, dan heb je je
   antwoord.
2. Het tabblad **Events**: is de laatste deploy mislukt, of nooit gestart?
3. Staat de service die het domein bedient wel in `render.yaml`? Is dat niet zo,
   dan is de blueprint nooit toegepast en gelden de cronjobs en env-vars
   daaruit niet voor de draaiende omgeving. Zie `MIGRATIE-FRANKFURT.md`.

Let op het verschil tussen herstarten en uitrollen. Een herstart haalt geen
nieuwe code binnen: op 9 november herstartte de service om 20:23 UTC en kwam
terug op dezelfde commit van september.

---

## Snelle troubleshooting

| Probleem | Oplossing |
|---|---|
| `502 Bad Gateway` op Render | Service slaapt (free tier) — eerste request duurt 30s |
| PWA install knop verschijnt niet | Cache leegmaken; iOS = altijd handmatig "Voeg toe aan beginscherm" |
| `503` na database aanmaken | Service moet redeployen na DATABASE_URL wijziging — wacht 2 min |
| Email komt niet aan | Check spam-folder; verifieer SMTP_USER/SMTP_PASS in Render logs |
| `400 Disallowed CORS` | Voeg domein toe aan `CORS_ORIGINS` env var (komma-gescheiden) |
| Nieuwe code niet zichtbaar na merge | `python scripts/deploy_drift.py` -- staat Auto-Deploy aan op de service die het domein bedient? Zie sectie 7 |
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
