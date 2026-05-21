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
from math import radians, sin, cos, sqrt, atan2
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from models import Melding, User, UserSkill, JobCluster
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
) -> dict:
    """Hoofdfunctie: genereer JobCluster-records voor open meldingen.

    Strategie:
    1. Pak alle open meldingen met `gw_term` gezet (CROW-classificatie compleet)
    2. Groepeer op gw_term
    3. Per groep: greedy geo-clustering binnen radius_km
    4. Cluster met >= min_cluster_size wordt een JobCluster
    5. Estimeer baseline + clustered uren via crow_kosten.estimate_cluster_hours

    Returns een summary-dict met counts en savings.
    """
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
    meldingen = (db.query(Melding)
                   .filter(Melding.organization_id == organization_id,
                           Melding.status.in_(ACTIVE_STATUSES),
                           Melding.gw_term.isnot(None),
                           Melding.job_cluster_id.is_(None))
                   .all())

    # Groep per gw_term
    by_term: dict[str, list[Melding]] = {}
    for m in meldingen:
        by_term.setdefault(m.gw_term, []).append(m)

    created_clusters: list[JobCluster] = []
    total_baseline = 0.0
    total_clustered = 0.0

    for gw_term, group in by_term.items():
        skill = maatregel_to_skill(group[0].gw_maatregel)
        for cluster_meldingen in _greedy_geo_clusters(group, radius_km=radius_km):
            if len(cluster_meldingen) < min_cluster_size:
                continue

            # Schat units (proxy: 50 m¹ per scheurvulling-melding, 30 m² per
            # oppervlakte-behandeling, etc — heuristiek tot we echte units hebben)
            units = _estimate_units(skill, len(cluster_meldingen))
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


def _estimate_units(skill_code: Optional[str], melding_count: int) -> float:
    """Heuristisch aantal eenheden (m¹/m²/voeg/plek) bij een N meldingen.

    Tot we echte 'omvang in eenheden' op melding-niveau hebben, gebruiken we
    een conservatieve schatting per skill — voldoende voor productivity-calc.
    """
    if not skill_code:
        return 0.0
    rate, eenheid, _setup = PRODUCTIVITY_PER_SKILL.get(skill_code, (0.0, "", 0.0))
    # Aanname: gemiddelde melding ~30 min ruwe arbeid → afgeleid uit rate
    avg_units_per_melding = (0.5 / rate) if rate > 0 else 50.0
    return avg_units_per_melding * melding_count


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
