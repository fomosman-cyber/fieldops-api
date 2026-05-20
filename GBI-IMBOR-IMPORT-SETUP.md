# GBI / iAsset / Antea integratie met FieldOps

GBI World, iAsset en Antea Beheer zijn populaire beheer-systemen voor Nederlandse gemeenten. FieldOps draait **naast** je bestaande GBI — geen vervanging, wel veld-laag erbij.

## Drie integratie-paden

### 1. Deep-link (klaar — 5 min setup)
Per melding in FieldOps verschijnt een knop **🏢 [Label]** die opent in je beheer-systeem op de juiste locatie of asset.

**Setup:**
1. Login → **Instellingen → Organisatie** → 🏢 **Extern beheer-systeem**
2. Label: `GBI World` (of `iAsset` / je eigen label)
3. URL-template (gebruik placeholders):

```
GBI World:   https://www.gbiworld.nl/Asset/?code={asset_code}
iAsset:      https://klant.iasset.nl/portaal/?location={lat},{lng}
Custom:      https://intern.gemeente.nl/beheer/{asset_code}
```

**Beschikbare placeholders:**
- `{lat}` — latitude van melding
- `{lng}` — longitude
- `{asset_code}` — code van gekoppelde asset (of leeg)
- `{melding_id}` — UUID van de melding

4. **Organisatie opslaan** — klaar.

### 2. Asset-import via IMBOR-CSV (bestaande feature)
GBI/iAsset/Antea kunnen je areaal-data exporteren naar **IMBOR**-conform CSV. FieldOps heeft een flexibele importer met auto-mapping van Nederlandse kolomnamen.

**Endpoint:** `POST /api/assets/import/csv`
**UI:** Assets → Import (bulk-upload via portaal — komt in volgende UI-PR)

**Auto-gemapte kolommen** (de importer herkent Nederlandse synoniemen):

| FieldOps-veld | Geaccepteerde kolomnamen in CSV |
|---|---|
| `code` | code, objectnummer, nummer, asset_id, kenmerk |
| `asset_type` | asset_type, objecttype, type, categorie, soort, hoofdgroep |
| `name` | name, naam, omschrijving, beschrijving |
| `lat` / `lng` | lat/lng, latitude/longitude, breedte/lengte, **RD x/y** (auto-converted) |
| `location_description` | locatie, adres, straat, plaats |
| `parent_code` | parent_code, parent, hoofdobject, ouder_code |

**Stappen vanuit GBI:**

1. Login op GBI World → Modules → **Export**
2. Kies object-type (bv. Bomen, Lichtmasten, Riolering)
3. Format: **CSV (semicolon-delimited)** of **IMBOR-XML** → converteer naar CSV
4. Upload via FieldOps Assets → bulk-import
5. **Bestaande codes worden geüpdatet, nieuwe aangemaakt** (idempotent)

**Velden om mee te nemen** (voor maximale data-kwaliteit):
- Object-code (verplicht, uniek)
- Object-type / categorie (verplicht)
- Locatie: lat/lng OF RD x/y OF straat+huisnummer
- Conditiescore (NEN 2767-2 of CROW)
- Bouwjaar / installatie-datum
- Eigenaar (gemeente / provincie / waterschap)

### 3. API-koppeling (vraagt contract met Antea)
Voor **realtime** synchronisatie tussen GBI en FieldOps heb je nodig:

- API-credentials van Antea Group (commercieel contract)
- REST-endpoint URL + authenticatie-method (waarschijnlijk OAuth2)
- Field-mapping (FieldOps Asset ↔ GBI Object)
- Sync-frequentie (live / hourly / daily)

**Email-template** om Antea te benaderen — stuur naar je accountmanager bij Antea:

```
Onderwerp: API-integratie GBI ↔ FieldOps

Beste [accountmanager],

Wij gebruiken FieldOps (https://fieldopsapp.nl) voor veld-inspecties
naast GBI World. Op dit moment werken we met handmatige CSV-export uit
GBI naar FieldOps.

We zouden graag een API-koppeling realiseren waarbij:
- FieldOps assets ophaalt uit GBI (read-only)
- Meldingen uit FieldOps worden gepost naar GBI als werkbon

Specifiek vragen we:
1. Documentatie van de GBI REST API (endpoints + schemas)
2. Beschikbaarheid van OAuth2 of API-key authenticatie
3. Welke object-types / velden via API beschikbaar zijn
4. Test-omgeving voor ontwikkeling
5. Indicatie van commerciële voorwaarden

Onze klant-organisatie: [GEMEENTE-NAAM]
Contact: faris@fieldopsapp.nl

Met vriendelijke groet,
[Naam]
```

## Veelvoorkomende vragen

**V: Waarom niet GBI volledig vervangen?**
A: FieldOps is veld-laag (mobiel inspecteren, melden, snel rapporteren). GBI is kantoor-laag (areaal-database, MJOP, financiële koppeling). Samen werken ze beter dan elk apart. Bovendien is migratie-shock duur — daar lopen aanbestedingen op stuk.

**V: Wat zijn IMBOR en NLCS?**
A: **IMBOR** = Informatiemodel Beheer Openbare Ruimte (Nederlandse standaard van CROW). **NLCS** = Nederlandse CAD-standaard. Beide zijn structuren voor areaal-data. GBI exporteert IMBOR-conform.

**V: Wat als mijn klant geen GBI gebruikt maar Wegmanagement Online of een eigen tool?**
A: Zelfde mechanisme — vul de URL-template van die tool in. De CSV-importer pakt sowieso veelvoorkomende kolomnamen op.

**V: Realtime push van melding → GBI als werkbon?**
A: Vereist API-koppeling (optie 3). Vraag Antea om webhook-endpoint of bidirectional sync-API.

**V: Hoe vaak moet ik importeren?**
A: Bij eerste setup: complete asset-set. Daarna alleen bij grote wijzigingen (nieuwe wijk, herinventarisatie). Voor dagelijkse synchronisatie heb je optie 3 nodig.

## Roadmap voor volledige Antea-integratie

| Versie | Wat | Wanneer |
|---|---|---|
| **Nu (v1)** | Deep-link + CSV-import (auto-mapping) | ✅ Live |
| **v2** | UI voor bulk-import wizard (preview + field-mapping confirmatie) | Q3 2026 |
| **v3** | Read-only Antea API (asset-pull) | Bij eerste klant met API-license |
| **v4** | Bidirectionele sync (melding → werkbon push naar GBI) | Bij 3+ klanten die het vragen |

## Compliance + data-privacy

- IMBOR-data is meestal **publiek beschikbaar** (BGT/areaal-info). Geen AVG-risico.
- **Persoonsgegevens** (melder-naam, telefoon) blijven in FieldOps, gaan **niet** naar GBI.
- Sync is **read-only** in v3 → geen risico op data-loss in GBI.

## Status van deze feature in FieldOps

- ✅ **Deep-link knop op melding** — live nu
- ✅ **CSV-import met auto-mapping** — bestaat al
- ✅ **Setup-handleiding** — deze pagina
- ⏸️ **UI bulk-import wizard** — komt in volgende sessie
- ⏸️ **API-koppeling** — wacht op klant die het bestelt
