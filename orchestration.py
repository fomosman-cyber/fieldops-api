"""Job Orchestration Engine — sinds v3.0 (mei 2026).

Twee kernfuncties:
1. SKILL-MATCHING — bij notificaties wordt de doelgroep gefilterd op vereiste
   skill voor de maatregel. Geen broadcast meer naar iedereen.
2. CLUSTERING — open meldingen met dezelfde gw_term + nabije locatie worden
   gegroepeerd in JobCluster-records, zodat een ploeg de hele dag dezelfde
   maatregel kan uitvoeren (Vullen polymeer 8u i.p.v. 4 setups op 1 dag).

Productiviteit-economics:
  Setup-tijd per techniek-omschakeling kost ~30-90 min. Een aannemer-ploeg
  die de hele dag scheurvulling doet maakt 200-300 m¹ vs 100-150 m¹ in mixed
  werk. FieldOps berekent het verschil per cluster en toont het als
  "productivity_savings_hours" — direct verkoopbaar als ROI-getal.
"""

from __future__ import annotations
import json
from math import radians, sin, cos, sqrt, atan2
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from models import Melding, User, UserSkill, JobCluster
from werksoorten import WERKSOORT_SKILL, synchroniseer as werksoort_synchroniseer
from crow_kosten import (
    maatregel_to_skill,
    estimate_cluster_hours,
    SKILL_CODES,
    PRODUCTIVITY_PER_SKILL,
)

# ════════════════════════════════════════════════════════════
# Skill-matching — wie krijgt notificaties?
# ════════════════════════════════════════════════════════════

def users_with_skill(
    db: Session,
    organization_id: str,
    skill_code: str,
    *,
    min_proficiency: int = 1,
    exclude_user_id: Optional[str] = None,
) -> list[User]:
    """Geef alle users in een org terug die een specifieke skill hebben.

    Sorteert op proficiency descending (experts eerst). Optioneel filteren
    op minimum proficiency-level (1=junior, 5=expert).
    """
    q = (db.query(User)
           .join(UserSkill, UserSkill.user_id == User.id)
           .filter(
               User.organization_id == organization_id,
               UserSkill.skill_code == skill_code,
               UserSkill.proficiency >= min_proficiency,
           ))
    if exclude_user_id:
        q = q.filter(User.id != exclude_user_id)
    return q.order_by(UserSkill.proficiency.desc()).all()


def maatregel_specialists(
    db: Session,
    organization_id: str,
    maatregel: Optional[str],
    *,
    exclude_user_id: Optional[str] = None,
) -> list[User]:
    """Geef alle specialisten terug voor een specifieke maatregel.

    Onbekende of generieke maatregelen → lege lijst (= broadcast naar
    iedereen behalve actor in audit.py fallback).
    """
    skill = maatregel_to_skill(maatregel)
    if not skill:
        return []
    return users_with_skill(db, organization_id, skill, exclude_user_id=exclude_user_id)


# ════════════════════════════════════════════════════════════
# Geo-helpers
# ════════════════════════════════════════════════════════════

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Afstand in km tussen twee GPS-coordinaten."""
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = (sin(dlat / 2) ** 2 +
         cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2)
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def geo_bounding(meldingen: Iterable[Melding]) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Bounding box van een set meldingen — (lat_min, lat_max, lng_min, lng_max)."""
    coords = [(m.lat, m.lng) for m in meldingen if m.lat is not None and m.lng is not None]
    if not coords:
        return (None, None, None, None)
    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]
    return (min(lats), max(lats), min(lngs), max(lngs))


def label_for_bbox(lat_min, lat_max, lng_min, lng_max) -> Optional[str]:
    """Genereer een human-readable label voor een geo-bounding box."""
    if lat_min is None:
        return None
    # Centrum
    lat_c = (lat_min + lat_max) / 2
    lng_c = (lng_min + lng_max) / 2
    # Heel grof zone-bepaling op basis van lat in NL (50.7-53.5)
    zone = "Centraal"
    if lat_c > 52.7:
        zone = "Noord"
    elif lat_c < 51.7:
        zone = "Zuid"
    elif lng_c > 5.5:
        zone = "Oost"
    elif lng_c < 4.5:
        zone = "West"
    # Diameter in km
    if lat_min == lat_max and lng_min == lng_max:
        return f"Zone {zone} · enkele locatie"
    diameter = haversine_km(lat_min, lng_min, lat_max, lng_max)
    return f"Zone {zone} · diameter {diameter:.1f} km"


# ════════════════════════════════════════════════════════════
# Clustering-algoritme
# ════════════════════════════════════════════════════════════

DEFAULT_CLUSTER_RADIUS_KM = 5.0  # binnen 5 km bij elkaar = clusterbaar

# ── Dagpakketten ────────────────────────────────────────────────────
#
# Een cluster is een werkdag. De kengetallen in PRODUCTIVITY_PER_SKILL zeggen
# hoeveel uur een eenheid kost en hoeveel opzettijd een dag heeft; daaruit
# volgt wat een ploeg op een dag haalt: (werkdag - opzet) / uren per eenheid.
# Scheuren vullen komt zo op 280 m¹, hotbox op zo'n 8 plekken, klinkers
# herstraten op 88 m². Een organisatie met snellere of tragere ploegen zet er
# haar eigen getal voor in de plaats.

WERKDAG_UREN = 8.0
CLUSTER_GRENZEN = {"werkdag_uren": (4.0, 12.0), "max_afstand_km": (0.2, 50.0)}


def standaard_dagproductie(skill_code: Optional[str], werkdag_uren: float = WERKDAG_UREN) -> Optional[float]:
    if skill_code not in PRODUCTIVITY_PER_SKILL:
        return None
    rate, _eenheid, setup = PRODUCTIVITY_PER_SKILL[skill_code]
    if rate <= 0 or werkdag_uren <= setup:
        return None
    return round((werkdag_uren - setup) / rate, 1)


def cluster_instellingen(org) -> dict:
    """Werkdag, maximale afstand en dagproductie per werksoort voor een org.

    Kapot of leeg -> de kengetallen. Een eigen dagproductie telt alleen voor
    werksoorten die we kennen en als hij groter dan nul is.
    """
    ruw = getattr(org, "cluster_instellingen", None) if org is not None else None
    data: dict = {}
    if ruw:
        try:
            data = json.loads(ruw) or {}
        except (ValueError, TypeError):
            data = {}
    uit = {"werkdag_uren": WERKDAG_UREN, "max_afstand_km": 2.0, "dagproductie": {}}
    try:
        for sleutel, (laag, hoog) in CLUSTER_GRENZEN.items():
            if sleutel in data and laag <= float(data[sleutel]) <= hoog:
                uit[sleutel] = float(data[sleutel])
        for skill, waarde in (data.get("dagproductie") or {}).items():
            if skill in PRODUCTIVITY_PER_SKILL and float(waarde) > 0:
                uit["dagproductie"][skill] = float(waarde)
    except (TypeError, ValueError):
        pass
    return uit


def dagproductie_voor(skill_code: Optional[str], inst: dict) -> Optional[float]:
    eigen = inst.get("dagproductie", {}).get(skill_code or "")
    return eigen or standaard_dagproductie(skill_code, inst.get("werkdag_uren", WERKDAG_UREN))


def _dagpakketten(meldingen: list[Melding], eenheden: dict[str, float],
                  capaciteit: float, max_km: float) -> list[tuple[list[Melding], float]]:
    """Meldingen van één werksoort verdelen over werkdagen.

    Begin aan de rand van het werkgebied (de melding het verst van het midden),
    en loop steeds naar de dichtstbijzijnde melding die nog in de dag past --
    zolang die binnen `max_km` van het begin van de dag ligt. Past er niets
    meer bij, dan is de dag vol en begint de volgende. Zo wordt een dag een
    route door een stuk van de wijk in plaats van een cirkel om een willekeurig
    punt, en blijft er aan het eind geen losse melding aan de overkant van de
    stad over.

    Eén melding die meer is dan een dag werk (een hele straat opnieuw
    bestraten) wordt een pakket op zichzelf, van meer dan één werkdag.
    Meldingen zonder plek worden alleen op hoeveelheid verdeeld.
    """
    geo = [m for m in meldingen if m.lat is not None and m.lng is not None]
    zonder = [m for m in meldingen if m.lat is None or m.lng is None]
    pakketten: list[tuple[list[Melding], float]] = []

    rest = list(geo)
    while rest:
        mid_lat = sum(m.lat for m in rest) / len(rest)
        mid_lng = sum(m.lng for m in rest) / len(rest)
        begin = max(rest, key=lambda m: haversine_km(mid_lat, mid_lng, m.lat, m.lng))
        rest.remove(begin)
        dag, last, huidig = [begin], eenheden[begin.id], begin
        while last < capaciteit:
            kandidaten = sorted(
                (m for m in rest if haversine_km(begin.lat, begin.lng, m.lat, m.lng) <= max_km),
                key=lambda m: haversine_km(huidig.lat, huidig.lng, m.lat, m.lng))
            volgende = next((m for m in kandidaten if last + eenheden[m.id] <= capaciteit), None)
            if volgende is None:
                break
            rest.remove(volgende)
            dag.append(volgende)
            last += eenheden[volgende.id]
            huidig = volgende
        pakketten.append((dag, last))

    dag, last = [], 0.0
    for m in zonder:
        if dag and last + eenheden[m.id] > capaciteit:
            pakketten.append((dag, last))
            dag, last = [], 0.0
        dag.append(m)
        last += eenheden[m.id]
    if dag:
        pakketten.append((dag, last))
    return pakketten


def _greedy_geo_clusters(
    meldingen: list[Melding],
    radius_km: float = DEFAULT_CLUSTER_RADIUS_KM,
) -> list[list[Melding]]:
    """Eenvoudige greedy clustering: één melding als seed, alle binnen radius
    erbij, herhaal tot elke melding gegroepeerd is. Werkt goed voor < 200
    open meldingen per gw_term.
    """
    remaining = [m for m in meldingen if m.lat is not None and m.lng is not None]
    no_geo = [m for m in meldingen if m.lat is None or m.lng is None]
    clusters: list[list[Melding]] = []

    while remaining:
        seed = remaining.pop(0)
        cluster = [seed]
        new_remaining = []
        for m in remaining:
            if haversine_km(seed.lat, seed.lng, m.lat, m.lng) <= radius_km:
                cluster.append(m)
            else:
                new_remaining.append(m)
        remaining = new_remaining
        clusters.append(cluster)

    # Meldingen zonder GPS → één losse "geo-onbekend" cluster per gw_term
    if no_geo:
        clusters.append(no_geo)
    return clusters


def generate_clusters(
    db: Session,
    organization_id: str,
    *,
    radius_km: float = DEFAULT_CLUSTER_RADIUS_KM,
    min_cluster_size: int = 2,
    replace_existing: bool = True,
    modus: str = "dag",
    instellingen: Optional[dict] = None,
) -> dict:
    """Hoofdfunctie: genereer JobCluster-records voor open meldingen.

    Strategie:
    0. Trek de maatregel van elke melding gelijk aan zijn werksoort
    1. Pak alle open meldingen met `gw_term` gezet (CROW-classificatie compleet)
    2. Groepeer op gw_term
    3. Per groep: greedy geo-clustering binnen radius_km
    4. Cluster met >= min_cluster_size wordt een JobCluster
    5. Estimeer baseline + clustered uren via crow_kosten.estimate_cluster_hours

    Stap 0 zorgt dat de clusterindeling dezelfde is als de werksoort-indeling
    op de kaart: hotbox bij hotbox, machinaal bij machinaal, scheuren bij
    scheuren. Wie een melding in het portaal van werksoort verandert, ziet dat
    bij de eerstvolgende generatie terug zonder ergens anders iets te hoeven
    doen. Meldingen zonder werksoort ("nog in te delen") hebben geen gw_term en
    blijven bewust buiten de clusters — die moeten eerst ingedeeld worden.

    Modus "dag" (standaard): binnen een werksoort worden de meldingen over
    werkdagen verdeeld op basis van de dagproductie en de gemeten m²/m¹/plekken
    (zie _dagpakketten). `radius_km` is dan de maximale afstand binnen één dag.
    Modus "straal": het oude gedrag, alles binnen de straal is één cluster.
    Werksoorten zonder kengetal gaan altijd op straal: daarvan weten we niet
    hoeveel er in een dag past.

    Returns een summary-dict met counts en savings.
    """
    inst = instellingen or {"werkdag_uren": WERKDAG_UREN, "dagproductie": {}}
    if replace_existing:
        # Verwijder oude voorgestelde clusters van deze org (assigned blijft)
        old = (db.query(JobCluster)
                 .filter(JobCluster.organization_id == organization_id,
                         JobCluster.status == "proposed")
                 .all())
        for c in old:
            # Ontkoppel meldingen
            for m in db.query(Melding).filter(Melding.job_cluster_id == c.id).all():
                m.job_cluster_id = None
            db.delete(c)
        db.commit()

    # Actieve meldingen met maatregel — ook 'in_behandeling' meenemen
    # omdat die nog steeds geclusterd kunnen worden voor planning.
    # Alleen 'opgelost' / 'afgerond' zijn klaar en hebben geen cluster nodig.
    ACTIVE_STATUSES = ("open", "nieuw", "in_behandeling", "in_uitvoering",
                       "gereed_uitvoering")

    # Stap 0 — werksoort is leidend voor de maatregel. Zonder dit blijft een
    # melding die in het portaal van werksoort is veranderd op zijn oude
    # gw_term clusteren, en valt een melding die er net een heeft gekregen
    # helemaal buiten de boot.
    actief = (db.query(Melding)
                .filter(Melding.organization_id == organization_id,
                        Melding.status.in_(ACTIVE_STATUSES))
                .all())
    # Let op: geen any(...) met een generator — die stopt bij de eerste melding
    # die verandert en laat de rest ongesynchroniseerd achter.
    if [m for m in actief if werksoort_synchroniseer(m)]:
        db.commit()

    meldingen = [m for m in actief
                 if m.gw_term is not None and m.job_cluster_id is None]

    # Groep per gw_term. Elke werksoort heeft een eigen gw_term, dus de
    # groepen zijn precies de werksoorten die de uitvoerder op de kaart ziet.
    by_term: dict[str, list[Melding]] = {}
    for m in meldingen:
        by_term.setdefault(m.gw_term, []).append(m)

    created_clusters: list[JobCluster] = []
    total_baseline = 0.0
    total_clustered = 0.0

    for gw_term, group in by_term.items():
        skill = _skill_voor_groep(group)
        capaciteit = dagproductie_voor(skill, inst) if modus == "dag" else None
        eenheid = PRODUCTIVITY_PER_SKILL.get(skill or "", (0, None, 0))[1]
        per_melding = {m.id: _eenheden_melding(skill, m) for m in group}

        if capaciteit:
            groepen = _dagpakketten(group, {k: v[0] for k, v in per_melding.items()},
                                    capaciteit, radius_km)
        else:
            groepen = [(g, None) for g in _greedy_geo_clusters(group, radius_km=radius_km)]

        for cluster_meldingen, last in groepen:
            # Een losse melding is alleen een cluster als hij zelf een flink
            # stuk van een dag is; anders is het gewoon een melding.
            vol_genoeg = capaciteit and last is not None and last >= 0.5 * capaciteit
            if len(cluster_meldingen) < min_cluster_size and not vol_genoeg:
                continue

            units = sum(per_melding[m.id][0] for m in cluster_meldingen)
            gemeten = sum(per_melding[m.id][0] for m in cluster_meldingen if per_melding[m.id][1])
            clustered_h, baseline_h = estimate_cluster_hours(skill, units) if skill else (0, 0)

            lat_min, lat_max, lng_min, lng_max = geo_bounding(cluster_meldingen)
            label = label_for_bbox(lat_min, lat_max, lng_min, lng_max)

            jc = JobCluster(
                organization_id=organization_id,
                gw_term=gw_term,
                skill_code=skill,
                melding_count=len(cluster_meldingen),
                estimated_hours=clustered_h,
                productivity_baseline_hours=baseline_h,
                productivity_savings_hours=round(max(0.0, baseline_h - clustered_h), 1),
                eenheden=round(units, 1) if skill else None,
                eenheid=eenheid if skill else None,
                dagproductie=capaciteit,
                werkdagen=round(units / capaciteit, 2) if capaciteit else None,
                gemeten_aandeel=round(gemeten / units, 2) if units else None,
                geo_lat_min=lat_min, geo_lat_max=lat_max,
                geo_lng_min=lng_min, geo_lng_max=lng_max,
                geo_label=label,
                status="proposed",
            )
            db.add(jc)
            db.flush()  # nodig om jc.id beschikbaar te krijgen voor melding-koppeling

            for m in cluster_meldingen:
                m.job_cluster_id = jc.id

            created_clusters.append(jc)
            total_baseline += baseline_h
            total_clustered += clustered_h

    db.commit()

    return {
        "clusters_created": len(created_clusters),
        "meldingen_clustered": sum(c.melding_count for c in created_clusters),
        "total_baseline_hours": round(total_baseline, 1),
        "total_clustered_hours": round(total_clustered, 1),
        "total_savings_hours": round(total_baseline - total_clustered, 1),
        "savings_percentage": round(
            ((total_baseline - total_clustered) / total_baseline * 100) if total_baseline else 0,
            1,
        ),
    }


def _skill_voor_groep(group: list[Melding]) -> Optional[str]:
    """De skill die de meeste meldingen in deze groep vragen.

    Eerst op werksoort — die is expliciet vastgesteld. Valt de categorie
    daarbuiten, dan via de CROW-maatregel. Eerder werd alleen naar de eerste
    melding gekeken; bij een groep waarvan juist die ene melding geen
    maatregel had, kreeg het hele cluster geen skill en dus geen urenraming.
    """
    stemmen: dict[str, int] = {}
    for m in group:
        skill = (WERKSOORT_SKILL.get((m.category or "").strip())
                 or maatregel_to_skill(m.gw_maatregel))
        if skill:
            stemmen[skill] = stemmen.get(skill, 0) + 1
    if not stemmen:
        return None
    return max(stemmen.items(), key=lambda kv: kv[1])[0]


def _maat_uit_melding(m: Melding) -> dict:
    """Maatvoering die bij de melding is opgeslagen, of een leeg dict."""
    if not m.norm_data_json:
        return {}
    try:
        data = json.loads(m.norm_data_json)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _eenheden_melding(skill_code: Optional[str], m: Melding) -> tuple[float, bool]:
    """(eenheden, gemeten?) voor één melding.

    Gemeten als de schouwer de maat heeft ingevuld; anders het oude kengetal
    (een half uur werk per melding). Dat de maat geschat is, zeggen we er
    eerlijk bij: een dagpakket op geschatte m² is een voorstel, geen planning.
    """
    if not skill_code:
        return 0.0, False
    rate, eenheid, _setup = PRODUCTIVITY_PER_SKILL.get(skill_code, (0.0, "", 0.0))
    terugval = (0.5 / rate) if rate > 0 else 50.0
    veld = {"plek": "aantal_vlakken", "m¹": "lengte_m", "m²": "oppervlakte_m2"}.get(eenheid)
    gemeten = _maat_uit_melding(m).get(veld) if veld else None
    try:
        gemeten = float(gemeten) if gemeten is not None else 0.0
    except (TypeError, ValueError):
        gemeten = 0.0
    return (gemeten, True) if gemeten > 0 else (terugval, False)


def _estimate_units(skill_code: Optional[str], meldingen: list[Melding]) -> float:
    """Aantal eenheden (m¹/m²/voeg/plek) in een cluster.

    Waar de schouwer heeft gemeten rekenen we met die maat: het aantal vlakken
    voor plekwerk, de lengte voor scheuren, het oppervlak voor machinaal werk.
    Die maten staan in norm_data_json. Voor meldingen zonder maat valt de
    schatting terug op het oude kengetal (een halve werkuur per melding,
    teruggerekend via de productiviteit), zodat een half ingevulde dataset niet
    ineens een cluster van nul uur oplevert.
    """
    if not skill_code:
        return 0.0
    rate, eenheid, _setup = PRODUCTIVITY_PER_SKILL.get(skill_code, (0.0, "", 0.0))
    # Aanname: gemiddelde melding ~30 min ruwe arbeid → afgeleid uit rate
    terugval = (0.5 / rate) if rate > 0 else 50.0
    veld = {"plek": "aantal_vlakken", "m¹": "lengte_m", "m²": "oppervlakte_m2"}.get(eenheid)

    totaal = 0.0
    for m in meldingen:
        gemeten = _maat_uit_melding(m).get(veld) if veld else None
        try:
            gemeten = float(gemeten) if gemeten is not None else 0.0
        except (TypeError, ValueError):
            gemeten = 0.0
        totaal += gemeten if gemeten > 0 else terugval
    return totaal


# ════════════════════════════════════════════════════════════
# Cluster-utilities
# ════════════════════════════════════════════════════════════

def assign_cluster(
    db: Session,
    cluster_id: str,
    user_id: str,
) -> JobCluster:
    """Wijs cluster toe aan een gebruiker. Cascadeert assigned_to naar alle
    onderliggende meldingen.
    """
    jc = db.query(JobCluster).filter(JobCluster.id == cluster_id).first()
    if not jc:
        raise ValueError(f"Cluster {cluster_id} bestaat niet")
    jc.assigned_to = user_id
    jc.status = "assigned"
    # Cascadeer naar meldingen
    for m in db.query(Melding).filter(Melding.job_cluster_id == cluster_id).all():
        m.assigned_to = user_id
    db.commit()
    db.refresh(jc)
    return jc


def my_clusters(db: Session, user_id: str) -> list[JobCluster]:
    """Geef alle clusters van een gebruiker terug, gesorteerd op planned_date."""
    return (db.query(JobCluster)
              .filter(JobCluster.assigned_to == user_id,
                      JobCluster.status.in_(["assigned", "in_progress"]))
              .order_by(JobCluster.planned_date.asc().nullslast(), JobCluster.created_at.desc())
              .all())


def cluster_summary(jc: JobCluster, *, include_meldingen: bool = False, db: Optional[Session] = None) -> dict:
    """Serialiseer een cluster naar dict voor API.

    Met include_meldingen=True wordt de bijbehorende melding-lijst
    embedded in het response — voor mobile day-planner zodat alle
    context in één API-call beschikbaar is.
    """
    out = {
        "id": jc.id,
        "gw_term": jc.gw_term,
        "skill_code": jc.skill_code,
        "skill_name": SKILL_CODES.get(jc.skill_code or "", jc.skill_code),
        "melding_count": jc.melding_count,
        "estimated_hours": jc.estimated_hours,
        "baseline_hours": jc.productivity_baseline_hours,
        "savings_hours": jc.productivity_savings_hours,
        "savings_pct": (
            round((jc.productivity_savings_hours or 0) / jc.productivity_baseline_hours * 100, 1)
            if jc.productivity_baseline_hours else 0
        ),
        "geo_label": jc.geo_label,
        # Dagpakket
        "eenheden": jc.eenheden,
        "eenheid": jc.eenheid,
        "dagproductie": jc.dagproductie,
        "werkdagen": jc.werkdagen,
        "vulling_pct": (round(min(1.0, jc.werkdagen) * 100) if jc.werkdagen else None),
        "gemeten_aandeel": jc.gemeten_aandeel,
        "geo_bbox": {
            "lat_min": jc.geo_lat_min, "lat_max": jc.geo_lat_max,
            "lng_min": jc.geo_lng_min, "lng_max": jc.geo_lng_max,
        } if jc.geo_lat_min is not None else None,
        "geo_centroid": _geo_centroid(jc),
        "planned_date": jc.planned_date.isoformat() if jc.planned_date else None,
        "assigned_to": jc.assigned_to,
        "assignee_name": jc.assignee.email if jc.assignee else None,
        "status": jc.status,
        "created_at": jc.created_at.isoformat() if jc.created_at else None,
    }
    if include_meldingen and db is not None:
        meldingen = (db.query(Melding)
                       .filter(Melding.job_cluster_id == jc.id)
                       .order_by(Melding.priority.desc())
                       .all())
        # Greedy route-order: start vanuit cluster-centroid, dan steeds dichtstbijzijnde
        ordered = _greedy_route_order(meldingen, start_lat=out["geo_centroid"][0] if out["geo_centroid"] else None,
                                      start_lng=out["geo_centroid"][1] if out["geo_centroid"] else None)
        out["meldingen"] = [{
            "id": m.id,
            "title": m.title,
            "description": m.description,
            "lat": m.lat, "lng": m.lng,
            "priority": m.priority,
            "status": m.status,
            "crow_klasse": m.crow_klasse,
            "gw_term": m.gw_term,
            "gw_kosten_orde": m.gw_kosten_orde,
            "photo_url": m.photo_url,
            "asset_id": m.asset_id,
            "maps_url": (f"https://maps.google.com/?q={m.lat},{m.lng}" if m.lat and m.lng else None),
        } for m in ordered]
    return out


def _geo_centroid(jc: JobCluster) -> Optional[tuple[float, float]]:
    """Geef centroid (gemiddeld punt) van cluster-bbox."""
    if jc.geo_lat_min is None or jc.geo_lng_min is None:
        return None
    return (
        (jc.geo_lat_min + jc.geo_lat_max) / 2,
        (jc.geo_lng_min + jc.geo_lng_max) / 2,
    )


def _greedy_route_order(
    meldingen: list[Melding],
    start_lat: Optional[float] = None,
    start_lng: Optional[float] = None,
) -> list[Melding]:
    """Sorteer meldingen op nearest-neighbor (greedy TSP) vanaf start-punt.

    Meldingen zonder GPS belanden achteraan, in originele volgorde.
    """
    with_geo = [m for m in meldingen if m.lat is not None and m.lng is not None]
    no_geo = [m for m in meldingen if m.lat is None or m.lng is None]

    if not with_geo:
        return list(meldingen)

    if start_lat is None or start_lng is None:
        # Begin bij de eerste melding, dan greedy verder
        ordered = [with_geo.pop(0)]
        current = (ordered[0].lat, ordered[0].lng)
    else:
        ordered = []
        current = (start_lat, start_lng)

    while with_geo:
        nxt = min(with_geo, key=lambda m: haversine_km(current[0], current[1], m.lat, m.lng))
        ordered.append(nxt)
        current = (nxt.lat, nxt.lng)
        with_geo.remove(nxt)

    return ordered + no_geo
