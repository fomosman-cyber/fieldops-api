"""Predictive Maintenance v0 — regelgebaseerde risicoscore per asset.

Output: integer 0-100 + "rationale" — een lijstje feiten die de score onderbouwen.
Bewust regelgebaseerd in v0: een controllable, uitlegbare baseline. Als er
voldoende data is wordt dit later vervangen door een ML-model dat met dezelfde
features traint (asset.installed_at, expected_lifespan_years, condition_score,
recent_meldingen, severity-mix).

Drie factoren, gewogen naar 100:
- leeftijd-fractie van verwachte levensduur (max 35 punten)
- condition_score (1-5, lager = beter); 5 → 35 punten, 1 → 0 punten (max 35)
- meldingen-historie laatste 12 maanden (max 30 punten)

Compliance/transparantie: rationale is een list[str] met menselijke regels —
direct toonbaar in de UI naast de score.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from models import Asset, Melding


SCORE_VERSION = "v1.0"

W_AGE = 35
W_CONDITION = 35
W_MELDINGEN = 30


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
    """3+ meldingen = max; bij meerdere kritiek/hoog harder ophogen."""
    base = min(W_MELDINGEN, total * 8)              # 0/1/2/3+ → 0/8/16/24, capped 30
    bonus = min(W_MELDINGEN - base, hoog_kritiek * 6)  # extra punten voor severe
    return int(min(W_MELDINGEN, base + bonus))


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

    # Meldingen
    one_year_ago = now - timedelta(days=365)
    total_m, severe_m = _melding_count_recent(db, asset.id, one_year_ago)
    mel_pts = _melding_points(total_m, severe_m)
    if total_m == 0:
        rationale.append("Geen meldingen in afgelopen 12 maanden — meldingfactor 0.")
    else:
        sev = f", waarvan {severe_m} hoog/kritiek" if severe_m else ""
        rationale.append(f"{total_m} meldingen in afgelopen 12 mnd{sev} — +{mel_pts} pt.")

    score = age_pts + cond_pts + mel_pts
    score = max(0, min(100, score))

    if score >= 70:
        band = "hoog"
        recommendation = "Plan inspectie of preventief onderhoud binnen 4 weken."
    elif score >= 40:
        band = "matig"
        recommendation = "Overweeg inspectie binnen 3 maanden."
    else:
        band = "laag"
        recommendation = "Geen acute actie nodig; volg reguliere onderhoudscyclus."

    return {
        "asset_id": asset.id,
        "asset_code": asset.code,
        "asset_type": asset.asset_type,
        "score": score,
        "band": band,
        "components": {
            "age": age_pts,
            "condition": cond_pts,
            "meldingen": mel_pts,
        },
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
