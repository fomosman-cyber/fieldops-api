"""Geometrie-helpers voor wegvak split + merge (fase 4 van NWB-Wegvakken).

Werkt op LineString-coords in `[lng, lat]` notatie (GeoJSON-conventie).
Gebruikt euclidische projectie/afstand — voldoende precisie voor splits-punten
in NL (afwijking <1m bij <10km segmenten). Geen PostGIS-dependency.

Publieke functies:
  - haversine_meters(p1, p2)
  - linestring_length_m(coords)
  - split_linestring_at_points(coords, split_points) → list[list[coord]]
  - merge_linestrings(linestrings) → list[coord]
"""

from __future__ import annotations

from math import radians, sin, cos, sqrt, atan2
from typing import List, Sequence, Tuple


Coord = Sequence[float]   # [lng, lat]
Line  = List[List[float]]  # list of [lng, lat]


# ─────────────────────────────────────────────────────────────────────
# Afstanden
# ─────────────────────────────────────────────────────────────────────

_EARTH_R = 6_371_000.0


def haversine_meters(p1: Coord, p2: Coord) -> float:
    """Geodesic afstand in meters tussen twee [lng, lat]-punten."""
    lng1, lat1 = p1[0], p1[1]
    lng2, lat2 = p2[0], p2[1]
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = (sin(dlat / 2) ** 2 +
         cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2)
    return 2 * _EARTH_R * atan2(sqrt(a), sqrt(1 - a))


def linestring_length_m(coords: Sequence[Coord]) -> float:
    """Totale lengte van een LineString in meters."""
    if not coords or len(coords) < 2:
        return 0.0
    total = 0.0
    for i in range(len(coords) - 1):
        total += haversine_meters(coords[i], coords[i + 1])
    return total


# ─────────────────────────────────────────────────────────────────────
# Projectie + split
# ─────────────────────────────────────────────────────────────────────

def _project_on_segment(p: Coord, a: Coord, b: Coord) -> Tuple[List[float], float, float]:
    """Project punt `p` op segment a→b in lng/lat-vlak.

    Returns (closest_point [lng,lat], t, dist_squared) waarbij t∈[0,1] de
    fractionele positie op het segment is (0=a, 1=b). dist_squared is in
    coord-units (graden²) — alleen voor onderlinge vergelijking.
    """
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    px, py = p[0], p[1]

    abx, aby = bx - ax, by - ay
    ab_len_sq = abx * abx + aby * aby
    if ab_len_sq == 0:
        # Punt-segment (a==b)
        dx, dy = px - ax, py - ay
        return [ax, ay], 0.0, dx * dx + dy * dy

    t = ((px - ax) * abx + (py - ay) * aby) / ab_len_sq
    t = max(0.0, min(1.0, t))
    proj_x = ax + t * abx
    proj_y = ay + t * aby
    dx, dy = px - proj_x, py - proj_y
    return [proj_x, proj_y], t, dx * dx + dy * dy


def _project_on_linestring(p: Coord, coords: Sequence[Coord]) -> dict:
    """Vind beste projectie van `p` op de hele polyline.

    Returns dict met: segment_index (i), t, projected ([lng,lat]),
    dist_along_m (gecumuleerde afstand vanaf startpunt tot projectie).
    """
    best = None
    cum_dist = 0.0
    cum_dist_at_best = 0.0

    for i in range(len(coords) - 1):
        proj, t, dsq = _project_on_segment(p, coords[i], coords[i + 1])
        seg_len = haversine_meters(coords[i], coords[i + 1])
        if best is None or dsq < best["dist_sq"]:
            best = {
                "segment_index": i,
                "t": t,
                "projected": proj,
                "dist_sq": dsq,
                "dist_along_m": cum_dist + seg_len * t,
            }
            cum_dist_at_best = cum_dist
        cum_dist += seg_len

    if best is None:
        raise ValueError("LineString moet minstens 2 punten hebben")
    return best


def split_linestring_at_points(coords: Sequence[Coord],
                               split_points: Sequence[Coord]) -> List[Line]:
    """Splits een LineString op één of meer punten.

    Elk split-punt wordt geprojecteerd op de polyline; de polyline wordt
    geknipt op de projectie-punten in volgorde van afstand-langs-de-lijn.
    Resultaat: N+1 LineStrings (waar N = aantal valid split-points).

    Args:
      coords: list van [lng, lat] (LineString)
      split_points: list van [lng, lat] — minstens 1

    Raises ValueError als coords te kort is, of geen split-points.
    """
    if not coords or len(coords) < 2:
        raise ValueError("LineString heeft minimaal 2 punten nodig")
    if not split_points:
        raise ValueError("Geef minimaal 1 split-point op")

    # Project alle split-points; sorteer op afstand-langs-de-lijn
    projections = sorted(
        (_project_on_linestring(sp, coords) for sp in split_points),
        key=lambda x: x["dist_along_m"],
    )

    # Filter projecties die op exact dezelfde plek zitten (binnen 0.5m)
    # of die op het start/eind-punt vallen (zinloos om te splitsen)
    total_len = linestring_length_m(coords)
    filtered: List[dict] = []
    for p in projections:
        if p["dist_along_m"] < 0.5:
            continue  # Skip splits aan het begin
        if total_len - p["dist_along_m"] < 0.5:
            continue  # Skip splits aan het eind
        if filtered and abs(filtered[-1]["dist_along_m"] - p["dist_along_m"]) < 0.5:
            continue  # Skip duplicates
        filtered.append(p)

    if not filtered:
        raise ValueError("Geen valide splits-punten (buiten begin/eind van wegvak)")

    # Knip de polyline. Werk vooruit door coords + filtered projecties.
    pieces: List[Line] = []
    current: Line = [list(coords[0])]
    proj_idx = 0

    for i in range(len(coords) - 1):
        # Voeg eventuele projecties op dit segment toe (in volgorde)
        while proj_idx < len(filtered) and filtered[proj_idx]["segment_index"] == i:
            proj = filtered[proj_idx]
            current.append(list(proj["projected"]))
            pieces.append(current)
            current = [list(proj["projected"])]
            proj_idx += 1
        current.append(list(coords[i + 1]))

    pieces.append(current)
    return pieces


# ─────────────────────────────────────────────────────────────────────
# Merge
# ─────────────────────────────────────────────────────────────────────

_MERGE_TOL_M = 5.0   # tolerantie voor "matching endpoints" — 5m is gul genoeg


def _coords_close_m(a: Coord, b: Coord, tol_m: float = _MERGE_TOL_M) -> bool:
    return haversine_meters(a, b) <= tol_m


def merge_linestrings(linestrings: Sequence[Sequence[Coord]],
                      tol_m: float = _MERGE_TOL_M) -> Line:
    """Merge een lijst LineStrings tot één.

    Vereist: aaneengesloten — eind van A koppelt aan begin (of eind) van B.
    Doet greedy chaining: start met de eerste LineString en hangt opvolgers
    eraan, met flip indien nodig. Matching is op euclidische afstand binnen
    `tol_m` meter (default 5m — toleranter dan strikte gelijkheid omdat
    NWB-coords kunnen afwijken op junction-punten).

    Raises ValueError als ze niet aaneengesloten zijn.
    """
    if not linestrings:
        raise ValueError("Geef minimaal 1 LineString op")
    if len(linestrings) == 1:
        return [list(p) for p in linestrings[0]]

    # Start met de eerste; markeer rest als "te koppelen"
    chain: Line = [list(p) for p in linestrings[0]]
    remaining: List[Line] = [[list(p) for p in ls] for ls in linestrings[1:]]

    while remaining:
        head = chain[0]
        tail = chain[-1]

        # Probeer een wegvak te vinden dat aansluit op tail of head
        attached = False
        for idx, ls in enumerate(remaining):
            ls_first = ls[0]
            ls_last = ls[-1]

            if _coords_close_m(tail, ls_first, tol_m):
                chain.extend(ls[1:])
                remaining.pop(idx); attached = True; break
            if _coords_close_m(tail, ls_last, tol_m):
                chain.extend(reversed(ls[:-1]))
                remaining.pop(idx); attached = True; break
            if _coords_close_m(head, ls_last, tol_m):
                chain = list(ls) + chain[1:]
                remaining.pop(idx); attached = True; break
            if _coords_close_m(head, ls_first, tol_m):
                chain = list(reversed(ls)) + chain[1:]
                remaining.pop(idx); attached = True; break

        if not attached:
            raise ValueError(
                f"Wegvakken niet aaneengesloten — {len(remaining)} stuk(ken) "
                f"konden niet gekoppeld worden binnen {tol_m}m tolerantie"
            )

    return chain
