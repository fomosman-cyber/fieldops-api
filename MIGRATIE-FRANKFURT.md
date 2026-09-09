# Verhuizing naar Frankfurt

Productie draait op dit moment in **Virginia** (VS) — zowel `fieldops-api` als
`fieldops-db`. De compliance-pagina belooft Frankfurt. Dit document beschrijft
hoe je dat rechtzet.

Render kan de regio van een bestaande service niet wijzigen. Er komt dus een
nieuwe omgeving náást de oude, en pas als die bewezen werkt gaat het domein om.
De oude omgeving blijft daarbij de hele tijd draaien, dus je kunt op elk moment
terug.

Reken op een halve dag, met ongeveer **een kwartier onderbreking** aan het eind.

---

## Vooraf: twee dingen die stil kunnen breken

**1. `SECRET_KEY` moet je overnemen, niet opnieuw laten genereren.**
`crypto_fields.py:32` valt terug op `SECRET_KEY` als `TOKEN_ENCRYPTION_KEY` leeg
is. Op de huidige productie is dat het geval, dus de opgeslagen Google- en
Microsoft-tokens zijn versleuteld met `SECRET_KEY`. Een verse sleutel maakt die
tokens onleesbaar en de koppelingen stuk. In het blueprint staat hij daarom op
`sync: false`.

**2. De paden naar foto's en back-ups moeten identiek blijven.**
`S3_PHOTO_PREFIX` is `photos` en `S3_BACKUP_PREFIX` is `backups/fieldops`. Wijk
daarvan af en bestaande bestanden staan er nog wel, maar vindt niemand ze meer.

---

## Stap 0 — Waar staan je foto's? (5 minuten, doe dit eerst)

Kijk in Render bij de huidige service onder Environment naar `S3_ENDPOINT_URL`
en `S3_REGION`.

- Wijst het naar Cloudflare R2 of naar `eu-central-1`? Dan staan je foto's al
  goed en hoeven ze niet mee. Ga door naar stap 1.
- Staat er een Amerikaanse regio (`us-east-1`, `us-west-2`)? Dan moeten de
  foto's ook verhuizen. Dat is een aparte klus met `rclone` of `aws s3 sync`, en
  dan wordt dit een dag in plaats van een dagdeel.

---

## Stap 1 — Nieuwe omgeving uitrollen

1. Render → **Blueprints** → **New Blueprint Instance**.
2. Kies de repo `fomosman-cyber/fieldops-api`, branch `main`.
3. Render leest `render.yaml` en stelt voor: `fieldops-api-eu`,
   `fieldops-db-eu` en `fieldops-backup-eu`, alle drie in Frankfurt.
4. Uitrollen. De build faalt of de service start niet — dat is verwacht, want de
   geheimen staan nog leeg.

## Stap 2 — Geheimen overzetten

Alles met `sync: false` moet je zelf invullen. Neem ze één voor één over uit de
oude service (Environment → oogje → kopiëren).

Twee uitzonderingen:

- **`SECRET_KEY`** — exact overnemen (zie hierboven). Zet dezelfde waarde ook
  in `TOKEN_ENCRYPTION_KEY`, dan blijft de versleuteling gelijk en staat hij
  voortaan expliciet.
- **`VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`** — hier neem je *niet* de oude
  waarde over. De oude private key lag publiek op internet. Gebruik het nieuwe
  paar uit `NIEUWE-VAPID-SLEUTELS.txt`. Bestaande push-abonnementen moeten
  daarna opnieuw subscriben; verder breekt er niets.

De cronjob heeft zijn eigen kopie van de S3-variabelen nodig.

## Stap 3 — Data overzetten (proefronde)

```bash
# Op de oude database: dump maken
pg_dump "$OUDE_DATABASE_URL" --no-owner --no-acl -Fc -f fieldops.dump

# In de nieuwe database terugzetten
pg_restore -d "$NIEUWE_DATABASE_URL" --no-owner --no-acl --clean --if-exists fieldops.dump
```

Beide connectiestrings staan in Render onder de betreffende database bij
**External Database URL**.

`main.py` draait bij het opstarten zijn eigen migratieblok (`create_all` plus
handgeschreven `ALTER TABLE`s). Laat de nieuwe service dus één keer opstarten
ná de restore, en kijk in de logs of daar geen `[migration] Waarschuwing`
tussen staat.

## Stap 4 — Controleren vóór het domein omgaat

De nieuwe service heeft een eigen `onrender.com`-adres. Loop dat langs:

- [ ] `/api/health` geeft `{"status":"ok"}`
- [ ] Inloggen werkt met een bestaand account
- [ ] Een project openen en een melding met foto bekijken — laadt de foto?
- [ ] Een inspectierapport als PDF genereren
- [ ] `/api/status/detailed` — staat `error_tracking` nu op geconfigureerd?
- [ ] `/RENDER-SETUP.md` geeft **404** (hoort dicht te zijn sinds PR #147)
- [ ] Cronjob handmatig draaien via **Trigger Run** en controleren dat er een
      nieuw bestand in de back-upbucket verschijnt

## Stap 5 — Omzetten (het kwartier)

1. Zet de oude service op onderhoud of schaal hem naar nul, zodat er niets meer
   bijkomt.
2. Laatste `pg_dump` / `pg_restore` — nu gaat het snel, want het verschil is
   klein.
3. Verplaats het custom domain `portaal.fieldopsapp.nl` van de oude naar de
   nieuwe service en pas de DNS aan zoals Render aangeeft.
4. Wacht tot het certificaat is uitgegeven en controleer opnieuw stap 4.

**Terug kunnen:** ging er iets mis, zet het domein terug op de oude service. Die
draait nog en heeft de data van vlak voor de omzetting.

## Stap 6 — Opruimen (na een week)

- Oude `fieldops-api` en `fieldops-db` verwijderen.
- `-eu` uit de namen halen als je dat netter vindt.
- `NIEUWE-VAPID-SLEUTELS.txt` van je schijf verwijderen.

---

## Wat hierna nog steeds niet klopt

Verhuizen zet je data in de EU. Het maakt **niet** alle claims op je
compliance-pagina waar, en dat moet je apart rechtzetten:

- **"Geen doorgifte buiten de EU"** blijft onwaar. `inspections.py:499` stuurt
  inspectiefoto's naar `api.anthropic.com` in de Verenigde Staten. Zet Anthropic
  op de sub-verwerkerslijst en noem de AI-analyse in de privacyverklaring.
- **"Geen Cloud Act-risico"** blijft onwaar zolang je op Render zit. Render is
  een Amerikaans bedrijf, en de CLOUD Act volgt het bedrijf, niet de server.
  Wil je die zin behouden, dan is een Europese aanbieder nodig
  (Clever Cloud, Scaleway). Anders: zin schrappen.
