"""Workshop-seed: 10 realistische demo-projecten voor een CROW/kunstwerken-inspectiebureau.

Doel: een klant-organisatie ("inspectiebureau") vullen met 10 projecten zoals
een inspectiebureau in het Groene Hart die zou hebben — bruggen/viaducten/
duikers (NEN 2767-2), wegvakken asfalt + elementenverharding (CROW 146a/b),
plus kademuren/beschoeiingen en speeltoestellen (NEN-EN 1176).

Alle meldingen gebruiken UITSLUITEND de CROW-dropdownwaarden die het portaal
verwacht (zie memory fieldops_crow_dropdown_values.md):
  schadegroep : samenhang | textuur | vlakheid | voegen | watergevoeligheid | stenen
  schadebeeld : alleen geldige combinaties per groep (validatie in dit script)
  ernst L/M/E - omvang 1/2/3 - NEN-conditie 1-5 (nooit 6)

MJOP-compatibiliteit (mjop_kosten.py):
  Alle asset_types komen uit de MJOP-kostentabel (brug/viaduct/duiker/gemaal/
  kademuur/speeltoestel/wegvak) — types als 'wegvak_asfalt', 'fietspad' of
  'stuw' vallen geluidloos uit het MJOP en worden hier dus NIET gebruikt.
  Omdat kunstwerken_taxonomy 'wegvak' niet kent, krijgen wegdek-inspecties een
  expliciete kunstwerk_type-override (wegdek_asfalt / wegdek_elementen).
  Het merendeel van de assets krijgt condition_score >= 3, anders blijft het
  MJOP leeg (alleen scores >= 3 zijn 'actionable').

Gebruik:
  python seed_workshop_demo.py                          # verse scratch-db naast dit script
  python seed_workshop_demo.py --db pad/naar/demo.db    # expliciete sqlite-file
  python seed_workshop_demo.py --db postgresql://...    # volledige SQLAlchemy-URL
  python seed_workshop_demo.py --org-name "Bureau X" --admin-email x@y.nl
  python seed_workshop_demo.py --reset                  # eerst org-data opruimen, dan herbouwen

Veiligheid:
  - Default-db is een NIEUWE scratch-file (workshop_demo.db naast dit script);
    het script raakt nooit ongevraagd een bestaande database aan.
  - Een db-file die 'fieldops.db' heet of een niet-sqlite-URL (productie/Render)
    wordt GEWEIGERD tenzij je expliciet --allow-prod-db meegeeft.
  - --reset verwijdert uitsluitend records met organization_id == de workshop-org.
  - Idempotent: een tweede run zonder --reset maakt geen duplicaten aan
    (deterministische sleutels: project-naam, asset-code, melding-/inspectie-titel).

Inloggegevens:
  admin-email  : --admin-email (default workshop-demo@fieldopsapp.nl)
  wachtwoord   : env WORKSHOP_PASSWORD (fallback 'WorkshopDemo2026!' met waarschuwing)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# In de repo-root staat models.py naast dit script; daarbuiten (losse checkout)
# wijst de default naar de zuster-map fieldops-api-main.
DEFAULT_API_DIR = (
    SCRIPT_DIR if (SCRIPT_DIR / "models.py").exists()
    else SCRIPT_DIR.parent / "fieldops-api-main"
)
DEFAULT_DB = SCRIPT_DIR / "workshop_demo.db"

DEFAULT_ORG_NAME = "Inspectiebureau Groene Hart B.V."
DEFAULT_ADMIN_EMAIL = "workshop-demo@fieldopsapp.nl"
_FALLBACK_PASSWORD = "WorkshopDemo2026!"


# ─────────────────────────────────────────────────────────────────────────────
# CROW-portaalwaarden — de ENIGE geldige combinaties (fieldops_crow_dropdown_values)
# ─────────────────────────────────────────────────────────────────────────────
CROW_SCHADEBEELDEN = {
    "samenhang": {"scheurvorming-langs", "scheurvorming-dwars", "scheurvorming-kruis",
                  "scheurvorming-rand", "scheurvorming-groep"},
    "textuur": {"rafeling", "spaltverlies"},
    "vlakheid": {"spoorvorming", "oneffenheden", "kuilen", "deformatie", "verzakking"},
    "voegen": {"voegwijdte", "voegvulling-gebrek"},
    "watergevoeligheid": {"bitumen-uittreden"},
    "stenen": {"gebroken-stenen", "ontbrekende-stenen", "los-liggend"},
}
CROW_ERNST = {"L", "M", "E"}
CROW_OMVANG = {"1", "2", "3"}
ONDERHOUD_CATEGORIEEN = {"observatie", "KO", "GO", "acuut"}


def _check_crow(groep: str, beeld: str, ernst: str, omvang: str, nen: int) -> None:
    """Hard-guard: alleen exacte portaalwaarden mogen de database in."""
    assert groep in CROW_SCHADEBEELDEN, f"ongeldige schadegroep: {groep}"
    assert beeld in CROW_SCHADEBEELDEN[groep], f"ongeldig schadebeeld {beeld} bij groep {groep}"
    assert ernst in CROW_ERNST, f"ongeldige ernst: {ernst}"
    assert omvang in CROW_OMVANG, f"ongeldige omvang: {omvang}"
    assert 1 <= nen <= 5, f"NEN-conditie moet 1-5 zijn (niet 6): {nen}"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (zelfde patroon als seed_demo_org.py)
# ─────────────────────────────────────────────────────────────────────────────
def _rng(*parts) -> random.Random:
    """Deterministische RNG per record-sleutel -> volledig idempotent."""
    return random.Random("|".join(str(p) for p in parts))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def workshop_password() -> str:
    pw = os.environ.get("WORKSHOP_PASSWORD")
    if pw:
        return pw
    print(f"  ! WORKSHOP_PASSWORD niet gezet - fallback '{_FALLBACK_PASSWORD}' gebruikt.")
    return _FALLBACK_PASSWORD


# Echte NL-plaatsen (WGS84 centrum-coordinaten, Groene Hart werkgebied).
PLAATSEN = {
    "Woerden": (52.0855, 4.8830),
    "Harmelen": (52.0900, 4.9640),
    "Gouda": (52.0115, 4.7105),
    "Alphen aan den Rijn": (52.1263, 4.6590),
    "Nieuwkoop": (52.1517, 4.7773),
    "Bodegraven": (52.0818, 4.7480),
    "Reeuwijk": (52.0483, 4.7247),
    "Leiden": (52.1601, 4.4970),
    "Waddinxveen": (52.0454, 4.6522),
    "Boskoop": (52.0728, 4.6549),
    "Zwammerdam": (52.1069, 4.7229),
    "Leimuiden": (52.2280, 4.6740),
}


def _coord(code: str, plaats: str) -> tuple[float, float]:
    """Plausibele, deterministische coordinaat binnen ~700 m van het centrum."""
    base_lat, base_lng = PLAATSEN[plaats]
    r = _rng("coord", code)
    return (
        round(base_lat + r.uniform(-0.006, 0.006), 6),
        round(base_lng + r.uniform(-0.009, 0.009), 6),
    )


def _lerp_coord(code: str, a: str, b: str, t: float) -> tuple[float, float]:
    """Punt op t (0..1) tussen twee plaatsen — voor traject-wegvakken (N-wegen)."""
    (lat1, lng1), (lat2, lng2) = PLAATSEN[a], PLAATSEN[b]
    r = _rng("lerp", code)
    return (
        round(lat1 + (lat2 - lat1) * t + r.uniform(-0.0008, 0.0008), 6),
        round(lng1 + (lng2 - lng1) * t + r.uniform(-0.0008, 0.0008), 6),
    )


def _wegvak_geometry(code: str, lat: float, lng: float) -> tuple[str, float]:
    """Kleine LineString (~350-700 m) vanaf (lat,lng) + berekende lengte in m."""
    r = _rng("geom", code)
    bearing = r.uniform(0.0, 360.0)
    pts = [(round(lng, 6), round(lat, 6))]
    total = 0.0
    cur_lat, cur_lng = lat, lng
    for _ in range(3):
        d = r.uniform(120.0, 230.0)  # meter per segment
        b = math.radians(bearing + r.uniform(-12.0, 12.0))
        cur_lat += (d * math.cos(b)) / 111_320.0
        cur_lng += (d * math.sin(b)) / (111_320.0 * math.cos(math.radians(cur_lat)))
        pts.append((round(cur_lng, 6), round(cur_lat, 6)))
        total += d
    geojson = json.dumps({"type": "LineString", "coordinates": [[x, y] for x, y in pts]})
    return geojson, round(total, 1)


_LIFESPAN = {
    "brug": 80, "viaduct": 80, "duiker": 60, "gemaal": 30,
    "kademuur": 60, "speeltoestel": 15, "wegvak": 25,
}
_WEGVAK_TYPES = {"wegvak"}


# ─────────────────────────────────────────────────────────────────────────────
# Projectdefinities — 10 projecten voor een inspectiebureau
#
#  assets   : (code, naam, asset_type, plaats-of-(a,b,t))
#              asset_type MOET in mjop_kosten.KOSTEN staan (anders geen MJOP)
#  meldingen: (asset_idx, titel-kern, categorie, prioriteit, status,
#              crow=(groep, beeld, ernst, omvang) of None, nen 1-5, onderhoud)
#  inspecties: (asset_idx, status, norm_label, taxonomy_type_override-of-None)
#              override nodig voor 'wegvak' (kent kunstwerken_taxonomy niet)
# ─────────────────────────────────────────────────────────────────────────────
def _projects() -> list[dict]:
    return [
        # ── 1. Bruggen Woerden (NEN 2767-2) ─────────────────────────────────
        {
            "name": "Bruggeninspectie NEN 2767 — Gemeente Woerden 2026",
            "description": "Eerste-graads inspectie van 8 beweegbare en vaste bruggen "
                           "in de kernen Woerden en Harmelen, conform NEN 2767-2 en CROW 134.",
            "gemeente": "Woerden", "color": "#7c3aed",
            "categories": ["brug", "kunstwerk", "nen2767"],
            "assets": [
                ("WRD-BR-001", "Kwakelbrug", "brug", "Woerden"),
                ("WRD-BR-002", "Rozenbrug", "brug", "Woerden"),
                ("WRD-BR-003", "Emmabrug Singel", "brug", "Woerden"),
                ("WRD-BR-004", "Brug Oostdam", "brug", "Woerden"),
                ("WRD-BR-005", "Brug Rietveld 12", "brug", "Woerden"),
                ("WRD-BR-006", "Harmelerbrug", "brug", "Harmelen"),
                ("WRD-BR-007", "Brug Geestdorp", "brug", "Woerden"),
                ("WRD-BR-008", "Voetbrug Brediuspark", "brug", "Woerden"),
            ],
            "meldingen": [
                (0, "Betonschade landhoofd noordzijde", "schade", "hoog", "open", None, 4, "GO"),
                (2, "Corrosie leuningwerk", "schade", "normaal", "in_behandeling", None, 3, "KO"),
                (5, "Lekkage voegovergang aanbrug", "schade", "normaal", "open", None, 3, "KO"),
                (7, "Houtrot in brugdek voetbrug", "schade", "hoog", "afgerond", None, 4, "GO"),
            ],
            "inspecties": [(0, "completed", "NEN 2767-2", None), (2, "signed", "NEN 2767-2", None),
                           (5, "in_progress", "NEN 2767-2", None)],
        },
        # ── 2. Viaducten N207 (provincie) ────────────────────────────────────
        {
            "name": "Viaducten & onderdoorgangen N207 — Provincie Zuid-Holland",
            "description": "Instandhoudingsinspectie van 5 viaducten en 2 onderliggende "
                           "duikers in het traject Alphen aan den Rijn — Leimuiden (N207).",
            "gemeente": "Alphen aan den Rijn", "color": "#0284c7",
            "categories": ["viaduct", "duiker", "kunstwerk"],
            "assets": [
                ("N207-VI-001", "Viaduct N207 Gnephoek", "viaduct", "Alphen aan den Rijn"),
                ("N207-VI-002", "Viaduct N207 Heimanswetering", "viaduct", "Alphen aan den Rijn"),
                ("N207-VI-003", "Viaduct N207 Burg. Bruinsslotsingel", "viaduct", "Alphen aan den Rijn"),
                ("N207-VI-004", "Viaduct N207 Leimuiderbrug-Zuid", "viaduct", "Leimuiden"),
                ("N207-VI-005", "Viaduct N207 Boskoop-Noord", "viaduct", "Boskoop"),
                ("N207-DU-001", "Duiker N207 hm 12.4", "duiker", "Alphen aan den Rijn"),
                ("N207-DU-002", "Duiker N207 hm 15.1", "duiker", "Leimuiden"),
            ],
            "meldingen": [
                (0, "Scheurvorming landhoofd oplegging", "inspectie", "hoog", "open", None, 4, "GO"),
                (3, "Graffiti en betonrot pijler 2", "schade", "normaal", "in_behandeling", None, 3, "KO"),
                (6, "Sedimentafzetting duiker, doorstroming beperkt", "schade", "normaal", "open", None, 3, "KO"),
            ],
            "inspecties": [(0, "completed", "NEN 2767-2", None), (4, "draft", "NEN 2767-2", None)],
        },
        # ── 3. Duikers Rijnland (waterschap) ─────────────────────────────────
        {
            "name": "Duikerinspectie poldergebied — Hoogheemraadschap van Rijnland",
            "description": "Conditiemeting van 11 polderduikers en 1 opvoergemaal in de "
                           "polders rond Nieuwkoop, Zwammerdam en Bodegraven (NEN 2767-2).",
            "gemeente": "Nieuwkoop", "color": "#0891b2",
            "categories": ["duiker", "gemaal", "kunstwerk", "water"],
            "assets": [
                ("HHR-DU-001", "Duiker Meijepolder Oost", "duiker", "Nieuwkoop"),
                ("HHR-DU-002", "Duiker Meijepolder West", "duiker", "Nieuwkoop"),
                ("HHR-DU-003", "Duiker Noordse Buurt 4", "duiker", "Nieuwkoop"),
                ("HHR-DU-004", "Duiker Ziendeweg", "duiker", "Nieuwkoop"),
                ("HHR-DU-005", "Duiker Zwammerdam-Zuid", "duiker", "Zwammerdam"),
                ("HHR-DU-006", "Duiker Spoekpolder", "duiker", "Zwammerdam"),
                ("HHR-DU-007", "Duiker Weijpoort 8", "duiker", "Bodegraven"),
                ("HHR-DU-008", "Duiker Meijekade 21", "duiker", "Bodegraven"),
                ("HHR-DU-009", "Duiker Oud Bodegraafseweg", "duiker", "Bodegraven"),
                ("HHR-DU-010", "Duiker Achttienkavels", "duiker", "Nieuwkoop"),
                ("HHR-DU-011", "Duiker Blokland", "duiker", "Nieuwkoop"),
                ("HHR-GM-001", "Opvoergemaal Meijepolder", "gemaal", "Nieuwkoop"),
            ],
            "meldingen": [
                (2, "Wortelingroei in duikerbuis", "schade", "normaal", "open", None, 3, "KO"),
                (5, "Verzakte uitstroombak, erosie talud", "schade", "hoog", "in_behandeling", None, 4, "GO"),
                (8, "Duiker deels dichtgeslibd", "schade", "normaal", "afgerond", None, 3, "KO"),
                (11, "Krooshek verstopt, opvoerhoogte verhoogd", "schade", "kritiek", "open", None, 4, "acuut"),
            ],
            "inspecties": [(2, "completed", "NEN 2767-2", None), (11, "signed", "NEN 2767-2", None)],
        },
        # ── 4. CROW 146a asfalt Gouda ────────────────────────────────────────
        {
            "name": "CROW 146a Asfaltinspectie kernwegennet — Gemeente Gouda",
            "description": "Globale visuele inspectie (CROW 146a) van 10 asfaltwegvakken "
                           "in het kernwegennet van Gouda; input voor het onderhoudsprogramma 2027.",
            "gemeente": "Gouda", "color": "#ea580c",
            "categories": ["wegdek", "verharding", "crow146"],
            "asset_props": {"verhardingstype": "asfalt"},
            "assets": [
                ("GD-WV-001", "Bloemendaalseweg wegvak 1", "wegvak", "Gouda"),
                ("GD-WV-002", "Goudkade wegvak 2", "wegvak", "Gouda"),
                ("GD-WV-003", "Burgemeester Jamessingel wegvak 1", "wegvak", "Gouda"),
                ("GD-WV-004", "Nieuwe Gouwe O.Z. wegvak 3", "wegvak", "Gouda"),
                ("GD-WV-005", "Ridder van Catsweg wegvak 2", "wegvak", "Gouda"),
                ("GD-WV-006", "Bodegraafsestraatweg wegvak 1", "wegvak", "Gouda"),
                ("GD-WV-007", "Graaf Florisweg wegvak 2", "wegvak", "Gouda"),
                ("GD-WV-008", "Sportlaan wegvak 1", "wegvak", "Gouda"),
                ("GD-WV-009", "Achterwillenseweg wegvak 4", "wegvak", "Gouda"),
                ("GD-WV-010", "Joubertstraat wegvak 1", "wegvak", "Gouda"),
            ],
            "meldingen": [
                (0, "Scheurvorming in langsrichting rijspoor", "schade", "hoog", "open",
                 ("samenhang", "scheurvorming-langs", "M", "2"), 3, "GO"),
                (2, "Dwarsscheuren t.h.v. kruisingsvlak", "schade", "normaal", "in_behandeling",
                 ("samenhang", "scheurvorming-dwars", "M", "1"), 3, "KO"),
                (4, "Rafeling deklaag busbaan", "schade", "normaal", "open",
                 ("textuur", "rafeling", "L", "2"), 2, "KO"),
                (6, "Spoorvorming rechter rijstrook", "schade", "hoog", "open",
                 ("vlakheid", "spoorvorming", "E", "2"), 4, "GO"),
                (8, "Bitumen-uittreden na warmteperiode", "schade", "laag", "afgerond",
                 ("watergevoeligheid", "bitumen-uittreden", "L", "1"), 2, "observatie"),
            ],
            "inspecties": [(0, "completed", "CROW 146a", "wegdek_asfalt"),
                           (6, "in_progress", "CROW 146a", "wegdek_asfalt")],
        },
        # ── 5. CROW 146b elementen Alphen ────────────────────────────────────
        {
            "name": "CROW 146b Elementenverharding binnenstad — Alphen aan den Rijn",
            "description": "Inspectie elementenverharding (klinkers/tegels) in het "
                           "centrumgebied van Alphen aan den Rijn volgens CROW 146b.",
            "gemeente": "Alphen aan den Rijn", "color": "#ca8a04",
            "categories": ["verharding", "elementen", "crow146"],
            "asset_props": {"verhardingstype": "elementenverharding"},
            "assets": [
                ("APN-EV-001", "Julianastraat (klinkers)", "wegvak", "Alphen aan den Rijn"),
                ("APN-EV-002", "Van Mandersloostraat (klinkers)", "wegvak", "Alphen aan den Rijn"),
                ("APN-EV-003", "Hooftstraat (gebakken klinkers)", "wegvak", "Alphen aan den Rijn"),
                ("APN-EV-004", "Raadhuisstraat (tegels)", "wegvak", "Alphen aan den Rijn"),
                ("APN-EV-005", "Aarkade (klinkers)", "wegvak", "Alphen aan den Rijn"),
                ("APN-EV-006", "Thorbeckeplein (natuursteen)", "wegvak", "Alphen aan den Rijn"),
                ("APN-EV-007", "Stationsplein (betonstraatsteen)", "wegvak", "Alphen aan den Rijn"),
                ("APN-EV-008", "Paradijslaan (klinkers)", "wegvak", "Alphen aan den Rijn"),
            ],
            "meldingen": [
                (0, "Gebroken klinkers laad- en loszone", "schade", "normaal", "open",
                 ("stenen", "gebroken-stenen", "M", "2"), 3, "KO"),
                (2, "Losliggende gebakken klinkers", "schade", "hoog", "in_behandeling",
                 ("stenen", "los-liggend", "M", "2"), 3, "KO"),
                (3, "Ontbrekende tegels na kabelwerk", "schade", "hoog", "open",
                 ("stenen", "ontbrekende-stenen", "E", "1"), 4, "GO"),
                (5, "Open voegen natuursteenverharding", "schade", "laag", "open",
                 ("voegen", "voegvulling-gebrek", "L", "2"), 2, "observatie"),
                (6, "Verzakking rond kolk stationsplein", "schade", "normaal", "afgerond",
                 ("vlakheid", "verzakking", "M", "1"), 3, "KO"),
            ],
            "inspecties": [(0, "completed", "CROW 146b", "wegdek_elementen")],
        },
        # ── 6. Fietspaden Nieuwkoop ──────────────────────────────────────────
        {
            "name": "Fietspadeninspectie buitengebied — Gemeente Nieuwkoop",
            "description": "Veiligheids- en conditie-inspectie van 7 vrijliggende "
                           "fietspaden in het buitengebied (CROW 146a).",
            "gemeente": "Nieuwkoop", "color": "#16a34a",
            "categories": ["fietspad", "verharding", "crow146"],
            "asset_props": {"verhardingstype": "asfalt", "functie": "fietspad"},
            "assets": [
                ("NKP-FP-001", "Fietspad Meijepad", "wegvak", "Nieuwkoop"),
                ("NKP-FP-002", "Fietspad Ziendeweg", "wegvak", "Nieuwkoop"),
                ("NKP-FP-003", "Fietspad Noordenseweg", "wegvak", "Nieuwkoop"),
                ("NKP-FP-004", "Fietspad Uiterbuurtweg", "wegvak", "Nieuwkoop"),
                ("NKP-FP-005", "Fietspad Vosholpad", "wegvak", "Nieuwkoop"),
                ("NKP-FP-006", "Fietspad Sluispad", "wegvak", "Nieuwkoop"),
                ("NKP-FP-007", "Fietspad Padbuurt", "wegvak", "Nieuwkoop"),
            ],
            "meldingen": [
                (0, "Wortelopdruk, oneffen ligging fietspad", "schade", "hoog", "open",
                 ("vlakheid", "oneffenheden", "M", "2"), 3, "GO"),
                (2, "Randschade door bermafkalving", "schade", "normaal", "in_behandeling",
                 ("samenhang", "scheurvorming-rand", "M", "2"), 3, "KO"),
                (4, "Kuilvorming na vorstperiode", "schade", "hoog", "open",
                 ("vlakheid", "kuilen", "E", "1"), 4, "GO"),
                (5, "Craquele scheurvorming toplaag", "schade", "laag", "afgerond",
                 ("samenhang", "scheurvorming-kruis", "L", "1"), 2, "observatie"),
            ],
            "inspecties": [(0, "signed", "CROW 146a", "wegdek_asfalt")],
        },
        # ── 7. Kunstwerken Bodegraven-Reeuwijk ───────────────────────────────
        {
            "name": "Kunstwerken buitengebied — Gemeente Bodegraven-Reeuwijk",
            "description": "Meerjarige instandhoudingsinspectie van bruggen, duikers en "
                           "een poldergemaal in het buitengebied (NEN 2767-2).",
            "gemeente": "Bodegraven-Reeuwijk", "color": "#9333ea",
            "categories": ["brug", "duiker", "gemaal", "kunstwerk"],
            "assets": [
                ("BR-BR-001", "Brug Oud Reeuwijkseweg", "brug", "Reeuwijk"),
                ("BR-BR-002", "Brug Nieuwdorperweg", "brug", "Reeuwijk"),
                ("BR-BR-003", "Plateelbrug Bodegraven", "brug", "Bodegraven"),
                ("BR-BR-004", "Brug Tempeldijk", "brug", "Reeuwijk"),
                ("BR-DU-001", "Duiker Reeuwijkse Hout", "duiker", "Reeuwijk"),
                ("BR-DU-002", "Duiker Twaalfmorgen", "duiker", "Reeuwijk"),
                ("BR-DU-003", "Duiker Parallelweg-Zuid", "duiker", "Bodegraven"),
                ("BR-DU-004", "Duiker Zuidzijde 102", "duiker", "Bodegraven"),
                ("BR-GM-001", "Poldergemaal Sluipwijk", "gemaal", "Reeuwijk"),
            ],
            "meldingen": [
                (0, "Aangereden leuning, scheefstand", "schade", "hoog", "open", None, 4, "GO"),
                (3, "Scheur in betonnen dekplaat", "inspectie", "normaal", "in_behandeling", None, 3, "KO"),
                (6, "Ingroei riet voor instroomopening", "schade", "laag", "afgerond", None, 2, "observatie"),
                (8, "Storing pomp 2, verhoogd geluidsniveau", "schade", "kritiek", "in_behandeling", None, 4, "acuut"),
            ],
            "inspecties": [(0, "completed", "NEN 2767-2", None), (8, "in_progress", "NEN 2767-2", None)],
        },
        # ── 8. Kademuren & beschoeiingen Leiden ──────────────────────────────
        {
            "name": "Kademuren & beschoeiingen Oude Rijn — Gemeente Leiden",
            "description": "Inspectie van historische kademuren en houten beschoeiingen "
                           "langs de singels en de Oude Rijn (NEN 2767-2, deels onder waterlijn).",
            "gemeente": "Leiden", "color": "#475569",
            "categories": ["kademuur", "beschoeiing", "kunstwerk"],
            "assets": [
                ("LDN-KM-001", "Kademuur Aalmarkt", "kademuur", "Leiden"),
                ("LDN-KM-002", "Kademuur Nieuwe Rijn zuidzijde", "kademuur", "Leiden"),
                ("LDN-KM-003", "Kademuur Oude Singel 40-88", "kademuur", "Leiden"),
                ("LDN-BS-001", "Beschoeiing Zoeterwoudsesingel", "kademuur", "Leiden"),
                ("LDN-BS-002", "Beschoeiing Witte Singel", "kademuur", "Leiden"),
                ("LDN-KM-004", "Kademuur Haven noordzijde", "kademuur", "Leiden"),
            ],
            "meldingen": [
                (0, "Uitspoeling achter kademuur, holle ruimte", "inspectie", "hoog", "open", None, 4, "GO"),
                (3, "Rottende gordingen beschoeiing", "schade", "normaal", "in_behandeling", None, 4, "GO"),
                (5, "Scheefstand kademuur t.h.v. trap", "inspectie", "kritiek", "open", None, 5, "acuut"),
            ],
            "inspecties": [(0, "completed", "NEN 2767-2", None), (3, "draft", "NEN 2767-2", None)],
        },
        # ── 9. Speeltoestellen Waddinxveen (NEN-EN 1176) ─────────────────────
        {
            "name": "Speeltoestellen NEN-EN 1176 — Gemeente Waddinxveen",
            "description": "Jaarlijkse hoofdinspectie van speeltoestellen in de openbare "
                           "ruimte conform NEN-EN 1176 (WAS-registratie).",
            "gemeente": "Waddinxveen", "color": "#db2777",
            "categories": ["speeltoestel", "nen-en-1176"],
            "assets": [
                ("WDV-SP-001", "Speeltoren Warnaarplantsoen", "speeltoestel", "Waddinxveen"),
                ("WDV-SP-002", "Schommelset Vondelwijk", "speeltoestel", "Waddinxveen"),
                ("WDV-SP-003", "Klimrek Oranjewijk", "speeltoestel", "Waddinxveen"),
                ("WDV-SP-004", "Veerelement Souburghlaan", "speeltoestel", "Waddinxveen"),
                ("WDV-SP-005", "Kabelbaan Bomenwijk", "speeltoestel", "Waddinxveen"),
                ("WDV-SP-006", "Duikelrek Zonnig Zuid", "speeltoestel", "Waddinxveen"),
            ],
            "meldingen": [
                (0, "Losse bout valbeveiliging platform", "schade", "kritiek", "in_behandeling", None, 4, "acuut"),
                (2, "Slijtage kunststof coating klimrek", "schade", "laag", "open", None, 2, "observatie"),
                (4, "Versleten kabelbaan-trolley", "schade", "hoog", "afgerond", None, 3, "KO"),
            ],
            "inspecties": [(0, "signed", "NEN-EN 1176", "speeltoestel"), (4, "completed", "NEN-EN 1176", "speeltoestel")],
        },
        # ── 10. Traject N458 wegvakken ───────────────────────────────────────
        {
            "name": "Trajectinspectie N458 Bodegraven–Woerden — wegvakken",
            "description": "Trajectmatige CROW 146a-inspectie van 9 aaneengesloten "
                           "asfaltwegvakken op de N458 tussen Bodegraven en Woerden.",
            "gemeente": "Bodegraven-Reeuwijk / Woerden", "color": "#dc2626",
            "categories": ["wegdek", "traject", "crow146"],
            "asset_props": {"verhardingstype": "asfalt", "wegnummer": "N458"},
            "assets": [
                ("N458-WV-%03d" % (i + 1), "N458 wegvak hm %.1f" % (1.0 + i * 1.4),
                 "wegvak", ("Bodegraven", "Woerden", (i + 0.5) / 9.0))
                for i in range(9)
            ],
            "meldingen": [
                (1, "Spoorvorming zwaar-verkeerstrook", "schade", "hoog", "open",
                 ("vlakheid", "spoorvorming", "M", "3"), 3, "GO"),
                (3, "Scheurvorming wegvakrand berm", "schade", "normaal", "open",
                 ("samenhang", "scheurvorming-rand", "M", "2"), 3, "KO"),
                (5, "Groepsgewijze scheurvorming opstelvak", "schade", "hoog", "in_behandeling",
                 ("samenhang", "scheurvorming-groep", "E", "2"), 4, "GO"),
                (7, "Deformatie t.h.v. duikerkruising", "schade", "normaal", "afgerond",
                 ("vlakheid", "deformatie", "M", "1"), 3, "KO"),
            ],
            "inspecties": [(5, "completed", "CROW 146a", "wegdek_asfalt")],
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Argumenten + veiligheid
# ─────────────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed 10 workshop-demo-projecten voor een inspectiebureau.")
    p.add_argument("--db", default=str(DEFAULT_DB),
                   help="Pad naar sqlite-file OF volledige SQLAlchemy-URL "
                        f"(default: nieuwe scratch-db {DEFAULT_DB})")
    p.add_argument("--org-name", default=DEFAULT_ORG_NAME,
                   help=f"Naam van de klant-organisatie (default: '{DEFAULT_ORG_NAME}')")
    p.add_argument("--admin-email", default=DEFAULT_ADMIN_EMAIL,
                   help=f"E-mail van de org-admin (default: {DEFAULT_ADMIN_EMAIL})")
    p.add_argument("--api-dir", default=str(DEFAULT_API_DIR),
                   help="Pad naar de fieldops-api-repo (voor models/database-import)")
    p.add_argument("--reset", action="store_true",
                   help="Verwijder eerst ALLE data van deze org en herbouw.")
    p.add_argument("--allow-prod-db", action="store_true",
                   help="Vereist om tegen een 'fieldops.db' of een niet-sqlite-URL "
                        "(bv. Render Postgres) te draaien. Zonder deze vlag weigert het script.")
    return p.parse_args()


def resolve_db_url(raw: str, allow_prod: bool) -> str:
    if "://" in raw:
        url = raw
        if not url.startswith("sqlite") and not allow_prod:
            sys.exit("GEWEIGERD: niet-sqlite-URL (productie?) zonder --allow-prod-db.")
        if url.startswith("sqlite") and "fieldops.db" in url.lower() and not allow_prod:
            sys.exit("GEWEIGERD: URL wijst naar een 'fieldops.db' zonder --allow-prod-db.")
        return url
    path = Path(raw).expanduser().resolve()
    if path.name.lower() == "fieldops.db" and not allow_prod:
        sys.exit(f"GEWEIGERD: {path} heet 'fieldops.db' (productie-naam). "
                 "Gebruik --allow-prod-db als je dit ECHT wilt.")
    path.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + path.as_posix()


# ─────────────────────────────────────────────────────────────────────────────
# Seed-stappen (imports gebeuren in main() NA het zetten van DATABASE_URL)
# ─────────────────────────────────────────────────────────────────────────────
def get_or_create_org(db, m, org_name: str, admin_email: str):
    org = db.query(m.Organization).filter(m.Organization.name == org_name).first()
    if not org:
        org = m.Organization(
            name=org_name,
            plan=m.SubscriptionPlan.PROFESSIONAL,
            status=m.AccountStatus.ACTIVE,
            max_users=25,
            contact_email=admin_email,
            brand_color="#0f766e",
        )
        db.add(org)
        db.commit()
        db.refresh(org)
        print(f"  + org '{org_name}'")
    else:
        print(f"  = org '{org_name}' bestaat al")
    return org


def _org_slug(org_name: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in org_name.lower())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:40] or "workshop"


def seed_users(db, m, hash_password, org, admin_email: str) -> dict:
    pw = workshop_password()
    slug = _org_slug(org.name)
    specs = [
        (admin_email, "Sanne", "van Dijk", m.UserRole.ADMIN, True),
        (f"inspecteur.{slug}@demo.fieldopsapp.nl", "Mark", "Verhoeven", m.UserRole.INSPECTOR, False),
        (f"planner.{slug}@demo.fieldopsapp.nl", "Ilse", "Jansen", m.UserRole.MANAGER, False),
    ]
    users: dict = {}
    for email, fn, ln, role, is_admin in specs:
        u = db.query(m.User).filter(m.User.email == email).first()
        if u and u.organization_id != org.id:
            sys.exit(f"FOUT: user {email} bestaat al in een ANDERE organisatie. "
                     "Kies een ander --admin-email of andere --org-name.")
        if not u:
            u = m.User(
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


def seed_projects(db, m, org, owner) -> dict:
    projects: dict = {}
    for spec in _projects():
        p = (db.query(m.Project)
             .filter(m.Project.organization_id == org.id, m.Project.name == spec["name"])
             .first())
        if not p:
            p = m.Project(
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


def _asset_coord(code: str, plaats) -> tuple[float, float]:
    if isinstance(plaats, tuple):           # traject: (plaats_a, plaats_b, t)
        return _lerp_coord(code, plaats[0], plaats[1], plaats[2])
    return _coord(code, plaats)


def seed_assets(db, m, org, owner, projects) -> dict:
    """Return {project_name: [Asset, ...]} in blueprint-volgorde."""
    per_project: dict = {}
    total = 0
    for spec in _projects():
        project = projects[spec["name"]]
        rows = []
        for code, naam, atype, plaats in spec["assets"]:
            a = (db.query(m.Asset)
                 .filter(m.Asset.organization_id == org.id, m.Asset.code == code)
                 .first())
            if not a:
                lat, lng = _asset_coord(code, plaats)
                lifespan = _LIFESPAN.get(atype, 40)
                r = _rng("asset", code)
                plaats_label = plaats if isinstance(plaats, str) else "N458"
                a = m.Asset(
                    code=code,
                    name=naam,
                    asset_type=atype,
                    lat=lat,
                    lng=lng,
                    location_description=f"{plaats_label} - {naam}",
                    project_id=project.id,
                    organization_id=org.id,
                    installed_at=_now() - timedelta(days=r.randint(4, max(6, lifespan - 6)) * 365),
                    expected_lifespan_years=lifespan,
                    # Overwegend >= 3: alleen scores >= 3 zijn 'actionable' in
                    # het MJOP (mjop_kosten.is_actionable) — anders blijft dat leeg.
                    condition_score=r.choice([2, 3, 3, 3, 4, 4, 5]),
                    created_by=owner.id,
                )
                if spec.get("asset_props"):
                    a.properties_json = json.dumps(spec["asset_props"])
                if atype in _WEGVAK_TYPES:
                    geo, length = _wegvak_geometry(code, lat, lng)
                    a.geometry_geojson = geo
                    a.length_m = length
                    a.is_segment = True
                db.add(a)
                db.flush()
                total += 1
            rows.append(a)
        per_project[spec["name"]] = rows
    db.commit()
    print(f"  + assets aangemaakt/gecontroleerd (nieuw: {total})")
    return per_project


def seed_meldingen(db, m, org, users, assets_per_project) -> int:
    creators = list(users.values())
    made = 0
    for spec in _projects():
        rows = assets_per_project[spec["name"]]
        for (idx, kern, cat, prio, status, crow, nen, onderhoud) in spec["meldingen"]:
            asset = rows[idx]
            title = f"{kern} — {asset.code} {asset.name}"
            exists = (db.query(m.Melding)
                      .filter(m.Melding.organization_id == org.id, m.Melding.title == title)
                      .first())
            if exists:
                continue
            assert onderhoud in ONDERHOUD_CATEGORIEEN, f"ongeldige onderhoud_categorie: {onderhoud}"
            assert 1 <= nen <= 5, f"NEN-conditie moet 1-5 zijn: {nen}"
            r = _rng("meld", asset.code, kern)
            created = _now() - timedelta(days=r.randint(2, 120), hours=r.randint(0, 23))
            updated = created
            if status == "afgerond":
                updated = min(created + timedelta(days=r.randint(2, 28)), _now())
            melding = m.Melding(
                title=title,
                description=f"Geconstateerd tijdens inspectieronde: {kern.lower()} "
                            f"bij {asset.name} ({asset.location_description}).",
                category=cat,
                priority=prio,
                status=status,
                lat=asset.lat,
                lng=asset.lng,
                project_id=asset.project_id,
                asset_id=asset.id,
                organization_id=org.id,
                nen_2767_conditie=nen,
                onderhoud_categorie=onderhoud,
                created_by=r.choice(creators).id,
                created_at=created,
                updated_at=updated,
            )
            if crow:
                groep, beeld, ernst, omvang = crow
                _check_crow(groep, beeld, ernst, omvang, nen)
                melding.crow_schadegroep = groep
                melding.crow_schadebeeld = beeld
                melding.crow_ernst = ernst
                melding.crow_omvang = omvang
                melding.crow_klasse = f"{ernst}{omvang}"
            db.add(melding)
            made += 1
    db.commit()
    print(f"  + {made} meldingen (CROW-waarden gevalideerd tegen portaal-dropdowns)")
    return made


def seed_inspecties(db, m, taxonomy, org, users, assets_per_project) -> int:
    inspecteur = users.get("inspector") or users["admin"]
    made = 0
    for spec in _projects():
        rows = assets_per_project[spec["name"]]
        for (idx, status, norm_label, ktype_override) in spec["inspecties"]:
            asset = rows[idx]
            # 'wegvak' (MJOP-type) bestaat niet in kunstwerken_taxonomy —
            # daarvoor levert de projectspec een expliciete override
            # (wegdek_asfalt / wegdek_elementen).
            ktype = taxonomy.normalize_type(ktype_override or asset.asset_type)
            if not ktype:
                print(f"  ! geen taxonomie voor asset_type {asset.asset_type} — inspectie overgeslagen")
                continue
            title = f"{norm_label} inspectie {asset.code} — {asset.name}"
            exists = (db.query(m.Inspection)
                      .filter(m.Inspection.organization_id == org.id, m.Inspection.title == title)
                      .first())
            if exists:
                continue
            r = _rng("insp", asset.code)
            datum = _now() - timedelta(days=r.randint(7, 150))
            insp = m.Inspection(
                organization_id=org.id,
                asset_id=asset.id,
                kunstwerk_type=ktype,
                project_id=asset.project_id,
                title=title,
                inspectie_type="eerste-graads",
                norm_referenties=norm_label,
                datum_inspectie=datum,
                inspecteur_id=inspecteur.id,
                inspecteur_naam=f"{inspecteur.first_name} {inspecteur.last_name}",
                weersomstandigheden=r.choice(["droog, 9 C", "bewolkt, 13 C", "lichte regen, 11 C"]),
                opdrachtgever_naam=spec["gemeente"] if "Rijnland" not in spec["name"]
                else "Hoogheemraadschap van Rijnland",
                status=status,
                created_by=inspecteur.id,
            )
            if ktype == "speeltoestel":
                insp.nen1176_inspectie_kind = r.choice(["hoofd", "operationeel"])
            db.add(insp)
            db.flush()

            elementen = taxonomy.elementen_voor(ktype)[:6] or [
                {"code": "ALG.OBJECT", "naam": asset.name, "groep": "constructief", "gebreken": []}
            ]
            worst = 1
            for order, el in enumerate(elementen):
                score = _rng("elem", asset.code, el["code"]).choice([1, 2, 2, 3, 3, 3, 4, 5])
                worst = max(worst, score)
                ie = m.InspectionElement(
                    inspection_id=insp.id,
                    organization_id=org.id,
                    element_code=el["code"],
                    element_naam=el["naam"],
                    element_groep=el.get("groep"),
                    beoordeeld=True,
                    conditiescore=score,
                    bevindingen="Visueel beoordeeld; geen bijzonderheden." if score <= 2
                    else "Aandachtspunt geconstateerd; opvolging geadviseerd.",
                    order_index=order,
                )
                db.add(ie)
                db.flush()
                if score >= 4 and el.get("gebreken"):
                    g = _rng("def", asset.code, el["code"]).choice(el["gebreken"])
                    db.add(m.InspectionDefect(
                        element_id=ie.id,
                        organization_id=org.id,
                        gebrek_code=g.get("code"),
                        gebrek_naam=g.get("naam", "Gebrek"),
                        omschrijving="Geconstateerd tijdens workshop-demo-inspectie.",
                        ernst=2,
                        intensiteit=2,
                        omvang_klasse=3,
                        defect_score=score,
                    ))
            insp.conditiescore_overall = worst
            if status in ("completed", "signed"):
                insp.aanbevolen_acties = (
                    "Geen directe maatregelen noodzakelijk." if worst <= 3
                    else "Herinspectie binnen 12 maanden; herstel slechtste element inplannen."
                )
            if status == "signed":
                insp.signed_at = datum + timedelta(days=1)
                insp.signed_by = inspecteur.id
            made += 1
    db.commit()
    print(f"  + {made} inspecties (met bouwdeel-decompositie uit kunstwerken_taxonomy)")
    return made


def reset_org(db, m, org) -> None:
    """Verwijder ALLE data van uitsluitend deze org, FK-veilig (zelfde patroon
    als seed_demo_org.reset_demo_org, incl. InspectionAnswer)."""
    oid = org.id
    insp_ids = [i.id for i in db.query(m.Inspection.id).filter(m.Inspection.organization_id == oid)]
    elem_ids = [e.id for e in db.query(m.InspectionElement.id)
                .filter(m.InspectionElement.organization_id == oid)]
    if elem_ids:
        db.query(m.InspectionDefect).filter(m.InspectionDefect.element_id.in_(elem_ids)).delete(
            synchronize_session=False)
        db.query(m.InspectionAnswer).filter(m.InspectionAnswer.element_id.in_(elem_ids)).delete(
            synchronize_session=False)
    if insp_ids:
        db.query(m.InspectionElement).filter(m.InspectionElement.inspection_id.in_(insp_ids)).delete(
            synchronize_session=False)
    db.query(m.Inspection).filter(m.Inspection.organization_id == oid).delete(synchronize_session=False)
    db.query(m.Melding).filter(m.Melding.organization_id == oid).delete(synchronize_session=False)
    db.query(m.Asset).filter(m.Asset.organization_id == oid).delete(synchronize_session=False)
    db.query(m.Project).filter(m.Project.organization_id == oid).delete(synchronize_session=False)
    db.query(m.User).filter(m.User.organization_id == oid).delete(synchronize_session=False)
    db.commit()
    print(f"  [reset] alle data van org {oid} verwijderd")


def print_summary(db, m, org) -> None:
    print(f"\n== Stand voor '{org.name}' ==")
    for label, model in (("users", m.User), ("projecten", m.Project), ("assets", m.Asset),
                         ("meldingen", m.Melding), ("inspecties", m.Inspection)):
        n = db.query(model).filter(model.organization_id == org.id).count()
        print(f"  {label:11s}: {n}")
    print("\n  Per project:")
    for p in (db.query(m.Project).filter(m.Project.organization_id == org.id)
              .order_by(m.Project.name)):
        na = db.query(m.Asset).filter(m.Asset.project_id == p.id).count()
        nm = db.query(m.Melding).filter(m.Melding.project_id == p.id).count()
        ni = db.query(m.Inspection).filter(m.Inspection.project_id == p.id).count()
        print(f"    {p.name[:58]:58s} assets={na:3d} meldingen={nm:2d} inspecties={ni:2d}")


def main() -> None:
    args = parse_args()

    db_url = resolve_db_url(args.db, args.allow_prod_db)
    # BELANGRIJK: env zetten VOOR de import van database.py — die leest
    # DATABASE_URL bij import (load_dotenv overschrijft bestaande env niet).
    os.environ["DATABASE_URL"] = db_url

    api_dir = Path(args.api_dir).expanduser().resolve()
    if not (api_dir / "models.py").exists():
        sys.exit(f"FOUT: {api_dir} bevat geen models.py — geef --api-dir op.")
    sys.path.insert(0, str(api_dir))

    from database import SessionLocal, engine, Base  # noqa: E402
    import models as m                                # noqa: E402
    import kunstwerken_taxonomy as taxonomy           # noqa: E402
    from auth import hash_password                    # noqa: E402

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        print(f"== Workshop-seed '{args.org_name}' ==")
        print(f"   db: {db_url}")
        org = get_or_create_org(db, m, args.org_name, args.admin_email)
        if args.reset:
            reset_org(db, m, org)
        users = seed_users(db, m, hash_password, org, args.admin_email)
        owner = users["admin"]
        projects = seed_projects(db, m, org, owner)
        assets_per_project = seed_assets(db, m, org, owner, projects)
        seed_meldingen(db, m, org, users, assets_per_project)
        seed_inspecties(db, m, taxonomy, org, users, assets_per_project)
        print_summary(db, m, org)
        print(f"\n  Inloggen: {args.admin_email}  (wachtwoord via env WORKSHOP_PASSWORD)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
