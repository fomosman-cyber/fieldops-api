"""Inspectie-cyclus regels per norm.

Op basis van het asset-type + laatste conditiescore wordt de **volgende
inspectie-datum** automatisch geagendeerd. Norm-conforme cycli:

  - NEN 2767-2 / CROW 134 (kunstwerken): cyclus hangt af van score (1-6)
  - NEN 3399 (riolering): strengtoestand 1-5, vergelijkbaar met NEN 2767
  - CROW 146 (wegdek):    6 jaar standaard
  - NEN-EN 1176 (speeltoestellen): 12 maanden vast (wettelijk verplicht)
  - NEN 3140 (openbare verlichting): 5 jaar (60 mnd) vast
  - VTA (bomen): afhankelijk van VTA-risicoklasse 1-5

Houd deze module **data + pure functies** — geen DB-IO, geen FastAPI
dependencies. Maakt het volledig unit-testbaar zonder fixtures.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone


def _to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normaliseer datetime naar naive UTC voor SQLite-compat.

    SQLite slaat datetimes als naïef op. Sommige delen van de codebase
    gebruiken `datetime.now(timezone.utc)` (aware). Om vergelijkingen
    veilig te maken zetten we beide naar dezelfde wereld: naïef UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        # Aware → converteer naar UTC en strip tzinfo
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Cycle-regels per asset-type + score
# ─────────────────────────────────────────────────────────────────────────────

# NEN 2767-2 + CROW 134 voor kunstwerken
# Score 1-6 → cyclus in maanden
# Volgens CROW 134 maatregelmatrix
_NEN2767_CYCLE_MONTHS = {
    1: 72,   # Uitstekend → 6 jaar
    2: 48,   # Goed → 4 jaar
    3: 24,   # Redelijk → 2 jaar
    4: 12,   # Matig → 12 maanden
    5: 6,    # Slecht → 6 maanden
    6: 1,    # Zeer slecht → 1 maand (acuut + her-inspectie)
}

# NEN 3399 strengtoestand 1-5 voor riolering
# Eigen schaal, mapping op cyclus
_NEN3399_CYCLE_MONTHS = {
    1: 84,   # Uitstekend → 7 jaar
    2: 60,   # Goed → 5 jaar
    3: 36,   # Voldoende → 3 jaar
    4: 18,   # Slecht → 18 maanden
    5: 6,    # Zeer slecht → 6 maanden
}

# VTA bomen — risicoklasse 1-5 (in onze codebase score 1-6, vta5=6)
# Bomen degenereren niet-lineair: bij vta 4-5 acuut, niet de NEN-tussenwaarden
_VTA_CYCLE_MONTHS = {
    1: 36,   # Veilig → 3 jaar
    2: 24,   # Veilig, opvolgen → 2 jaar
    3: 12,   # Aandacht → 1 jaar
    4: 3,    # Risico → 3 maanden
    5: 1,    # Direct risico → 1 maand (na maatregel)
    6: 1,    # NEN 6 mapping (alias voor vta 5)
}

# Vaste cycli per object-type — geen score-afhankelijkheid
_FIXED_CYCLE_MONTHS = {
    "speeltoestel": 12,        # NEN-EN 1176 hoofdinspectie jaarlijks verplicht
    "verlichting": 60,         # NEN 3140 — 5 jaar elektrische keuring
    "lantaarnpaal": 60,        # alias voor verlichting
    "wegvak": 72,              # CROW 146 — 6 jaar visueel
    "wegdek": 72,              # alias
    "verharding": 72,          # alias
    "fontein": 24,              # NEN 2767-4 installaties
    "kunstgrasveld": 36,        # NEN 2767-4 specifiek
    "wegmarkering": 24,         # CROW 145 — 2-jaarlijkse retroreflectie-meting (default)
    "markering": 24,            # alias
    "belijning": 24,            # alias
}

# Wegmarkering-cyclus afhankelijk van weg-categorie (CROW 145 § 4.2).
# Voor stroomwegen en gebiedsontsluitingswegen: jaarlijkse RL-meting verplicht
# vanwege hogere snelheid + slijtage. Voor erftoegangswegen: 24 maanden.
_WEGMARKERING_CYCLE_BY_CAT = {
    "stroomweg":              12,
    "stroomwegen":            12,
    "hoofdweg":               12,
    "gebiedsontsluiting":     12,
    "gebiedsontsluitingsweg": 12,
    "erftoegang":             24,
    "erftoegangsweg":         24,
    "verblijfsgebied":        24,
    "fietspad":               24,
}


def wegmarkering_cycle_months(weg_categorie: Optional[str]) -> int:
    """Bepaal cyclus voor wegmarkering op basis van weg-categorie (CROW 145).

    Default (onbekend / niet ingevuld) = 24 maanden.

    >>> wegmarkering_cycle_months("stroomweg")
    12
    >>> wegmarkering_cycle_months("erftoegang")
    24
    >>> wegmarkering_cycle_months(None)
    24
    """
    if not weg_categorie:
        return 24
    return _WEGMARKERING_CYCLE_BY_CAT.get(weg_categorie.strip().lower(), 24)

# NEN-EN 1176 multi-cyclus voor speeltoestellen — kies kind per inspectie.
# Bron: NEN-EN 1176-7 § 6 + Warenwetbesluit Attractie- en Speeltoestellen art. 13.
#   routine      = visuele inspectie (vandalisme, schoonmaak, zwerfvuil)
#                  -> wekelijks voor druk-bezochte, anders 2-wekelijks
#   operationeel = functionele test (beweegbare delen, slijtage, bevestiging)
#                  -> maandelijks tot driemaandelijks
#   hoofd        = uitgebreide inspectie inclusief constructieve onderdelen
#                  -> jaarlijks verplicht
NEN1176_CYCLE_DAYS = {
    "routine": 7,
    "operationeel": 30,
    "hoofd": 365,
}


def nen1176_next_due_days(kind: Optional[str]) -> Optional[int]:
    """Geef het aantal dagen tot de volgende NEN-EN 1176 inspectie van dit kind.

    >>> nen1176_next_due_days("routine")
    7
    >>> nen1176_next_due_days("hoofd")
    365
    >>> nen1176_next_due_days(None) is None
    True
    """
    if not kind:
        return None
    return NEN1176_CYCLE_DAYS.get(kind.strip().lower())

# Asset-types die de NEN 2767-2 score-cyclus gebruiken
_NEN2767_TYPES = {
    "brug", "viaduct", "tunnel", "sluis", "stuw",
    "duiker", "kademuur", "gemaal", "kunstwerk",
}

_NEN3399_TYPES = {
    "riolering", "riool", "vrijvervalriool",
    "rioolput", "inspectieput", "rio_gemaal", "overstort",
}

_VTA_TYPES = {
    "boom", "laanboom", "monumentale-boom", "park-boom",
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API — cyclus berekening
# ─────────────────────────────────────────────────────────────────────────────

def cycle_months_for(asset_type: Optional[str],
                     conditie_score: Optional[int]) -> Optional[int]:
    """Bepaal de inspectie-cyclus in maanden voor dit asset.

    Returns None als geen regel matcht (UI valt dan terug op handmatige
    invoer of een default-cyclus van 60 maanden).

    >>> cycle_months_for("brug", 1)
    72
    >>> cycle_months_for("brug", 4)
    12
    >>> cycle_months_for("boom", 5)
    1
    >>> cycle_months_for("speeltoestel", None)
    12
    >>> cycle_months_for("verlichting", 3)
    60
    >>> cycle_months_for(None, 3) is None
    True
    """
    if not asset_type:
        return None
    key = asset_type.strip().lower()

    # 1. Vaste cycli (speeltoestel, verlichting, wegdek) — score-onafhankelijk
    # Voor wegmarkering kan een asset-specifieke weg_categorie de cyclus
    # overschrijven (zie wegmarkering_cycle_months helper). Caller geeft die
    # info via een aparte route.
    if key in _FIXED_CYCLE_MONTHS:
        return _FIXED_CYCLE_MONTHS[key]

    # 2. VTA bomen — afhankelijk van score (risicoklasse)
    if key in _VTA_TYPES:
        if conditie_score is None:
            return _VTA_CYCLE_MONTHS[1]  # default voor nieuwe bomen
        return _VTA_CYCLE_MONTHS.get(int(conditie_score), 12)

    # 3. NEN 3399 riolering
    if key in _NEN3399_TYPES:
        if conditie_score is None:
            return _NEN3399_CYCLE_MONTHS[1]
        # NEN 3399 is 1-5, accepteer ook 1-6 (clip 6→5)
        s = min(int(conditie_score), 5) if conditie_score else 1
        return _NEN3399_CYCLE_MONTHS.get(s, 36)

    # 4. NEN 2767-2 kunstwerken
    if key in _NEN2767_TYPES:
        if conditie_score is None:
            return _NEN2767_CYCLE_MONTHS[1]
        return _NEN2767_CYCLE_MONTHS.get(int(conditie_score), 24)

    return None


def next_due_date(last_inspected: Optional[datetime],
                  cycle_months: Optional[int]) -> Optional[datetime]:
    """Bereken volgende inspectie-datum.

    >>> from datetime import datetime, timezone
    >>> last = datetime(2026, 1, 15, tzinfo=timezone.utc)
    >>> next_due_date(last, 12).date().isoformat()
    '2027-01-15'
    >>> next_due_date(None, 12) is None
    True
    >>> next_due_date(last, None) is None
    True
    """
    if not last_inspected or not cycle_months:
        return None
    # Naïef per maand: 30.44 dagen gemiddeld → cycle_months × 30.44
    # Voor exacte kalender-rekening zou je dateutil.relativedelta gebruiken,
    # maar dat is een extra dep. ±30 dagen drift over 6 jaar is acceptabel
    # voor inspectie-planning (dagen-precisie is genoeg).
    return last_inspected + timedelta(days=int(cycle_months * 30.44))


def is_overdue(next_due: Optional[datetime],
               today: Optional[datetime] = None) -> bool:
    """True als de inspectie verlopen is.

    Normaliseert tzinfo om SQLite-naïeve datetimes te accepteren.

    >>> from datetime import datetime, timezone
    >>> past = datetime(2025, 1, 1, tzinfo=timezone.utc)
    >>> future = datetime(2027, 1, 1, tzinfo=timezone.utc)
    >>> now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    >>> is_overdue(past, now)
    True
    >>> is_overdue(future, now)
    False
    >>> is_overdue(None, now)
    False
    """
    if not next_due:
        return False
    nd = _to_naive_utc(next_due)
    now = _to_naive_utc(today or datetime.now(timezone.utc))
    return nd < now


def days_until_due(next_due: Optional[datetime],
                   today: Optional[datetime] = None) -> Optional[int]:
    """Aantal dagen tot volgende inspectie. Negatief = verlopen.

    Normaliseert tzinfo om SQLite-naïeve datetimes te accepteren.

    >>> from datetime import datetime, timezone
    >>> due = datetime(2026, 6, 15, tzinfo=timezone.utc)
    >>> now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    >>> days_until_due(due, now)
    31
    >>> days_until_due(None, now) is None
    True
    """
    if not next_due:
        return None
    nd = _to_naive_utc(next_due)
    now = _to_naive_utc(today or datetime.now(timezone.utc))
    return (nd - now).days


def compliance_status(next_due: Optional[datetime],
                      today: Optional[datetime] = None) -> str:
    """Classificeer compliance-status voor UI:

    - "compliant"   : volgende > 30 dagen
    - "due-soon"    : volgende binnen 30 dagen
    - "overdue"     : volgende in verleden
    - "unscheduled" : geen volgende-datum gepland

    >>> from datetime import datetime, timezone, timedelta
    >>> now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    >>> compliance_status(now + timedelta(days=60), now)
    'compliant'
    >>> compliance_status(now + timedelta(days=10), now)
    'due-soon'
    >>> compliance_status(now - timedelta(days=5), now)
    'overdue'
    >>> compliance_status(None, now)
    'unscheduled'
    """
    if not next_due:
        return "unscheduled"
    days = days_until_due(next_due, today)
    if days is None:
        return "unscheduled"
    if days < 0:
        return "overdue"
    if days <= 30:
        return "due-soon"
    return "compliant"


def norm_reference(asset_type: Optional[str]) -> str:
    """Welke norm regelt de cyclus voor dit asset-type — voor UI/PDF.

    >>> norm_reference("brug")
    'NEN 2767-2 / CROW 134'
    >>> norm_reference("boom")
    'VTA / CROW 200'
    >>> norm_reference("speeltoestel")
    'NEN-EN 1176/1177'
    >>> norm_reference("riolering")
    'NEN 3399'
    >>> norm_reference("verlichting")
    'NEN 3140'
    >>> norm_reference("onbekend")
    'NEN 2767'
    """
    if not asset_type:
        return "NEN 2767"
    key = asset_type.strip().lower()
    if key in _VTA_TYPES:
        return "VTA / CROW 200"
    if key in _NEN3399_TYPES:
        return "NEN 3399"
    if key in _NEN2767_TYPES:
        return "NEN 2767-2 / CROW 134"
    if key in ("speeltoestel",):
        return "NEN-EN 1176/1177"
    if key in ("verlichting", "lantaarnpaal"):
        return "NEN 3140"
    if key in ("wegvak", "wegdek", "verharding"):
        return "CROW 146"
    if key in ("fontein", "kunstgrasveld"):
        return "NEN 2767-4"
    if key in ("wegmarkering", "markering", "belijning"):
        return "CROW 145"
    return "NEN 2767"


# ─────────────────────────────────────────────────────────────────────────────
# Versie-tag — voor audit-trail mocht de cyclus-tabel ooit wijzigen
# ─────────────────────────────────────────────────────────────────────────────

CYCLE_RULES_VERSION = "cycle.v1.0-2026-05"
