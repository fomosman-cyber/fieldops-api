"""Import van het project 'SOK Amsterdam - Asfalt 2026' met wegen en meldingen.

Bron: drie PDF-rapporten van Gemeente Amsterdam (Asfalt Schouw NOORD van
26-08-2026, Notities Asfalt NOORD 2026 en Herstelwerkzaamheden Noord). De
uitgelezen inhoud staat in data/sok_amsterdam_asfalt_2026.json; dit script
zet die om naar een project, wegvak-assets en meldingen.

Wat er wordt aangemaakt
  - 1 project  "SOK Amsterdam - Asfalt 2026" (gemeente Amsterdam), met het
    projectgebied als GeoJSON-omhullende van alle geschouwde punten
  - 48 assets  één wegvak-asset per weg, geplaatst op het zwaartepunt van de
    meldingen op die weg, gekoppeld aan het project
  - 156 meldingen met de GPS-coordinaat uit het rapport, gekoppeld aan het
    project en aan de weg-asset, met de schouwfoto uit het rapport als
    `photo_url` (de voor-situatie). `photo_after_url` blijft leeg: dat is de
    foto die de uitvoerder op locatie maakt.

Werksoort
  Elke melding krijgt categorie "Asfalt - nog in te delen". Deel ze in het
  portaal in als hotbox (rood), asfalt machinaal (groen) of scheuren vullen
  (oranje) via de bulk-actie 'Werksoort wijzigen'; de kaart kleurt de punten
  daarna automatisch. Zie werksoorten.py.

Gebruik (vanuit fieldops-api/)
    python import_sok_amsterdam.py --org "Naam van je organisatie"
    python import_sok_amsterdam.py --org-id <id> --user <e-mailadres>
    python import_sok_amsterdam.py --dry-run        # toont alleen wat er zou gebeuren
    python import_sok_amsterdam.py --csv uitvoer/   # schrijft de import-CSV's weg

Zonder --org wordt de enige organisatie in de database gekozen; zijn er
meerdere, dan stopt het script en toont het de keuzes. Zonder --user wordt de
eerste actieve admin van die organisatie als aanmaker gebruikt.

Het script is idempotent: het matcht op projectnaam, asset-code en de
bronreferentie onderaan elke omschrijving. Een tweede run maakt geen
duplicaten. Bij bestaande meldingen wint het veldwerk: een in het portaal
gekozen werksoort, een verplaatste pin of een aangepaste titel blijft staan,
alleen lege velden worden aangevuld. De schouwfoto wordt wel steeds ververst,
zodat je later betere opnames kunt inladen. Met --overschrijf wint het
databestand alsnog op alle velden.
"""
from __future__ import annotations

import argparse
import base64
import re
import csv
import json
import mimetypes
import sys
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from database import SessionLocal
from models import Asset, Melding, Organization, Project, User, UserRole
from werksoorten import ONBEPAALD

DATA_FILE = Path(__file__).parent / "data" / "sok_amsterdam_asfalt_2026.json"
FOTO_MAP = Path(__file__).parent / "data" / "sok_amsterdam_fotos"

# Alle meldingen komen als open werkvoorraad binnen. De rapporten kennen geen
# urgentie toe, dus we verzinnen er ook geen: alles start op "normaal".
STATUS = "open"
PRIORITEIT = "normaal"


# ── helpers ───────────────────────────────────────────────────────────────
def foto_data_url(bestandsnaam: str | None) -> str | None:
    """Schouwfoto als data-URL. De rapporten zijn scans, dus de foto's zijn uit
    de paginabitmap geknipt en liggen als JPEG naast het databestand."""
    if not bestandsnaam:
        return None
    pad = FOTO_MAP / bestandsnaam
    if not pad.exists():
        return None
    mime = mimetypes.guess_type(pad.name)[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(pad.read_bytes()).decode()


def laad_data() -> dict:
    if not DATA_FILE.exists():
        sys.exit(f"Databestand ontbreekt: {DATA_FILE}")
    with DATA_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def kies_organisatie(db: Session, naam: str | None, org_id: str | None) -> Organization:
    if org_id:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            sys.exit(f"Geen organisatie met id {org_id}")
        return org
    if naam:
        org = db.query(Organization).filter(Organization.name == naam).first()
        if not org:
            beschikbaar = [o.name for o in db.query(Organization).all()]
            sys.exit(f"Geen organisatie '{naam}'. Beschikbaar: {beschikbaar}")
        return org
    orgs = db.query(Organization).all()
    if len(orgs) == 1:
        return orgs[0]
    if not orgs:
        sys.exit("Er staat nog geen organisatie in de database.")
    sys.exit("Meerdere organisaties gevonden — kies er een met --org:\n  "
             + "\n  ".join(f"{o.name}  (--org-id {o.id})" for o in orgs))


def kies_gebruiker(db: Session, org: Organization, email: str | None) -> User:
    if email:
        user = db.query(User).filter(
            User.email == email, User.organization_id == org.id).first()
        if not user:
            sys.exit(f"Geen gebruiker {email} in organisatie {org.name}")
        return user
    user = (db.query(User)
            .filter(User.organization_id == org.id, User.is_active == True)  # noqa: E712
            .order_by(User.role != UserRole.ADMIN, User.created_at)
            .first())
    if not user:
        sys.exit(f"Geen actieve gebruiker in organisatie {org.name}")
    return user


# ── import-stappen ────────────────────────────────────────────────────────
def upsert_project(db: Session, data: dict, org: Organization, user: User) -> Project:
    p = data["project"]
    project = db.query(Project).filter(
        Project.organization_id == org.id, Project.name == p["naam"]).first()
    if project is None:
        project = Project(name=p["naam"], organization_id=org.id, created_by=user.id)
        db.add(project)
    project.description = p["omschrijving"]
    project.gemeente = p["gemeente"]
    project.color = p["kleur"]
    project.categories = json.dumps(p["categories"], ensure_ascii=False)
    project.boundary_geojson = json.dumps(p["boundary_geojson"], ensure_ascii=False)
    db.flush()
    return project


def upsert_wegen(db: Session, data: dict, org: Organization, user: User,
                 project: Project) -> dict[str, Asset]:
    """Eén asset per weg. Match op asset-code binnen de organisatie."""
    codes = [w["code"] for w in data["wegen"]]
    bestaand = {a.code: a for a in db.query(Asset).filter(
        Asset.organization_id == org.id, Asset.code.in_(codes)).all()}

    assets: dict[str, Asset] = {}
    for w in data["wegen"]:
        asset = bestaand.get(w["code"])
        if asset is None:
            asset = Asset(code=w["code"], organization_id=org.id, created_by=user.id)
            db.add(asset)
        asset.name = w["naam"]
        asset.asset_type = w["asset_type"]
        asset.lat = w["lat"]
        asset.lng = w["lng"]
        asset.location_description = w["locatie_omschrijving"]
        asset.project_id = project.id
        assets[w["code"]] = asset
    db.flush()
    return assets


REF_RE = re.compile(r"Bronreferentie:\s*(SOK-AMS-2026-\d+)")


def upsert_meldingen(db: Session, data: dict, org: Organization, user: User,
                     project: Project, assets: dict[str, Asset],
                     overschrijf: bool = False) -> tuple[int, int, int]:
    """Meldingen aanmaken/bijwerken.

    Gematcht op de bronreferentie onderaan de omschrijving, niet op de titel:
    een titel kan nog bijgeschaafd worden en zou dan een duplicaat opleveren.

    Bij een bestaande melding wint het veldwerk. Alleen lege velden worden
    aangevuld; een in het portaal gekozen werksoort, een verplaatste pin of een
    aangepaste titel blijft staan. De foto uit het rapport wordt wel steeds
    ververst — dat is juist de reden om opnieuw te importeren als er betere
    opnames binnenkomen. Met `overschrijf=True` wint het databestand alsnog op
    alle velden.
    """
    bestaand = {}
    for m in db.query(Melding).filter(
            Melding.organization_id == org.id,
            Melding.project_id == project.id).all():
        if (hit := REF_RE.search(m.description or "")):
            bestaand[hit.group(1)] = m

    nieuw = bijgewerkt = 0
    met_foto = 0
    for m in data["meldingen"]:
        melding = bestaand.get(m["ref"])
        if melding is None:
            melding = Melding(organization_id=org.id, created_by=user.id, status=STATUS)
            db.add(melding)
            nieuw += 1
        else:
            bijgewerkt += 1
        vers = overschrijf or melding.id is None
        if vers or not melding.title:
            melding.title = m["titel"]
        if vers or not melding.description:
            melding.description = m["omschrijving"]
        if vers or not melding.category:
            melding.category = ONBEPAALD
        if vers or not melding.priority:
            melding.priority = PRIORITEIT
        if vers or melding.lat is None:
            melding.lat = m["lat"]
            melding.lng = m["lng"]
        # De schouwfoto komt uit het rapport en wordt wel steeds ververst.
        if (foto := foto_data_url(m.get("foto"))):
            melding.photo_url = foto
        melding.project_id = project.id
        asset = assets.get(m["asset_code"])
        melding.asset_id = asset.id if asset else None
        if melding.photo_url:
            met_foto += 1
    db.flush()
    return nieuw, bijgewerkt, met_foto


# ── CSV-uitvoer (terugval voor de import-schermen in het portaal) ─────────
def schrijf_csv(data: dict, map_pad: str) -> list[str]:
    doel = Path(map_pad)
    doel.mkdir(parents=True, exist_ok=True)
    project_naam = data["project"]["naam"]
    paden = []

    weg_pad = doel / "sok-amsterdam-wegen.csv"
    with weg_pad.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["code", "asset_type", "name", "lat", "lng", "location_description"])
        for weg in data["wegen"]:
            w.writerow([weg["code"], weg["asset_type"], weg["naam"],
                        weg["lat"], weg["lng"], weg["locatie_omschrijving"]])
    paden.append(str(weg_pad))

    meld_pad = doel / "sok-amsterdam-meldingen.csv"
    with meld_pad.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["title", "description", "category", "priority",
                    "lat", "lng", "project", "asset_code", "foto"])
        for m in data["meldingen"]:
            w.writerow([m["titel"], m["omschrijving"], ONBEPAALD, PRIORITEIT,
                        m["lat"] if m["lat"] is not None else "",
                        m["lng"] if m["lng"] is not None else "",
                        project_naam, m["asset_code"], m.get("foto") or ""])
    paden.append(str(meld_pad))

    # De CSV-import van meldingen accepteert een .zip met foto's; de fotokolom
    # verwijst naar de bestandsnamen daarin.
    foto_pad = doel / "sok-amsterdam-fotos.zip"
    with zipfile.ZipFile(foto_pad, "w", zipfile.ZIP_STORED) as z:
        for m in data["meldingen"]:
            if m.get("foto") and (FOTO_MAP / m["foto"]).exists():
                z.write(FOTO_MAP / m["foto"], m["foto"])
    paden.append(str(foto_pad))
    return paden


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", help="naam van de organisatie")
    ap.add_argument("--org-id", help="id van de organisatie (gaat voor --org)")
    ap.add_argument("--user", help="e-mailadres van de aanmaker")
    ap.add_argument("--dry-run", action="store_true",
                    help="toon wat er zou gebeuren, schrijf niets weg")
    ap.add_argument("--overschrijf", action="store_true",
                    help="zet ook velden terug die in het portaal zijn aangepast "
                         "(werksoort, titel, pin); standaard blijven die staan")
    ap.add_argument("--csv", metavar="MAP",
                    help="schrijf de import-CSV's naar deze map en stop")
    args = ap.parse_args()

    data = laad_data()

    if args.csv:
        for pad in schrijf_csv(data, args.csv):
            print(f"geschreven: {pad}")
        return

    db = SessionLocal()
    try:
        org = kies_organisatie(db, args.org, args.org_id)
        user = kies_gebruiker(db, org, args.user)
        print(f"organisatie : {org.name}")
        print(f"aanmaker    : {user.email}")

        project = upsert_project(db, data, org, user)
        assets = upsert_wegen(db, data, org, user, project)
        nieuw, bijgewerkt, met_foto = upsert_meldingen(
            db, data, org, user, project, assets, overschrijf=args.overschrijf)

        zonder_gps = [m["titel"] for m in data["meldingen"] if m["lat"] is None]
        gemarkeerd = [m["titel"] for m in data["meldingen"] if m["gps_waarschuwing"]]

        print(f"project     : {project.name}")
        print(f"wegen       : {len(assets)} assets")
        print(f"meldingen   : {nieuw} nieuw, {bijgewerkt} bijgewerkt")
        print(f"foto's      : {met_foto} van {len(data['meldingen'])} meldingen")
        if zonder_gps:
            print(f"LET OP      : {len(zonder_gps)} melding(en) zonder GPS in de bron — "
                  f"coordinaat handmatig aanvullen:")
            for t in zonder_gps:
                print(f"              - {t}")
        losse_wegen = [w["naam"] for w in data["wegen"] if w["lat"] is None]
        if losse_wegen:
            print(f"LET OP      : {len(losse_wegen)} weg(en) zonder coordinaat "
                  f"(volgt uit de melding erop): {', '.join(losse_wegen)}")
        if gemarkeerd:
            print(f"LET OP      : {len(gemarkeerd)} coordinaat(en) buiten het "
                  f"verwachte gebied — controleer in het portaal:")
            for t in gemarkeerd:
                print(f"              - {t}")

        if args.dry_run:
            db.rollback()
            print("\ndry-run: niets weggeschreven.")
        else:
            db.commit()
            print("\nklaar.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
