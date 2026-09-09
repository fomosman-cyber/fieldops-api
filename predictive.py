"""Predictive Maintenance v2 — regelgebaseerde risicoscore per asset, met CROW.

Output: integer 0-100 + "rationale" — een lijstje feiten die de score onderbouwen.
Bewust regelgebaseerd in v0: een controllable, uitlegbare baseline.

Vier kerncomponenten (gewogen naar 100):
- leeftijd-fractie van verwachte levensduur (max 25 punten)
- NEN-conditiescore 1-5 (max 25 punten)
- ergste CROW-klasse (L1..E3) op recente meldingen (max 30 punten)
- meldingen-historie laatste 12 maanden (max 20 punten)

v2.1-trend toevoegingen:
- trend-bonus (max 10 punten, na cap) — meldingen-frequentie 90d vs voorgaand 90d
- confidence (0.0-1.0) — gebaseerd op data-completeness, niet op modelvertrouwen
- geo_cluster_signal — meldingen-density rond dit asset, voorzichtig signaleren

Compliance/transparantie: rationale is een list[str] met menselijke regels —
direct toonbaar in de UI naast de score.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional
import math
from sqlalchemy.orm import Session
from sqlalchemy import case, func

from models import Asset, Melding, Inspection, InspectionElement, InspectionDefect
from crow_kosten import klasse_to_risk_points, KLASSE_RISK_POINTS


SCORE_VERSION = "v2.2-norm-aware"

# Basis-wegingen (totaal 100)
W_AGE = 25
W_CONDITION = 25
W_CROW = 30
W_MELDINGEN = 20

# Bonus-wegingen — bovenop de basis 100, daarna gecapt op 100. Werken als
# "amplifiers" op assets die er al matig voor staan en aanvullende signalen
# laten zien.
W_TREND_MAX = 10
W_INSPECTION_MAX = 15   # v2.2 — formele inspectie-defecten zwaarder dan losse meldingen


def _age_fraction(asset: Asset, now: datetime) -> Optional[float]:
    if not asset.installed_at or not asset.expected_lifespan_years or asset.expected_lifespan_years <= 0:
        return None
    # SQLite returnt timezone-naive; UTC veronderstellen voor consistente delta
    installed = asset.installed_at
    if installed.tzinfo is None:
        installed = installed.replace(tzinfo=timezone.utc)
    age_years = (now - installed).total_seconds() / (365.25 * 24 * 3600)
    return max(0.0, age_years / asset.expected_lifespan_years)


def _condition_points(score: Optional[int]) -> int:
    """1=als-nieuw → 0pt; 5=zeer slecht → max."""
    if score is None:
        return 0
    s = max(1, min(5, score))
    return int(round((s - 1) / 4 * W_CONDITION))


class MeldingContext:
    """Alle meldinggegevens die de risicoscore nodig heeft, voor veel assets tegelijk.

    ``compute_asset_risk`` deed per asset vier aparte database-heenreizen: de
    CROW-klassen, de prioriteiten, en twee tellingen voor de trend. Voor een los
    asset is dat prima. Maar ``/api/predictive/summary`` draait er een lus
    overheen voor de hele organisatie, en dat is de endpoint achter het
    dashboard -- die draait bij elke keer openen.

    Bij vijftig assets waren dat ruim driehonderd queries. Bij een klant met
    vijfduizend wegvakken worden dat er twintigduizend. Op sqlite merk je dat
    nauwelijks; op PostgreSQL is elke query een round-trip over het netwerk.

    Deze klasse haalt hetzelfde op in drie queries, ongeacht het aantal assets.
    """

    __slots__ = ("klassen", "prioriteiten", "recent", "prior", "defecten",
                 "geo")

    # Venster van het buurt-signaal. Moet gelijk blijven aan de default van
    # _geo_cluster_signal, anders kijkt de gebatchte weg naar een andere periode
    # dan de losse.
    GEO_VENSTER_DAGEN = 30

    def __init__(self, db: Session, asset_ids: list[str], now: datetime,
                 organization_id: Optional[str] = None):
        self.klassen: dict[str, list[str]] = {}
        self.prioriteiten: dict[str, dict[str, int]] = {}
        self.recent: dict[str, int] = {}
        self.prior: dict[str, int] = {}
        self.defecten: dict[str, list] = {}
        self.geo: list[tuple] = []
        if not asset_ids:
            return

        een_jaar = now - timedelta(days=365)
        cutoff_recent = now - timedelta(days=90)
        cutoff_prior = now - timedelta(days=180)

        # 1. CROW-klassen van het afgelopen jaar.
        for aid, klasse in (db.query(Melding.asset_id, Melding.crow_klasse)
                              .filter(Melding.asset_id.in_(asset_ids),
                                      Melding.created_at >= een_jaar,
                                      Melding.crow_klasse.isnot(None))
                              .all()):
            self.klassen.setdefault(aid, []).append(klasse)

        # 2. Aantallen per prioriteit over hetzelfde jaar.
        for aid, prio, n in (db.query(Melding.asset_id, Melding.priority,
                                      func.count(Melding.id))
                               .filter(Melding.asset_id.in_(asset_ids),
                                       Melding.created_at >= een_jaar)
                               .group_by(Melding.asset_id, Melding.priority)
                               .all()):
            self.prioriteiten.setdefault(aid, {})[prio] = int(n)

        # 3. De twee trendvensters in een keer: laatste 90 dagen en de 90 dagen
        #    daarvoor, uit elkaar gehouden met een vlag in de group-by.
        recent_vlag = case((Melding.created_at >= cutoff_recent, 1), else_=0)
        for aid, is_recent, n in (db.query(Melding.asset_id, recent_vlag,
                                           func.count(Melding.id))
                                    .filter(Melding.asset_id.in_(asset_ids),
                                            Melding.created_at >= cutoff_prior)
                                    .group_by(Melding.asset_id, recent_vlag)
                                    .all()):
            doel = self.recent if int(is_recent) == 1 else self.prior
            doel[aid] = doel.get(aid, 0) + int(n)

        # 4. Inspectie-defecten van het afgelopen jaar. Twee functies vroegen
        #    hier apart om -- de historie-bonus en de type-specifieke floors --
        #    en die tweede stelde per asset-type ook nog eens meerdere vragen.
        for aid, defect in (db.query(Inspection.asset_id, InspectionDefect)
                              .join(InspectionElement,
                                    InspectionDefect.element_id == InspectionElement.id)
                              .join(Inspection,
                                    InspectionElement.inspection_id == Inspection.id)
                              .filter(Inspection.asset_id.in_(asset_ids),
                                      Inspection.created_at >= een_jaar)
                              .all()):
            self.defecten.setdefault(aid, []).append(defect)

        # 5. Meldingen met coordinaten in het buurt-venster. Het buurt-signaal
        #    deed hier per asset een bounding-box-query op, terwijl het daarna
        #    toch in Python op afstand filtert. Een keer ophalen en er per asset
        #    overheen lopen geeft precies hetzelfde antwoord.
        #
        #    Alleen de vier kolommen die het signaal gebruikt -- niet het hele
        #    Melding-object, want daar hangen de base64-foto's aan.
        if organization_id:
            geo_cutoff = now - timedelta(days=self.GEO_VENSTER_DAGEN)
            self.geo = (db.query(Melding.asset_id, Melding.lat, Melding.lng,
                                 Melding.crow_klasse)
                          .filter(Melding.organization_id == organization_id,
                                  Melding.created_at >= geo_cutoff,
                                  Melding.lat.isnot(None),
                                  Melding.lng.isnot(None))
                          .all())


def _melding_count_recent(db: Session, asset_id: str, since: datetime) -> tuple[int, int]:
    """Return (total, hoog_kritiek) over een tijdvenster."""
    rows = (db.query(Melding.priority, func.count(Melding.id))
              .filter(Melding.asset_id == asset_id, Melding.created_at >= since)
              .group_by(Melding.priority).all())
    total = 0
    hoog_kritiek = 0
    for pri, cnt in rows:
        total += int(cnt)
        if pri in ("hoog", "kritiek"):
            hoog_kritiek += int(cnt)
    return total, hoog_kritiek


def _melding_points(total: int, hoog_kritiek: int) -> int:
    """Max W_MELDINGEN (20). 3+ meldingen → cap; severe ↑."""
    base = min(W_MELDINGEN, total * 6)
    bonus = min(W_MELDINGEN - base, hoog_kritiek * 4)
    return int(min(W_MELDINGEN, base + bonus))


def _worst_crow_klasse(db: Session, asset_id: str, since: datetime) -> Optional[str]:
    """Geef de ergste CROW-klasse terug van meldingen in de afgelopen periode.
    Ranking: E3 > E2 > E1 > M3 > M2 > M1 > L3 > L2 > L1.
    """
    klassen = (db.query(Melding.crow_klasse)
                 .filter(Melding.asset_id == asset_id,
                         Melding.created_at >= since,
                         Melding.crow_klasse.isnot(None))
                 .all())
    return _ergste_klasse([k[0] for k in klassen])


def _ergste_klasse(klassen: list) -> Optional[str]:
    """De ergste CROW-klasse uit een lijst. Ranking E3 > E2 > ... > L1.

    Apart van de query, zodat de losse en de gebatchte weg gegarandeerd
    hetzelfde antwoord geven -- twee kopieen van deze sortering zouden vroeg of
    laat uit elkaar lopen.
    """
    valid = [k for k in klassen if k in KLASSE_RISK_POINTS]
    if not valid:
        return None
    return max(valid, key=klasse_to_risk_points)


def _crow_points(klasse: Optional[str]) -> int:
    """L1..E3 → punten gewogen naar W_CROW (30)."""
    if not klasse:
        return 0
    raw = klasse_to_risk_points(klasse)  # 0-48 op de raw schaal
    # Schaal naar W_CROW (30): max raw 48 → 30 punten
    return int(round(raw / 48 * W_CROW))


def _melding_trend(db: Session, asset_id: str, now: datetime) -> tuple[int, int, int]:
    """Vergelijk meldingen-frequentie laatste 90d vs daarvoor 90d.

    Return (recent_count, prior_count, trend_pts) — waar trend_pts 0..W_TREND_MAX is.

    Logica: als recent significant hoger ligt dan prior (gebruiken een ratio),
    dan is er een verslechterende trend. Een asset met 1→1 meldingen scoort 0;
    een asset met 1→4 (4x) krijgt een paar punten; 0→3 ook (uit het niets).
    """
    cutoff_recent = now - timedelta(days=90)
    cutoff_prior = now - timedelta(days=180)

    recent = db.query(func.count(Melding.id)).filter(
        Melding.asset_id == asset_id,
        Melding.created_at >= cutoff_recent,
    ).scalar() or 0
    prior = db.query(func.count(Melding.id)).filter(
        Melding.asset_id == asset_id,
        Melding.created_at >= cutoff_prior,
        Melding.created_at < cutoff_recent,
    ).scalar() or 0

    recent = int(recent)
    prior = int(prior)
    return recent, prior, _trend_punten(recent, prior)


def _trend_punten(recent: int, prior: int) -> int:
    """Trendpunten uit twee tellingen. Apart van de query, om dezelfde reden
    als _ergste_klasse: een tweede kopie van deze drempels zou gaan afwijken."""
    # Geen data — geen trend
    if recent == 0:
        return 0

    # Uit-het-niets-uitbarsting: prior=0, recent>0 → schaal op recent
    if prior == 0:
        # 1 melding alleen is geen trend, 2+ wel
        return min(W_TREND_MAX, max(0, (recent - 1) * 3))

    # Ratio-gebaseerde stijging
    ratio = recent / prior
    if ratio <= 1.2:
        return 0
    if ratio <= 2.0:
        return 4
    if ratio <= 3.0:
        return 7
    return W_TREND_MAX


def _confidence(asset: Asset, *, has_meldingen: bool, has_crow_classification: bool) -> float:
    """Heuristische confidence — hoeveel van de 4 input-bronnen ingevuld zijn.

    Dit is *data-completeness*, niet model-vertrouwen. Een score van 0.25 zegt:
    "we hebben maar 1 van de 4 datapunten waar deze score op rust, neem 'm
    met een korreltje zout." Helpt admins prioriteren waar ze data moeten
    aanvullen.
    """
    parts = [
        asset.installed_at is not None and asset.expected_lifespan_years is not None,
        asset.condition_score is not None,
        has_meldingen,
        has_crow_classification,
    ]
    return round(sum(1 for p in parts if p) / len(parts), 2)


def _geo_cluster_signal(db: Session, asset: Asset, now: datetime,
                        radius_m: int = 200, window_days: int = 30,
                        voorgeladen: Optional[list] = None) -> Optional[dict]:
    """Tel meldingen binnen radius+window rond dit asset (excl. asset's eigen
    meldingen). Een hoge density wijst op buurt-brede problemen die een
    enkele asset-score niet vangt — bv. een straat met scheurvorming over
    meerdere wegvakken.

    Output is een signaal, geen score-bonus — bewust gescheiden zodat de
    asset-score niet stijgt door problemen op de buren.
    """
    if asset.lat is None or asset.lng is None:
        return None
    cutoff = now - timedelta(days=window_days)
    # Bounding-box-prefilter (snel met index op organization_id),
    # daarna haversine-filter in Python. Voor een Render Starter-tier DB met
    # max ~10k meldingen/org acceptabel; bij grotere volumes kan dit naar
    # PostGIS verhuizen.
    deg_per_m_lat = 1 / 111_320  # ~constant
    deg_per_m_lng = 1 / (111_320 * max(0.01, math.cos(math.radians(asset.lat))))
    box_lat = radius_m * deg_per_m_lat * 1.2  # 20% marge voor box-vs-cirkel
    box_lng = radius_m * deg_per_m_lng * 1.2

    if voorgeladen is not None:
        # Al opgehaald voor de hele lijst; hier alleen nog de box eromheen.
        kandidaten = [(aid, lat, lng, klasse) for aid, lat, lng, klasse in voorgeladen
                      if aid != asset.id
                      and asset.lat - box_lat <= lat <= asset.lat + box_lat
                      and asset.lng - box_lng <= lng <= asset.lng + box_lng]
    else:
        kandidaten = [(m.asset_id, m.lat, m.lng, m.crow_klasse) for m in db.query(
            Melding.asset_id, Melding.lat, Melding.lng, Melding.crow_klasse).filter(
            Melding.organization_id == asset.organization_id,
            Melding.asset_id != asset.id,
            Melding.created_at >= cutoff,
            Melding.lat.isnot(None), Melding.lng.isnot(None),
            Melding.lat.between(asset.lat - box_lat, asset.lat + box_lat),
            Melding.lng.between(asset.lng - box_lng, asset.lng + box_lng),
        ).all()]

    nearby = [k for k in kandidaten
              if _haversine_m(asset.lat, asset.lng, k[1], k[2]) <= radius_m]
    if not nearby:
        return None

    # Hottest klasse in de buurt (zelfde ranking als _worst_crow_klasse)
    classified = [k[3] for k in nearby if k[3] in KLASSE_RISK_POINTS]
    hottest = max(classified, key=klasse_to_risk_points) if classified else None

    return {
        "nearby_count": len(nearby),
        "radius_m": radius_m,
        "window_days": window_days,
        "hottest_klasse": hottest,
    }


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Geodetische afstand in meters tussen twee WGS84-punten. Bedoeld voor
    radius-checks tot enkele kilometers — voor langere afstanden zou je
    Vincenty willen, maar dat is hier overkill."""
    R = 6_371_000  # aardstraal in meters
    lat1r = math.radians(lat1); lat2r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(lat1r)*math.cos(lat2r)*math.sin(dlng/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def _inspection_history_points(db: Session, asset_id: str, since: datetime) -> tuple[int, int, int]:
    """v2.2 — Formele inspectie-defecten als signaal naast losse meldingen.

    Een defect uit een ondertekende NEN 2767-inspectie weegt zwaarder dan een
    burger-melding: het is door een gekwalificeerde inspecteur geclassificeerd.

    Return (punten, totaal_defecten, ernstige_defecten met score>=4).
    """
    # Defecten op alle elementen van inspecties op dit asset, recent
    rows = (db.query(InspectionDefect)
              .join(InspectionElement, InspectionDefect.element_id == InspectionElement.id)
              .join(Inspection, InspectionElement.inspection_id == Inspection.id)
              .filter(Inspection.asset_id == asset_id,
                      Inspection.created_at >= since)
              .all())
    return _inspectie_punten(rows)


def _inspectie_punten(rows: list) -> tuple[int, int, int]:
    """Punten uit een lijst defecten. Apart van de query zodat de losse en de
    gebatchte weg dezelfde uitkomst geven."""
    if not rows:
        return 0, 0, 0
    total = len(rows)
    severe = sum(1 for d in rows if d.defect_score is not None and d.defect_score >= 4)
    # Punten: 5 per ernstig defect + 1 per overig, cap W_INSPECTION_MAX
    pts = min(W_INSPECTION_MAX, severe * 5 + (total - severe) * 1)
    return int(pts), total, severe


def _override_uit_defecten(asset: Asset, defecten: list) -> Optional[tuple[int, str]]:
    """Dezelfde type-specifieke floors als _type_specific_override, maar op een
    lijst die al is opgehaald.

    De regels staan hier een keer; _type_specific_override haalt de defecten op
    en geeft ze hieraan door. Twee kopieen van deze drempels zouden vroeg of
    laat uit elkaar lopen, en dan scoort dezelfde boom op het dashboard anders
    dan in de drilldown.
    """
    if not defecten:
        return None

    def _eerste(voorwaarde):
        for d in defecten:
            try:
                if voorwaarde(d):
                    return d
            except TypeError:
                continue
        return None

    # Speeltoestel — NEN-EN 1176 categorie C/D
    if asset.asset_type == "speeltoestel":
        if _eerste(lambda d: d.en1176_categorie == "D"):
            return 95, "NEN-EN 1176 categorie D defect (afgesloten) → forceer 95+"
        if _eerste(lambda d: d.en1176_categorie == "C"):
            return 80, "NEN-EN 1176 categorie C defect (gebruik beperken) → forceer 80+"

    # Boom — VTA Mattheck risicoklasse 5 of t/r < 0.30
    if asset.asset_type == "boom":
        if _eerste(lambda d: d.vta_risicoklasse == 5):
            return 95, "VTA risicoklasse 5 (acute breekrisico) → forceer 95+"
        if _eerste(lambda d: d.vta_t_r_ratio is not None and d.vta_t_r_ratio < 0.30):
            return 90, "VTA Mattheck t/r < 0.30 (breukrisico) → forceer 90+"

    # Verlichting — NEN 3140 isolatie-resistance onveilig
    if asset.asset_type == "verlichting":
        if _eerste(lambda d: d.nen3140_isolatie_megaohm is not None
                   and d.nen3140_isolatie_megaohm < 0.5):
            return 80, "NEN 3140 isolatie < 0.5 MΩ (elektrische onveiligheid) → forceer 80+"

    # Wegmarkering — CROW 145 RL droog < 80 mcd = vervangen
    if asset.asset_type == "wegmarkering":
        if _eerste(lambda d: d.crow145_rl_droog_mcd is not None
                   and d.crow145_rl_droog_mcd < 80):
            return 70, "CROW 145 retroreflectie droog < 80 mcd (vervang-drempel) → forceer 70+"

    # Riolering — NEN 3399 eindklasse 5 = acute vervanging
    if asset.asset_type == "riolering":
        if _eerste(lambda d: d.nen3399_klasse == 5):
            return 90, "NEN 3399 eindklasse 5 (direct vervangen) → forceer 90+"

    return None


def _type_specific_override(asset: Asset, db: Session, since: datetime) -> Optional[tuple[int, str]]:
    """v2.2 — Type-specifieke regels die de score forceren bovenop het basis-model.

    Wanneer een norm-specifiek defect kritiek is (NEN-EN 1176 cat C/D, VTA
    klasse 5, NEN 3140 isolatie < 0.5 MOhm), is het juridisch en operationeel
    onverdedigbaar om dat asset op laag risico te scoren. Deze regels zetten
    een minimum-score (floor) die de eindscore garandeert.

    Haalt de defecten op en laat _override_uit_defecten het oordeel vellen. De
    drempels staan dus op een plek: dezelfde boom moet op het dashboard niet
    anders scoren dan in de drilldown.

    Return (floor_score, reden_tekst) of None.
    """
    defecten = (db.query(InspectionDefect)
                  .join(InspectionElement,
                        InspectionDefect.element_id == InspectionElement.id)
                  .join(Inspection, InspectionElement.inspection_id == Inspection.id)
                  .filter(Inspection.asset_id == asset.id,
                          Inspection.created_at >= since)
                  .all())
    return _override_uit_defecten(asset, defecten)


def compute_asset_risk(db: Session, asset: Asset,
                       ctx: "MeldingContext | None" = None) -> dict:
    """Bereken risicoscore + uitleg. Werkt op een asset.

    Geef `ctx` mee als je dit voor veel assets achter elkaar doet: dan komen de
    meldinggegevens uit drie groepsqueries in plaats van vier queries per asset.
    Zonder ctx blijft het gedrag precies zoals het was -- de losse
    asset-drilldown heeft die batch niet nodig en zou er alleen trager van worden.
    """
    now = datetime.now(timezone.utc)

    rationale: list[str] = []

    # Leeftijd
    age_frac = _age_fraction(asset, now)
    if age_frac is None:
        age_pts = 0
        rationale.append("Geen leeftijd-/levensduur-data — leeftijdsfactor 0.")
    else:
        # >100% van levensduur → max; 50% → de helft van max
        age_pts = int(round(min(1.5, age_frac) / 1.5 * W_AGE))
        pct = round(age_frac * 100)
        if age_frac >= 1.0:
            rationale.append(f"Levensduur overschreden ({pct}% van verwachte {asset.expected_lifespan_years} jaar) — +{age_pts} pt.")
        elif age_frac >= 0.7:
            rationale.append(f"Nadert einde levensduur ({pct}%) — +{age_pts} pt.")
        else:
            rationale.append(f"Binnen levensduur ({pct}%) — +{age_pts} pt.")

    # Conditie
    cond_pts = _condition_points(asset.condition_score)
    if asset.condition_score is None:
        rationale.append("Geen NEN-conditiescore vastgelegd — conditiefactor 0.")
    else:
        rationale.append(f"NEN-conditiescore {asset.condition_score} (1=als-nieuw, 5=zeer slecht) — +{cond_pts} pt.")

    # Ergste CROW-klasse op recente meldingen (nieuw in v2.0)
    one_year_ago = now - timedelta(days=365)
    if ctx is not None:
        worst_klasse = _ergste_klasse(ctx.klassen.get(asset.id, []))
    else:
        worst_klasse = _worst_crow_klasse(db, asset.id, one_year_ago)
    crow_pts = _crow_points(worst_klasse)
    if worst_klasse:
        cat_map = {"L": "observatie", "M": "klein onderhoud", "E": "groot onderhoud"}
        ernst_letter = worst_klasse[0]
        rationale.append(
            f"Ergste CROW-klasse op recente meldingen: {worst_klasse} ({cat_map.get(ernst_letter, '')}) — +{crow_pts} pt."
        )
    else:
        rationale.append("Geen CROW-classificatie op recente meldingen — CROW-factor 0.")

    # Meldingen-aantal
    if ctx is not None:
        per_prio = ctx.prioriteiten.get(asset.id, {})
        total_m = sum(per_prio.values())
        severe_m = sum(n for p, n in per_prio.items() if p in ("hoog", "kritiek"))
    else:
        total_m, severe_m = _melding_count_recent(db, asset.id, one_year_ago)
    mel_pts = _melding_points(total_m, severe_m)
    if total_m == 0:
        rationale.append("Geen meldingen in afgelopen 12 maanden — meldingfactor 0.")
    else:
        sev = f", waarvan {severe_m} hoog/kritiek" if severe_m else ""
        rationale.append(f"{total_m} meldingen in afgelopen 12 mnd{sev} — +{mel_pts} pt.")

    base_score = age_pts + cond_pts + crow_pts + mel_pts
    base_score = max(0, min(100, base_score))

    # Trend-bonus (v2.1) — voegt toe bovenop base, maar wordt na cap niet boven 100.
    if ctx is not None:
        recent_m = ctx.recent.get(asset.id, 0)
        prior_m = ctx.prior.get(asset.id, 0)
        trend_pts = _trend_punten(recent_m, prior_m)
    else:
        recent_m, prior_m, trend_pts = _melding_trend(db, asset.id, now)
    if trend_pts > 0:
        rationale.append(
            f"Toenemende meldingstrend ({prior_m}→{recent_m} in voorgaande 90d "
            f"vs laatste 90d) — +{trend_pts} pt."
        )

    # Inspectie-historie-bonus (v2.2) — formele defecten uit ondertekende inspecties
    if ctx is not None:
        insp_pts, insp_total, insp_severe = _inspectie_punten(
            ctx.defecten.get(asset.id, []))
    else:
        insp_pts, insp_total, insp_severe = _inspection_history_points(
            db, asset.id, one_year_ago)
    if insp_pts > 0:
        sev_text = f", waarvan {insp_severe} score≥4" if insp_severe else ""
        rationale.append(
            f"{insp_total} inspectie-defecten in afgelopen 12 mnd{sev_text} — +{insp_pts} pt."
        )

    score = max(0, min(100, base_score + trend_pts + insp_pts))

    # Type-specifieke overrides (v2.2) — forceer score-floor bij norm-kritieke defecten
    if ctx is not None:
        override = _override_uit_defecten(asset, ctx.defecten.get(asset.id, []))
    else:
        override = _type_specific_override(asset, db, one_year_ago)
    override_applied = None
    if override:
        floor, reason = override
        if floor > score:
            rationale.append(f"⚠ NORM-OVERRIDE: {reason}")
            score = floor
            override_applied = {"floor": floor, "reason": reason}

    # Bandbepaling — drempels iets aangescherpt voor CROW-aware schaal
    if score >= 65:
        band = "hoog"
        recommendation = "Plan inspectie of preventief onderhoud binnen 4 weken."
    elif score >= 35:
        band = "matig"
        recommendation = "Overweeg inspectie binnen 3 maanden — check op LVO-kandidaat."
    else:
        band = "laag"
        recommendation = "Geen acute actie nodig; volg reguliere CROW-jaarcyclus."

    confidence = _confidence(
        asset,
        has_meldingen=(total_m > 0),
        has_crow_classification=(worst_klasse is not None),
    )

    geo_signal = _geo_cluster_signal(
        db, asset, now, voorgeladen=(ctx.geo if ctx is not None else None))
    if geo_signal:
        rationale.append(
            f"{geo_signal['nearby_count']} meldingen binnen "
            f"{geo_signal['radius_m']}m laatste {geo_signal['window_days']}d "
            f"(buurt-signaal, geen score-effect)."
        )

    return {
        "asset_id": asset.id,
        "asset_code": asset.code,
        "asset_type": asset.asset_type,
        "score": score,
        "band": band,
        "confidence": confidence,
        "components": {
            "age": age_pts,
            "condition": cond_pts,
            "crow": crow_pts,
            "meldingen": mel_pts,
            "trend": trend_pts,
            "inspection_history": insp_pts,  # v2.2
        },
        "trend": {
            "recent_90d": recent_m,
            "prior_90d": prior_m,
            "points": trend_pts,
        },
        "inspection_history": {  # v2.2
            "total_defects_12m": insp_total,
            "severe_defects_score_4plus": insp_severe,
            "points": insp_pts,
        },
        "norm_override": override_applied,  # v2.2 — None of {floor, reason}
        "geo_cluster": geo_signal,
        "worst_crow_klasse": worst_klasse,
        "rationale": rationale,
        "recommendation": recommendation,
        "score_version": SCORE_VERSION,
        "computed_at": now.isoformat(),
    }


def list_at_risk(db: Session, organization_id: str, *,
                 min_score: int = 60, asset_type: Optional[str] = None,
                 project_id: Optional[str] = None,
                 limit: int = 100) -> list[dict]:
    """Geef assets terug met risk_score >= min_score. Berekent on-demand.

    project_id filtert op assets van één project — gebruikt door de
    Voorspeller om per project te filteren (#13).
    """
    q = db.query(Asset).filter(
        Asset.organization_id == organization_id,
        Asset.archived_at.is_(None),
    )
    if asset_type:
        q = q.filter(Asset.asset_type == asset_type)
    if project_id:
        q = q.filter(Asset.project_id == project_id)
    assets = q.all()

    # Een context voor de hele lijst: drie queries in plaats van vier per asset.
    ctx = MeldingContext(db, [a.id for a in assets], datetime.now(timezone.utc),
                         organization_id=organization_id)
    results = [compute_asset_risk(db, a, ctx) for a in assets]
    results = [r for r in results if r["score"] >= min_score]
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def find_geo_clusters(db: Session, organization_id: str, *,
                      window_days: int = 30,
                      radius_m: int = 200,
                      min_count: int = 3) -> list[dict]:
    """Vind groepen meldingen die geografisch dicht bij elkaar liggen binnen
    een tijdvenster — wijk-brede problemen die per-asset-scoring mist.

    Greedy clustering: pak een ongeziene melding als seed, verzamel alle nog
    ongeziene meldingen binnen `radius_m`, dat is één cluster. Herhaal tot er
    geen seeds meer over zijn. Niet optimaal qua centroid maar uitlegbaar en
    deterministisch — voldoende voor een ops-dashboard.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    meldingen = db.query(Melding).filter(
        Melding.organization_id == organization_id,
        Melding.created_at >= cutoff,
        Melding.lat.isnot(None), Melding.lng.isnot(None),
    ).all()

    seen: set[str] = set()
    clusters: list[dict] = []

    for seed in meldingen:
        if seed.id in seen:
            continue
        members = [seed]
        seen.add(seed.id)
        for m in meldingen:
            if m.id in seen:
                continue
            if _haversine_m(seed.lat, seed.lng, m.lat, m.lng) <= radius_m:
                members.append(m)
                seen.add(m.id)
        if len(members) < min_count:
            continue

        avg_lat = sum(m.lat for m in members) / len(members)
        avg_lng = sum(m.lng for m in members) / len(members)
        classified = [m.crow_klasse for m in members if m.crow_klasse in KLASSE_RISK_POINTS]
        hottest = max(classified, key=klasse_to_risk_points) if classified else None

        # Bepaal severity-band aan de hand van hottest klasse + count
        if hottest and hottest[0] == "E":
            severity = "hoog"
        elif hottest and hottest[0] == "M":
            severity = "matig"
        elif len(members) >= min_count + 3:
            severity = "matig"
        else:
            severity = "laag"

        clusters.append({
            "center_lat": round(avg_lat, 6),
            "center_lng": round(avg_lng, 6),
            "count": len(members),
            "radius_m": radius_m,
            "window_days": window_days,
            "hottest_klasse": hottest,
            "severity": severity,
            "asset_ids": sorted({m.asset_id for m in members if m.asset_id}),
            "melding_ids": [m.id for m in members],
        })

    clusters.sort(key=lambda c: (c["count"], c["hottest_klasse"] or ""), reverse=True)
    return clusters
