"""Demo-seed voor een GEISOLEERDE 'Demo Gemeente'-organisatie.

Doel: een complete, realistische dataset voor sales-demos zonder ooit echte
klantdata aan te raken. Alles wordt aangemaakt onder een aparte organisatie
("Demo Gemeente"); het script raakt nooit een andere org aan.

Bevat:
  - 1 org "Demo Gemeente" (plan PROFESSIONAL, status ACTIVE)
  - 4 users incl. inlog demo@fieldopsapp.nl (org-admin)
  - 3 projecten (Wegen / Kunstwerken / Riolering - gemeente Westland)
  - ~50 geo-gelokaliseerde assets (Westland-kernen)
  - ~30 meldingen, gespreid over de laatste ~6 maanden t/m vandaag
  - ~10 kunstwerk-inspecties (NEN 2767-2) met element-decompositie
  - 4 opleveringen met opleverpunten

Inloggegevens
  e-mail:     demo@fieldopsapp.nl
  wachtwoord: env DEMO_PASSWORD  (fallback: 'DemoFieldOps2026!' met waarschuwing)

Gebruik (vanuit fieldops-api/):
  python seed_demo_org.py            # idempotent: maakt aan wat nog niet bestaat
  python seed_demo_org.py --reset    # verwijdert ALLE demo-org-data en herbouwt

Veiligheid:
  --reset verwijdert uitsluitend records met organization_id == de demo-org.
  Andere organisaties (echte klanten) worden nooit geraakt.

Determinisme:
  Alle 'random' keuzes worden per record afgeleid uit een vaste sleutel (asset-
  code / titel), niet uit een gedeelde RNG-stream. Daardoor is het script echt
  idempotent: re-runs maken geen duplicaten, ongeacht volgorde of proces.
"""
from __future__ import annotations

import argparse
import json
import os
import random
from datetime import datetime, timedelta, timezone

from auth import hash_password
from database import SessionLocal
from kunstwerken_taxonomy import elementen_voor, normalize_type
from models import (
    AccountStatus,
    Asset,
    Inspection,
    InspectionDefect,
    InspectionElement,
    Melding,
    Oplevering,
    OpleveringPunt,
    Organization,
    Project,
    SubscriptionPlan,
    User,
    UserRole,
)

ORG_NAME = "Demo Gemeente"
DEMO_EMAIL = "demo@fieldopsapp.nl"
_FALLBACK_PASSWORD = "DemoFieldOps2026!"


def _rng(*parts) -> random.Random:
    """Deterministische RNG afgeleid uit een stabiele sleutel (geen gedeelde
    stream) -> volledig idempotent over runs en volgorde heen."""
    return random.Random("|".join(str(p) for p in parts))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def demo_password() -> str:
    pw = os.environ.get("DEMO_PASSWORD")
    if pw:
        return pw
    print(f"  ! DEMO_PASSWORD niet gezet - fallback '{_FALLBACK_PASSWORD}' gebruikt.")
    return _FALLBACK_PASSWORD


# -- Westland-kernen met centrum-coordinaten (lat, lng) ----------------------
KERNEN = {
    "Naaldwijk": (51.9940, 4.2070),
    "Monster": (52.0190, 4.1730),
    "'s-Gravenzande": (52.0010, 4.1620),
    "Wateringen": (52.0220, 4.2660),
    "De Lier": (51.9760, 4.2540),
    "Honselersdijk": (52.0040, 4.2210),
    "Poeldijk": (52.0180, 4.2080),
}


def _coord(code: str, kern: str) -> tuple[float, float]:
    """Een plausibele, deterministische coordinaat binnen ~600 m van het kern-
    centrum (afgeleid uit de asset-code)."""
    base_lat, base_lng = KERNEN[kern]
    r = _rng("coord", code)
    return (
        round(base_lat + r.uniform(-0.006, 0.006), 6),
        round(base_lng + r.uniform(-0.008, 0.008), 6),
    )


# -- Asset-specs per project. (code, naam, asset_type, kern) -----------------
# asset_type is bewust simpel/canoniek zodat kunstwerk-inspecties via
# normalize_type() blijven werken (brug/viaduct/duiker/sluis/...).
def _asset_blueprints() -> dict[str, list[tuple[str, str, str, str]]]:
    return {
        "Wegen & Verharding Westland": [
            ("WL-WV-001", "Dijkweg wegvak 1 (asfalt)", "wegvak_asfalt", "Naaldwijk"),
            ("WL-WV-002", "Galgeweg wegvak 2 (asfalt)", "wegvak_asfalt", "Naaldwijk"),
            ("WL-WV-003", "Monsterseweg wegvak 1 (asfalt)", "wegvak_asfalt", "Monster"),
            ("WL-WV-004", "Naaldwijkseweg wegvak 3 (asfalt)", "wegvak_asfalt", "Honselersdijk"),
            ("WL-WV-005", "Kerkstraat (klinkerverharding)", "wegvak_elementen", "De Lier"),
            ("WL-WV-006", "Molenstraat (klinkerverharding)", "wegvak_elementen", "'s-Gravenzande"),
            ("WL-FP-001", "Fietspad Lange Broekweg", "fietspad", "Naaldwijk"),
            ("WL-FP-002", "Fietspad Poeldijkseweg", "fietspad", "Poeldijk"),
            ("WL-FP-003", "Fietspad Vlietweg", "fietspad", "Wateringen"),
            ("WL-FP-004", "Fietspad Maesemundeweg", "fietspad", "Monster"),
            ("WL-TR-001", "Trottoir Hoflaan oostzijde", "trottoir", "Naaldwijk"),
            ("WL-TR-002", "Trottoir Verspuijstraat", "trottoir", "De Lier"),
            ("WL-TR-003", "Trottoir Kerkplein", "trottoir", "'s-Gravenzande"),
            ("WL-TR-004", "Trottoir Wilhelminaplein", "trottoir", "Wateringen"),
            ("WL-LM-001", "Lichtmast Dijkweg 12", "lantaarnpaal", "Naaldwijk"),
            ("WL-LM-002", "Lichtmast Dijkweg 28", "lantaarnpaal", "Naaldwijk"),
            ("WL-LM-003", "Lichtmast Galgeweg 4", "lantaarnpaal", "Naaldwijk"),
            ("WL-LM-004", "Lichtmast Monsterseweg 55", "lantaarnpaal", "Monster"),
            ("WL-LM-005", "Lichtmast Poeldijkseweg 9", "lantaarnpaal", "Poeldijk"),
            ("WL-LM-006", "Lichtmast Vlietweg 71", "lantaarnpaal", "Wateringen"),
            ("WL-LM-007", "Lichtmast Kerkstraat 3", "lantaarnpaal", "De Lier"),
            ("WL-LM-008", "Lichtmast Molenstraat 18", "lantaarnpaal", "'s-Gravenzande"),
        ],
        "Kunstwerken Westland": [
            ("WL-BR-001", "Hoflaanbrug", "brug", "Naaldwijk"),
            ("WL-BR-002", "Gantelbrug", "brug", "Honselersdijk"),
            ("WL-BR-003", "Zwethbrug", "brug", "De Lier"),
            ("WL-BR-004", "Molenslootbrug", "brug", "Monster"),
            ("WL-BR-005", "Vlietbrug Wateringen", "brug", "Wateringen"),
            ("WL-VI-001", "Viaduct N213 Naaldwijk", "viaduct", "Naaldwijk"),
            ("WL-VI-002", "Viaduct A20 De Lier", "viaduct", "De Lier"),
            ("WL-DU-001", "Duiker Gantel km 1.2", "duiker", "Honselersdijk"),
            ("WL-DU-002", "Duiker Zweth km 0.4", "duiker", "De Lier"),
            ("WL-DU-003", "Duiker Monstersevaart", "duiker", "Monster"),
            ("WL-SL-001", "Sluis Boomawatering", "sluis", "Naaldwijk"),
            ("WL-KM-001", "Kademuur Naaldwijkse haven", "kademuur", "Naaldwijk"),
            ("WL-SP-001", "Speeltoestel Wilhelminapark", "speeltoestel", "'s-Gravenzande"),
            ("WL-SP-002", "Speeltoestel Julianapark", "speeltoestel", "Wateringen"),
            ("WL-BM-001", "Laanboom Dijkweg (iep)", "boom", "Naaldwijk"),
            ("WL-BM-002", "Monumentale linde Kerkplein", "boom", "'s-Gravenzande"),
        ],
        "Riolering & Water Westland": [
            ("WL-PUT-001", "Inspectieput Dijkweg 12", "put", "Naaldwijk"),
            ("WL-PUT-002", "Inspectieput Galgeweg 30", "put", "Naaldwijk"),
            ("WL-PUT-003", "Inspectieput Monsterseweg 55", "put", "Monster"),
            ("WL-PUT-004", "Inspectieput Kerkstraat 3", "put", "De Lier"),
            ("WL-PUT-005", "Inspectieput Vlietweg 71", "put", "Wateringen"),
            ("WL-PUT-006", "Inspectieput Poeldijkseweg 9", "put", "Poeldijk"),
            ("WL-KOL-001", "Straatkolk Dijkweg 12a", "kolk", "Naaldwijk"),
            ("WL-KOL-002", "Straatkolk Galgeweg 30a", "kolk", "Naaldwijk"),
            ("WL-KOL-003", "Straatkolk Monsterseweg 55a", "kolk", "Monster"),
            ("WL-KOL-004", "Straatkolk Kerkstraat 3a", "kolk", "De Lier"),
            ("WL-GM-001", "Rioolgemaal Naaldwijk-Oost", "gemaal", "Naaldwijk"),
            ("WL-DR-001", "Drainage Honselersdijk West", "drainage", "Honselersdijk"),
        ],
    }


# Verwachte levensduur per asset-type (jaren) - voor predictive/Voorspeller.
_LIFESPAN = {
    "brug": 80, "viaduct": 80, "duiker": 60, "sluis": 80, "kademuur": 60,
    "lantaarnpaal": 40, "put": 60, "kolk": 40, "gemaal": 30, "drainage": 40,
    "speeltoestel": 15, "boom": 120,
    "wegvak_asfalt": 24, "wegvak_elementen": 40, "fietspad": 30, "trottoir": 40,
}


# -- Project-specs -----------------------------------------------------------
PROJECT_SPECS = [
    {
        "name": "Wegen & Verharding Westland",
        "description": "Beheer en onderhoud van wegvakken, fiets- en voetpaden en "
                       "openbare verlichting in gemeente Westland.",
        "gemeente": "Westland",
        "color": "#0284c7",
        "categories": ["wegdek", "verharding", "verlichting"],
    },
    {
        "name": "Kunstwerken Westland",
        "description": "Formele inspecties en onderhoud van bruggen, viaducten, "
                       "duikers, sluis en kademuren (NEN 2767-2 + CROW 134).",
        "gemeente": "Westland",
        "color": "#7c3aed",
        "categories": ["brug", "viaduct", "duiker", "kunstwerk"],
    },
    {
        "name": "Riolering & Water Westland",
        "description": "Inspectie en onderhoud van riolering, putten, kolken en "
                       "gemalen (NEN 3399).",
        "gemeente": "Westland",
        "color": "#16a34a",
        "categories": ["put", "kolk", "riolering", "gemaal"],
    },
]


# -- Reset -------------------------------------------------------------------
def reset_demo_org(db, org: Organization) -> None:
    """Verwijder ALLE data van uitsluitend deze demo-org, FK-veilig."""
    oid = org.id
    insp_ids = [i.id for i in db.query(Inspection.id).filter(Inspection.organization_id == oid)]
    elem_ids = [
        e.id for e in db.query(InspectionElement.id).filter(InspectionElement.organization_id == oid)
    ]
    if elem_ids:
        db.query(InspectionDefect).filter(InspectionDefect.element_id.in_(elem_ids)).delete(
            synchronize_session=False
        )
    if insp_ids:
        db.query(InspectionElement).filter(InspectionElement.inspection_id.in_(insp_ids)).delete(
            synchronize_session=False
        )
    db.query(Inspection).filter(Inspection.organization_id == oid).delete(synchronize_session=False)
    db.query(OpleveringPunt).filter(OpleveringPunt.organization_id == oid).delete(
        synchronize_session=False
    )
    db.query(Oplevering).filter(Oplevering.organization_id == oid).delete(synchronize_session=False)
    db.query(Melding).filter(Melding.organization_id == oid).delete(synchronize_session=False)
    db.query(Asset).filter(Asset.organization_id == oid).delete(synchronize_session=False)
    db.query(Project).filter(Project.organization_id == oid).delete(synchronize_session=False)
    db.query(User).filter(User.organization_id == oid).delete(synchronize_session=False)
    db.commit()
    print(f"  [reset] demo-org data verwijderd (org={oid})")


# -- Seed-stappen ------------------------------------------------------------
def get_or_create_org(db) -> Organization:
    org = db.query(Organization).filter(Organization.name == ORG_NAME).first()
    if not org:
        org = Organization(
            name=ORG_NAME,
            plan=SubscriptionPlan.PROFESSIONAL,
            status=AccountStatus.ACTIVE,
            max_users=25,
            contact_email=DEMO_EMAIL,
            brand_color="#0284c7",
        )
        db.add(org)
        db.commit()
        db.refresh(org)
        print(f"  + org {ORG_NAME}")
    else:
        print(f"  = org {ORG_NAME} bestaat al")
    return org


def seed_users(db, org: Organization) -> dict[str, User]:
    pw = demo_password()
    specs = [
        (DEMO_EMAIL, "Demo", "Beheerder", UserRole.ADMIN, True),
        ("inspecteur@demo-gemeente.nl", "Joey", "Bakker", UserRole.INSPECTOR, False),
        ("aannemer@demo-gemeente.nl", "Ramon", "de Wit", UserRole.CONTRACTOR, False),
        ("manager@demo-gemeente.nl", "Esther", "Vermeer", UserRole.MANAGER, False),
    ]
    users: dict[str, User] = {}
    for email, fn, ln, role, is_admin in specs:
        u = db.query(User).filter(User.email == email).first()
        if not u:
            u = User(
                email=email,
                hashed_password=hash_password(pw),
                first_name=fn,
                last_name=ln,
                role=role,
                is_active=True,
                is_org_admin=is_admin,
                must_change_password=False,
                organization_id=org.id,
            )
            db.add(u)
            db.flush()
            print(f"  + user {email} ({role.value})")
        users[role.value] = u
    db.commit()
    return users


def seed_projects(db, org: Organization, owner: User) -> dict[str, Project]:
    projects: dict[str, Project] = {}
    for spec in PROJECT_SPECS:
        p = (
            db.query(Project)
            .filter(Project.organization_id == org.id, Project.name == spec["name"])
            .first()
        )
        if not p:
            p = Project(
                name=spec["name"],
                description=spec["description"],
                gemeente=spec["gemeente"],
                status="active",
                color=spec["color"],
                categories=json.dumps(spec["categories"]),
                organization_id=org.id,
                created_by=owner.id,
            )
            db.add(p)
            db.flush()
            print(f"  + project {p.name}")
        projects[spec["name"]] = p
    db.commit()
    return projects


def seed_assets(db, org: Organization, owner: User, projects: dict[str, Project]) -> list[Asset]:
    all_assets: list[Asset] = []
    for project_name, blueprints in _asset_blueprints().items():
        project = projects[project_name]
        for code, name, atype, kern in blueprints:
            a = (
                db.query(Asset)
                .filter(Asset.organization_id == org.id, Asset.code == code)
                .first()
            )
            if not a:
                lat, lng = _coord(code, kern)
                lifespan = _LIFESPAN.get(atype, 30)
                r = _rng("asset", code)
                age_years = r.randint(2, max(3, lifespan - 5))
                a = Asset(
                    code=code,
                    name=name,
                    asset_type=atype,
                    lat=lat,
                    lng=lng,
                    location_description=f"{kern} - {name}",
                    project_id=project.id,
                    organization_id=org.id,
                    installed_at=_now() - timedelta(days=age_years * 365),
                    expected_lifespan_years=lifespan,
                    condition_score=r.choice([1, 2, 2, 3, 3, 3, 4, 4, 5]),
                    created_by=owner.id,
                )
                db.add(a)
                db.flush()
            all_assets.append(a)
    db.commit()
    print(f"  + {len(all_assets)} assets (Westland)")
    return all_assets


# Melding-templates per asset-type: (titel-sjabloon, categorie, kandidaat-prioriteiten)
_MELDING_TEMPLATES = {
    "wegvak_asfalt": [("Scheurvorming in asfalt bij {n}", "schade", ["normaal", "hoog"]),
                      ("Spoorvorming rijbaan {n}", "schade", ["normaal"])],
    "wegvak_elementen": [("Verzakte klinkers {n}", "schade", ["normaal", "hoog"])],
    "fietspad": [("Wortelopdruk fietspad {n}", "schade", ["normaal", "hoog"])],
    "trottoir": [("Losliggende tegel trottoir {n}", "schade", ["laag", "normaal"])],
    "lantaarnpaal": [("Lichtmast {n} brandt niet", "schade", ["normaal", "hoog"]),
                     ("Scheve lichtmast {n} na aanrijding", "schade", ["hoog", "kritiek"])],
    "brug": [("Betonschade onderbouw {n}", "inspectie", ["normaal", "hoog"]),
             ("Lekkage voegovergang {n}", "schade", ["normaal"])],
    "viaduct": [("Scheurvorming landhoofd {n}", "inspectie", ["hoog"])],
    "duiker": [("Verstopping duiker {n}", "schade", ["normaal", "hoog"])],
    "sluis": [("Sluisdeur {n} sluit niet volledig", "schade", ["hoog", "kritiek"])],
    "kademuur": [("Uitspoeling achter kademuur {n}", "inspectie", ["hoog"])],
    "speeltoestel": [("Speeltoestel {n} - losse bout geconstateerd", "schade", ["hoog", "kritiek"])],
    "boom": [("Dode tak in {n} boven fietspad", "schade", ["hoog"])],
    "put": [("Putdeksel {n} ligt los", "schade", ["normaal", "hoog"]),
            ("Routine-inspectie put {n}", "inspectie", ["laag"])],
    "kolk": [("Kolk {n} verstopt - water op straat", "schade", ["normaal", "hoog"])],
    "gemaal": [("Pompstoring gemaal {n}", "schade", ["kritiek"])],
    "drainage": [("Drainage {n} functioneert onvoldoende", "inspectie", ["normaal"])],
}

_STATUSSEN = ["open", "open", "in_behandeling", "in_behandeling", "afgerond", "afgerond", "opgelost"]


def seed_meldingen(db, org: Organization, users: dict[str, User], assets: list[Asset],
                   target: int = 30) -> int:
    creators = list(users.values())
    pool = [a for a in assets if a.asset_type in _MELDING_TEMPLATES]
    # Deterministische, gespreide selectie van ~target assets.
    pool.sort(key=lambda a: _rng("meldpick", a.code).random())
    made = 0
    for asset in pool[:target]:
        r = _rng("meld", asset.code)
        tmpl, cat, prios = r.choice(_MELDING_TEMPLATES[asset.asset_type])
        title = tmpl.format(n=asset.name)
        exists = (
            db.query(Melding)
            .filter(Melding.organization_id == org.id, Melding.title == title)
            .first()
        )
        if exists:
            continue
        status = r.choice(_STATUSSEN)
        # Datums over de laatste ~6 maanden t/m vandaag -> vult de trend-grafiek.
        created = _now() - timedelta(days=r.randint(0, 175), hours=r.randint(0, 23))
        updated = created
        if status in ("afgerond", "opgelost"):
            updated = min(created + timedelta(days=r.randint(1, 21)), _now())
        m = Melding(
            title=title,
            description=f"Demo-melding bij {asset.name} ({asset.location_description}).",
            category=cat,
            priority=r.choice(prios),
            status=status,
            lat=asset.lat,
            lng=asset.lng,
            project_id=asset.project_id,
            asset_id=asset.id,
            organization_id=org.id,
            created_by=r.choice(creators).id,
            created_at=created,
            updated_at=updated,
        )
        db.add(m)
        made += 1
    db.commit()
    print(f"  + {made} meldingen (gespreid over ~6 mnd)")
    return made


_INSPECTABLE = {"brug", "viaduct", "duiker", "sluis", "kademuur", "speeltoestel", "boom"}
_INSP_STATUS = ["completed", "completed", "signed", "in_progress", "draft"]


def seed_inspecties(db, org: Organization, users: dict[str, User], assets: list[Asset],
                    target: int = 10) -> int:
    inspecteur = users.get("inspector") or users["admin"]
    candidates = [a for a in assets if normalize_type(a.asset_type) in _INSPECTABLE]
    candidates.sort(key=lambda a: _rng("insppick", a.code).random())
    made = 0
    for asset in candidates[:target]:
        ktype = normalize_type(asset.asset_type)
        title = f"NEN 2767-2 inspectie {asset.name}"
        exists = (
            db.query(Inspection)
            .filter(Inspection.organization_id == org.id, Inspection.title == title)
            .first()
        )
        if exists:
            continue
        r = _rng("insp", asset.code)
        datum = _now() - timedelta(days=r.randint(5, 160))
        status = r.choice(_INSP_STATUS)
        insp = Inspection(
            organization_id=org.id,
            asset_id=asset.id,
            kunstwerk_type=ktype,
            project_id=asset.project_id,
            title=title,
            inspectie_type="eerste-graads",
            datum_inspectie=datum,
            inspecteur_id=inspecteur.id,
            inspecteur_naam=f"{inspecteur.first_name} {inspecteur.last_name}",
            weersomstandigheden=r.choice(["droog, 8 C", "bewolkt, 12 C", "licht regen, 10 C"]),
            opdrachtgever_naam="Gemeente Westland",
            status=status,
            created_by=inspecteur.id,
        )
        db.add(insp)
        db.flush()

        elementen = elementen_voor(ktype)[:6] or [
            {"code": "ALG.OBJECT", "naam": asset.name, "groep": "constructief", "gebreken": []}
        ]
        worst = 1
        for idx, el in enumerate(elementen):
            score = _rng("elem", asset.code, el["code"]).choice([1, 2, 2, 3, 3, 3, 4])
            worst = max(worst, score)
            ie = InspectionElement(
                inspection_id=insp.id,
                organization_id=org.id,
                element_code=el["code"],
                element_naam=el["naam"],
                element_groep=el.get("groep"),
                beoordeeld=True,
                conditiescore=score,
                bevindingen="Visueel beoordeeld; geen bijzonderheden." if score <= 2
                else "Aandachtspunt geconstateerd, opvolging geadviseerd.",
                order_index=idx,
            )
            db.add(ie)
            db.flush()
            # Bij een matige/slechte score een representatief gebrek vastleggen.
            if score >= 4 and el.get("gebreken"):
                g = _rng("def", asset.code, el["code"]).choice(el["gebreken"])
                db.add(InspectionDefect(
                    element_id=ie.id,
                    organization_id=org.id,
                    gebrek_code=g.get("code"),
                    gebrek_naam=g.get("naam", "Gebrek"),
                    omschrijving="Geconstateerd tijdens demo-inspectie.",
                    ernst=2,
                    intensiteit=2,
                    omvang_klasse=3,
                    defect_score=score,
                ))
        insp.conditiescore_overall = worst
        if status in ("completed", "signed"):
            insp.aanbevolen_acties = (
                "Geen directe maatregelen." if worst <= 3
                else "Herinspectie binnen 12 maanden; herstel slechtste element inplannen."
            )
        if status == "signed":
            insp.signed_at = datum + timedelta(days=1)
            insp.signed_by = inspecteur.id
        made += 1
    db.commit()
    print(f"  + {made} kunstwerk-inspecties (met element-decompositie)")
    return made


def seed_opleveringen(db, org: Organization, users: dict[str, User], projects: dict[str, Project],
                      assets: list[Asset]) -> int:
    owner = users["admin"]
    aannemer = users.get("contractor")
    by_project: dict[str, list[Asset]] = {}
    for a in assets:
        by_project.setdefault(a.project_id, []).append(a)

    specs = [
        ("Oplevering klein onderhoud wegen Q2", "Wegen & Verharding Westland", "opgeleverd"),
        ("Oplevering kunstwerken-onderhoud voorjaar", "Kunstwerken Westland", "aanvaard"),
        ("Oplevering riolering Naaldwijk-Oost", "Riolering & Water Westland", "concept"),
        ("Oplevering vervanging lichtmasten", "Wegen & Verharding Westland", "opgeleverd"),
    ]
    made = 0
    for title, project_name, status in specs:
        project = projects[project_name]
        exists = (
            db.query(Oplevering)
            .filter(Oplevering.organization_id == org.id, Oplevering.title == title)
            .first()
        )
        if exists:
            continue
        r = _rng("op", title)
        op = Oplevering(
            organization_id=org.id,
            project_id=project.id,
            title=title,
            description=f"Proces-verbaal van oplevering - {project.name}.",
            locatie="Gemeente Westland",
            datum_oplevering=_now() - timedelta(days=r.randint(3, 60)),
            opdrachtgever_naam="Gemeente Westland",
            aannemer_naam=(f"{aannemer.first_name} {aannemer.last_name}" if aannemer else "Aannemer Demo BV"),
            status=status,
            created_by=owner.id,
        )
        if status == "aanvaard":
            op.signed_off_at = _now() - timedelta(days=r.randint(1, 3))
        db.add(op)
        db.flush()
        punten_assets = (by_project.get(project.id) or [])[:3]
        for idx, a in enumerate(punten_assets):
            db.add(OpleveringPunt(
                oplevering_id=op.id,
                organization_id=org.id,
                code=a.code,
                omschrijving=f"Uitgevoerd onderhoud aan {a.name}.",
                uitvoeringsmethode="Conform bestek en CROW-richtlijn uitgevoerd.",
                asset_id=a.id,
                order_index=idx,
                status="gereed",
            ))
        made += 1
    db.commit()
    print(f"  + {made} opleveringen (met opleverpunten)")
    return made


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed een geisoleerde 'Demo Gemeente'-org.")
    parser.add_argument("--reset", action="store_true",
                        help="Verwijder eerst ALLE bestaande demo-org-data en herbouw.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        print(f"== Demo-seed '{ORG_NAME}' ==")
        org = get_or_create_org(db)
        if args.reset:
            reset_demo_org(db, org)
        users = seed_users(db, org)
        owner = users["admin"]
        projects = seed_projects(db, org, owner)
        assets = seed_assets(db, org, owner, projects)
        seed_meldingen(db, org, users, assets)
        seed_inspecties(db, org, users, assets)
        seed_opleveringen(db, org, users, projects, assets)

        print("\n== Stand voor Demo Gemeente ==")
        for label, model in (("users", User), ("projecten", Project), ("assets", Asset),
                             ("meldingen", Melding), ("inspecties", Inspection),
                             ("opleveringen", Oplevering)):
            n = db.query(model).filter(model.organization_id == org.id).count()
            print(f"  {label:13s}: {n}")
        print(f"\n  Inloggen: {DEMO_EMAIL}  (wachtwoord via env DEMO_PASSWORD)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
