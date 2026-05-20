"""NEN 3140 — Elektrische installaties laagspanning (openbare verlichting).

NEN 3140 is de Nederlandse norm voor onderhoud + inspectie van elektrische
installaties LS (<1000V AC). Voor openbare verlichting + verkeerslichten
zijn de meetwaarden bepalend:
  - Isolatieweerstand: >= 1 MΩ (1.0 MΩ minimum tussen fase-aarde)
  - Aardingsweerstand: <= 100 Ω (lokaal, lokaal sterk afhankelijk grond)
  - Aardlek-uitschakeltijd: <= 200 ms voor 30 mA aardlek

Frequentie:
  - Visuele inspectie: jaarlijks
  - Functionele controle: 3-jaarlijks
  - Volledige meting: 5-jaarlijks (bij ouderdom/verbouwing korter)

Bron: NEN 3140:2011 + supplement A2:2019 + NEN-EN 60204-1 (machineveiligheid).

Heuristische classificatie + grens-check op meetwaarden.
"""
from __future__ import annotations
from typing import Optional


# Defect-types met grens-check op meetwaarden
NEN_3140_DEFECTEN = [
    {
        "code": "isolatie_te_laag",
        "naam": "Isolatieweerstand onder grens (< 1 MΩ)",
        "ernst": "kritiek",
        "klasse_voorbeelden": ["acuut"],
        "grens_megaohm": 1.0,
        "maatregel": "Direct spanningloos maken + kabel-inspectie + meting na herstel",
        "uitleg": "Onder NEN 3140-norm; risico op kortsluiting en aanraakgevaar",
        "norm_grens": "≥ 1.0 MΩ (kabel) of ≥ 0.5 MΩ (incl. armatuur)",
    },
    {
        "code": "aarding_te_hoog",
        "naam": "Aardingsweerstand te hoog (> 100 Ω)",
        "ernst": "hoog",
        "klasse_voorbeelden": ["hoog"],
        "grens_ohm": 100,
        "maatregel": "Aardelektrode verlengen of vervangen; tweede pen bij hoge grond-weerstand",
        "uitleg": "Bij fout kan rasterspanning te hoog worden (stapspanning-risico)",
        "norm_grens": "≤ 100 Ω (lokaal) / ≤ 10 Ω voor TT-stelsel",
    },
    {
        "code": "aardlek_traag",
        "naam": "Aardlek-schakelaar reageert te traag",
        "ernst": "kritiek",
        "klasse_voorbeelden": ["acuut"],
        "grens_ms": 200,
        "maatregel": "Vervang ALS-30mA module; test bij volgende ronde opnieuw",
        "uitleg": "Uitschakeltijd >200ms bij 30mA = verhoogd elektrocutie-risico",
        "norm_grens": "≤ 200 ms @ 30 mA (NEN 1010-410.4)",
    },
    {
        "code": "aardlek_drempel",
        "naam": "Aardlek-stroom over drempelwaarde",
        "ernst": "hoog",
        "klasse_voorbeelden": ["hoog"],
        "grens_ma": 30,
        "maatregel": "Lek-stroom > 30mA — circuit afgaan op kabelfout / vocht-intrek",
        "uitleg": "30mA is de schakeldrempel; structureel boven = lekstroom-probleem",
        "norm_grens": "Schakelt < 30 mA",
    },
    {
        "code": "armatuur_defect",
        "naam": "Defecte armatuur — bekabeling zichtbaar",
        "ernst": "hoog",
        "klasse_voorbeelden": ["hoog", "middel"],
        "maatregel": "Armatuur vervangen of grondig herstellen; geen tijdelijke isolatie",
        "uitleg": "Open of beschadigde armatuur, kabel-uitloop zichtbaar",
        "kleur_hint": "metaal-glans + zwarte kabelmantel zichtbaar",
    },
    {
        "code": "kabel_corrosie",
        "naam": "Kabel-corrosie / mantelschade",
        "ernst": "middel",
        "klasse_voorbeelden": ["middel"],
        "maatregel": "Vernieuwen kabeltracé op aangetast segment + check op vocht-intrek",
        "uitleg": "Buitenmantel beschadigd, koperdraad mogelijk geoxideerd",
        "kleur_hint": "groene patina, zwarte vlek mantel",
    },
    {
        "code": "verbinding_los",
        "naam": "Loszittende klemverbinding in mast-voet",
        "ernst": "hoog",
        "klasse_voorbeelden": ["hoog"],
        "maatregel": "Vastdraaien op koppelmoment + thermische check 24u later",
        "uitleg": "Slechte klemcontacten leiden tot overhitting en branduitval",
        "kleur_hint": "donkere verkleuring rondom klem",
    },
    {
        "code": "deksel_open",
        "naam": "Openstaand of ontbrekend mast-deksel",
        "ernst": "kritiek",
        "klasse_voorbeelden": ["acuut"],
        "maatregel": "Direct dichten + nieuwe deksel + slot-controle",
        "uitleg": "Aanraakgevaar onder spanning; ook waterintrek-risico",
        "kleur_hint": "zichtbare zwarte holte in mastvoet",
    },
]


def check_grenswaarden(*,
                      isolatie_megaohm: Optional[float] = None,
                      aarding_ohm: Optional[float] = None,
                      aardlek_ms: Optional[float] = None,
                      aardlek_ma: Optional[float] = None,
                      ) -> list[dict]:
    """Strikte grens-check: returnt defecten waar gemeten waarden NEN 3140 overschrijden."""
    out = []
    if isolatie_megaohm is not None and isolatie_megaohm < 1.0:
        out.append({
            "code": "isolatie_te_laag",
            "value": isolatie_megaohm,
            "unit": "MΩ",
            "grens": 1.0,
            "type": "below",
        })
    if aarding_ohm is not None and aarding_ohm > 100:
        out.append({
            "code": "aarding_te_hoog",
            "value": aarding_ohm,
            "unit": "Ω",
            "grens": 100,
            "type": "above",
        })
    if aardlek_ms is not None and aardlek_ms > 200:
        out.append({
            "code": "aardlek_traag",
            "value": aardlek_ms,
            "unit": "ms",
            "grens": 200,
            "type": "above",
        })
    if aardlek_ma is not None and aardlek_ma > 30:
        out.append({
            "code": "aardlek_drempel",
            "value": aardlek_ma,
            "unit": "mA",
            "grens": 30,
            "type": "above",
        })
    return out


def klasse_advies(ernst: str) -> str:
    """Advies-tekst per ernst-klasse."""
    return {
        "kritiek": "ACUUT — spanningloos + herstellen vóór heropening",
        "hoog":    "Binnen 24-72 uur herstellen + tijdelijk afsluiten",
        "middel":  "Binnen 4 weken inplannen",
        "laag":    "Volgende routine-onderhoud",
    }.get(ernst, "—")


def classify_nen3140(*,
                    asset_type: Optional[str] = None,
                    properties_hint: Optional[str] = None,
                    isolatie_megaohm: Optional[float] = None,
                    aarding_ohm: Optional[float] = None,
                    aardlek_ms: Optional[float] = None,
                    aardlek_ma: Optional[float] = None,
                    brightness: Optional[float] = None,
                    dominant_color_rgb: Optional[tuple[int, int, int]] = None,
                    ) -> list[dict]:
    """Top-N defecten voor NEN 3140-inspectie.

    Combineert harde meet-grenzen (boven/onder norm) met heuristieken
    op foto-features. Defecten die meetwaarden schenden krijgen
    automatisch hoge confidence.
    """
    # Stap 1: harde grens-checks (krijgen 90%+ confidence)
    grens_breaches = check_grenswaarden(
        isolatie_megaohm=isolatie_megaohm,
        aarding_ohm=aarding_ohm,
        aardlek_ms=aardlek_ms,
        aardlek_ma=aardlek_ma,
    )
    grens_codes = {g["code"] for g in grens_breaches}

    suggestions = []
    for d in NEN_3140_DEFECTEN:
        code = d["code"]
        if code in grens_codes:
            # Harde norm-schending — top confidence
            breach = next(g for g in grens_breaches if g["code"] == code)
            confidence = 92
            reason = "meetwaarde " + str(breach["value"]) + " " + breach["unit"] + " (" + ("onder" if breach["type"] == "below" else "boven") + " norm " + str(breach["grens"]) + ")"
            suggestions.append({
                "code": code,
                "naam": d["naam"],
                "ernst": d["ernst"],
                "confidence": confidence,
                "confidence_pct": confidence,
                "klasse_advies": d["klasse_voorbeelden"][0],
                "klasse_voorbeelden": d["klasse_voorbeelden"],
                "maatregel": d["maatregel"],
                "uitleg": d["uitleg"],
                "norm_grens": d.get("norm_grens", ""),
                "reason": reason,
                "method": "grens-check",
            })
            continue

        # Stap 2: heuristieken voor visuele defecten
        confidence = 30
        reasons = ["nen-3140-taxonomy"]

        if dominant_color_rgb:
            r, g, b = dominant_color_rgb
            avg = (r + g + b) / 3
            if r > g + 25 and r > 100 and code in ("kabel_corrosie", "verbinding_los"):
                confidence += 18
                reasons.append("roest/oxidatie-tint")
            if avg < 60 and code in ("deksel_open", "armatuur_defect"):
                confidence += 15
                reasons.append("donkere holte zichtbaar")
            if g > r + 20 and g > 70 and code == "kabel_corrosie":
                confidence += 20
                reasons.append("groene patina (koper-oxidatie)")

        if brightness is not None and brightness < 70 and code == "deksel_open":
            confidence += 12
            reasons.append("donker gebied (mogelijk holte)")

        if asset_type:
            at = asset_type.lower()
            if "mast" in at and code in ("deksel_open", "verbinding_los"):
                confidence += 15
            elif "armatuur" in at and code in ("armatuur_defect", "kabel_corrosie"):
                confidence += 12
            elif "verkeerslicht" in at and code in ("aardlek_drempel", "kabel_corrosie"):
                confidence += 10

        confidence = min(85, max(20, confidence))  # cap heuristiek <90% (grens-check krijgt 92)
        suggestions.append({
            "code": code,
            "naam": d["naam"],
            "ernst": d["ernst"],
            "confidence": confidence,
            "confidence_pct": confidence,
            "klasse_advies": d["klasse_voorbeelden"][0],
            "klasse_voorbeelden": d["klasse_voorbeelden"],
            "maatregel": d["maatregel"],
            "uitleg": d["uitleg"],
            "norm_grens": d.get("norm_grens", ""),
            "reason": " + ".join(reasons),
            "method": "heuristiek",
        })

    suggestions.sort(key=lambda s: -s["confidence"])
    return suggestions[:5]
