"""NWB Wegvakken — proxy naar PDOK WFS service.

NWB = Nationaal Wegen Bestand, beheerd door Rijkswaterstaat.
Officiële NL-database van alle wegvakken met stabiele WVK_ID's.

Endpoint: https://service.pdok.nl/rws/nwbwegen/wfs/v1_0
Layer: nwbwegen:wegvakken
Output: GeoJSON (RD-coords standard, we converteren naar WGS84)

CQL-filters:
  STT_NAAM='Verdilaan'         (straatnaam, exact)
  STT_NAAM ILIKE 'Verd%'        (straatnaam, like)
  GME_NAAM='Westland'           (gemeente, exact)
  WVK_ID='12345'                (wegvak-id)

Wegvak-attributen:
  WVK_ID         — uniek wegvak-ID (nationaal stabiel)
  WVK_BEGDAT     — begin-datum van deze wegvak-versie
  JTE_ID_BEG     — junction-id begin
  JTE_ID_END     — junction-id einde
  STT_NAAM       — straatnaam
  GME_NAAM       — gemeente
  WEGNUMMER      — A12 / N201 / etc (alleen rijks- en provinciale wegen)
  HNRSTRLNKS/RCHTS — huisnummer-bereik links/rechts
"""

from __future__ import annotations
from datetime import datetime
from typing import Any, Optional

import httpx

# PDOK NWB Wegen — open data, geen API-key nodig
WFS_BASE = "https://service.pdok.nl/rws/nwbwegen/wfs/v1_0"
LAYER_NAME = "wegvakken"   # of "nwbwegen:wegvakken" — wfs accepteert beide
DEFAULT_TIMEOUT = 20


def _wfs_request(ogc_filter_xml: str, max_features: int = 100, srs: str = "EPSG:4326") -> dict:
    """Doe een GetFeature WFS-call op de NWB-wegvakken layer.

    PDOK NWB respecteert OGC XML <Filter>-syntaxis maar negeert
    CQL_FILTER. We bouwen een OGC-Filter-string en geven die mee als
    `filter` parameter.

    Returns parsed GeoJSON FeatureCollection. Raises bij HTTP/4xx/5xx.
    """
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": LAYER_NAME,
        "outputFormat": "application/json",
        "srsName": srs,
        "count": max_features,
        "filter": ogc_filter_xml,
    }
    with httpx.Client(timeout=DEFAULT_TIMEOUT) as cli:
        r = cli.get(WFS_BASE, params=params)
        r.raise_for_status()
        return r.json()


# OGC Filter XML-helpers
def _xml_escape(value: str) -> str:
    """Escape XML special chars in een filter-waarde."""
    return (str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def _eq_filter(prop: str, val: str) -> str:
    """Bouw <PropertyIsEqualTo> XML — case-sensitive exact match."""
    return (
        f'<PropertyIsEqualTo>'
        f'<PropertyName>{prop}</PropertyName>'
        f'<Literal>{_xml_escape(val)}</Literal>'
        f'</PropertyIsEqualTo>'
    )


def _like_filter(prop: str, val: str, *, match_case: bool = False) -> str:
    """Bouw <PropertyIsLike> — case-insensitive default. Wildcard '*' werkt
    in `val` (bv. 'verdi*' matcht 'Verdilaan' en 'Verdilaanstraat')."""
    case = "true" if match_case else "false"
    return (
        f'<PropertyIsLike wildCard="*" singleChar="?" escapeChar="!" matchCase="{case}">'
        f'<PropertyName>{prop}</PropertyName>'
        f'<Literal>{_xml_escape(val)}</Literal>'
        f'</PropertyIsLike>'
    )


def _and_filter(*subs: str) -> str:
    """Wrap filters in <And>."""
    return '<And>' + ''.join(subs) + '</And>'


def _filter_doc(inner: str) -> str:
    """Wrap in root <Filter> element met OGC namespace."""
    return f'<Filter xmlns="http://www.opengis.net/ogc">{inner}</Filter>'


def search_by_street_and_gemeente(straatnaam: str, gemeente: str,
                                  *, max_features: int = 50) -> list[dict]:
    """Zoek wegvakken op straatnaam + gemeente.

    Case-insensitive exact-match. Als 0 resultaten: probeer trailing wildcard
    (bv. 'verdi' matcht ook 'Verdilaanstraat'). NWB-data heeft Title-Case
    schrijfwijze, dus user input mag in om-het-even-welke case zijn.
    """
    straat = straatnaam.strip()
    gem = gemeente.strip()
    # Pass 1: exact (case-insensitive)
    f = _filter_doc(_and_filter(
        _like_filter("sttNaam", straat),
        _like_filter("gmeNaam", gem),
    ))
    data = _wfs_request(f, max_features=max_features)
    feats = data.get("features", [])
    if feats:
        return [_simplify_feature(feat) for feat in feats]
    # Pass 2: trailing wildcard fallback (alleen als geen wildcard al in input)
    if "*" not in straat:
        f2 = _filter_doc(_and_filter(
            _like_filter("sttNaam", straat + "*"),
            _like_filter("gmeNaam", gem),
        ))
        data2 = _wfs_request(f2, max_features=max_features)
        return [_simplify_feature(feat) for feat in data2.get("features", [])]
    return []


def search_by_street_anywhere(straatnaam: str, *, max_features: int = 50) -> list[dict]:
    """Zoek wegvakken op straatnaam, in heel NL (geen gemeente-filter).

    Case-insensitive met wildcard-fallback — zie `search_by_street_and_gemeente`.
    """
    straat = straatnaam.strip()
    f = _filter_doc(_like_filter("sttNaam", straat))
    data = _wfs_request(f, max_features=max_features)
    feats = data.get("features", [])
    if feats:
        return [_simplify_feature(feat) for feat in feats]
    if "*" not in straat:
        f2 = _filter_doc(_like_filter("sttNaam", straat + "*"))
        data2 = _wfs_request(f2, max_features=max_features)
        return [_simplify_feature(feat) for feat in data2.get("features", [])]
    return []


def get_wegvak_by_id(wvk_id: str) -> Optional[dict]:
    """Haal één specifiek wegvak op via WVK_ID — case-sensitive exact match."""
    f = _filter_doc(_eq_filter("wvkId", str(wvk_id)))
    data = _wfs_request(f, max_features=1)
    feats = data.get("features", [])
    return _simplify_feature(feats[0]) if feats else None


def _simplify_feature(feature: dict) -> dict:
    """Vereenvoudig een NWB GeoJSON Feature naar wat de frontend nodig heeft.

    PDOK NWB gebruikt camelCase: wvkId, sttNaam, gmeNaam, wvkBegdat,
    jteIdBeg, jteIdEnd, hnrstrlnks, hnrstrrhts. We accepteren ook lowercase
    fallbacks voor robuustheid.
    """
    p = feature.get("properties", {}) or {}
    geom = feature.get("geometry") or {}

    def _get(*keys, default=None):
        for k in keys:
            if k in p:
                return p[k]
            # Probeer lowercase + uppercase variants
            for variant in (k.lower(), k.upper()):
                if variant in p:
                    return p[variant]
        return default

    # WVK_ID kan int of float zijn — normalize naar string zonder decimalen
    wvk_raw = _get("wvkId", "wvk_id")
    wvk_id = ""
    if wvk_raw is not None:
        try:
            wvk_id = str(int(float(wvk_raw)))
        except (ValueError, TypeError):
            wvk_id = str(wvk_raw)

    def _id_str(val):
        if val is None:
            return None
        try:
            return str(int(float(val)))
        except (ValueError, TypeError):
            return str(val) or None

    wvk_begdat_raw = _get("wvkBegdat", "wvk_begdat")
    wvk_begdat = None
    if wvk_begdat_raw and isinstance(wvk_begdat_raw, str):
        wvk_begdat = wvk_begdat_raw[:10]

    return {
        "wvk_id":      wvk_id,
        "wvk_begdat":  wvk_begdat,
        "jte_id_beg":  _id_str(_get("jteIdBeg", "jte_id_beg")),
        "jte_id_end":  _id_str(_get("jteIdEnd", "jte_id_end")),
        "straatnaam":  _get("sttNaam", "stt_naam"),
        "gemeente":    _get("gmeNaam", "gme_naam"),
        "wegnummer":   (_get("wegnummer") or "") or None,
        "wegtype":     _get("wegtype"),
        "wgtype_oms":  _get("wgtypeOms"),
        "geometry":    geom,
        "length_m":    _calc_length_meters(geom),
        "huisnr_links":  _id_str(_get("hnrstrlnks")),
        "huisnr_rechts": _id_str(_get("hnrstrrhts", "hnrstrrchts")),
    }


def _calc_length_meters(geom: dict) -> Optional[float]:
    """Schat lengte van LineString in meters via haversine.

    Werkt voor LineString in WGS84-coords. MultiLineString: sommatie van
    sub-lengths. Geen meter-precieze geodesic-calc — voldoende voor UI.
    """
    if not geom or not isinstance(geom, dict):
        return None
    typ = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return None
    try:
        from math import radians, sin, cos, sqrt, atan2
        R = 6371000.0

        def hav(p1, p2):
            lng1, lat1 = p1[0], p1[1]
            lng2, lat2 = p2[0], p2[1]
            dlat = radians(lat2 - lat1)
            dlng = radians(lng2 - lng1)
            a = (sin(dlat / 2) ** 2 +
                 cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2)
            return 2 * R * atan2(sqrt(a), sqrt(1 - a))

        def line_length(line_coords):
            tot = 0.0
            for i in range(len(line_coords) - 1):
                tot += hav(line_coords[i], line_coords[i + 1])
            return tot

        if typ == "LineString":
            return round(line_length(coords), 1)
        elif typ == "MultiLineString":
            return round(sum(line_length(seg) for seg in coords), 1)
    except Exception:
        return None
    return None


def parse_begdat(raw: Optional[str]) -> Optional[datetime]:
    """Parse NWB WVK_BEGDAT string naar datetime."""
    if not raw:
        return None
    try:
        # YYYY-MM-DD format
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
