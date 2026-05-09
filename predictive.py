"""Predictive Maintenance v2 — regelgebaseerde risicoscore per asset, met CROW.

Output: integer 0-100 + "rationale" — een lijstje feiten die de score onderbouwen.
Bewust regelgebaseerd in v0: een controllable, uitlegbare baseline.

Vier factoren in v2.0-crow, gewogen naar 100:
- leeftijd-fractie van verwachte levensduur (max 25 punten)
- NEN-conditiescore 1-5 (max 25 punten)
- ergste CROW-klasse (L1..E3) op recente meldingen (max 30 punten)
- meldingen-historie laatste 12 maanden (max 20 punten)

Compliance/transparantie: rationale is een list[str] met menselijke regels —
direct toonbaar in de UI naast de score.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from models import Asset, Melding
from crow_kosten import klasse_to_risk_points, KLASSE_RISK_POINTS


SCORE_VERSION = "v2.0-crow"

# Wegingen 100 totaal
W_AGE = 25
W_CONDITION = 25
W_CROW = 30
W_MELDINGEN = 20


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
    if not klassen:
        return None
    # Sorteer op risk-points (hoogst eerst)
    valid = [k[0] for k in klassen if k[0] in KLASSE_RISK_POINTS]
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


def compute_asset_risk(db: Session, asset: Asset) -> dict:
    """Bereken risicoscore + uitleg. Werkt op één asset."""
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
    total_m, severe_m = _melding_count_recent(db, asset.id, one_year_ago)
    mel_pts = _melding_points(total_m, severe_m)
    if total_m == 0:
        rationale.append("Geen meldingen in afgelopen 12 maanden — meldingfactor 0.")
    else:
        sev = f", waarvan {severe_m} hoog/kritiek" if severe_m else ""
        rationale.append(f"{total_m} meldingen in afgelopen 12 mnd{sev} — +{mel_pts} pt.")

    score = age_pts + cond_pts + crow_pts + mel_pts
    score = max(0, min(100, score))

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

    return {
        "asset_id": asset.id,
        "asset_code": asset.code,
        "asset_type": asset.asset_type,
        "score": score,
        "band": band,
        "components": {
            "age": age_pts,
            "condition": cond_pts,
            "crow": crow_pts,
            "meldingen": mel_pts,
        },
        "worst_crow_klasse": worst_klasse,
        "rationale": rationale,
        "recommendation": recommendation,
        "score_version": SCORE_VERSION,
        "computed_at": now.isoformat(),
    }


def list_at_risk(db: Session, organization_id: str, *,
                 min_score: int = 60, asset_type: Optional[str] = None,
                 limit: int = 100) -> list[dict]:
    """Geef assets terug met risk_score >= min_score. Berekent on-demand."""
    q = db.query(Asset).filter(
        Asset.organization_id == organization_id,
        Asset.archived_at.is_(None),
    )
    if asset_type:
        q = q.filter(Asset.asset_type == asset_type)
    assets = q.all()

    results = [compute_asset_risk(db, a) for a in assets]
    results = [r for r in results if r["score"] >= min_score]
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]
