"""Narratieve tekstgeneratie voor het MJOP-rapport (PDF).

De MJOP-PDF had al uitgebreide uitleg (methodologie, voorbeeld-berekening,
bronnen). Deze module voegt de ontbrekende kop toe: een inleiding/doel en een
managementsamenvatting in proza — de 'so what' die een directeur/beheerder als
eerste leest. Pure functies, los te unit-testen.
"""
from __future__ import annotations


def _eur(bedrag) -> str:
    """EUR met Nederlandse duizendscheiding (12345 -> '12.345')."""
    try:
        return f"{float(bedrag):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def inleiding(*, scope_naam, years, assets_in_scope, include_score_2) -> str:
    preventief = (" Preventieve maatregelen (conditiescore 2) zijn in deze raming meegenomen."
                  if include_score_2 else
                  " Alleen direct te plannen maatregelen (conditiescore 3 en hoger) zijn meegenomen.")
    return (
        f"Dit Meerjaren Onderhoudsplan (MJOP) geeft voor {scope_naam} een onderbouwde "
        f"meerjarenraming van het benodigde onderhoud over een horizon van {years} jaar. "
        f"Het plan is opgesteld op basis van de geregistreerde conditie van {assets_in_scope} "
        f"assets en norm-conforme onderhoudscycli (NEN 2767-2, CROW 134/145/146, NEN 3140, "
        f"NEN-EN 1176). Doel is het onderbouwen van de onderhoudsbegroting en het inzichtelijk "
        f"maken van piekjaren en vervangingsmomenten, zodat de beheerder tijdig kan reserveren "
        f"en plannen.{preventief}"
    )


def managementsamenvatting(*, total_min, total_max, years, mjop_regels, assets_count,
                           assets_zonder_score, prioriteit_count, piekjaar,
                           piekjaar_max) -> str:
    zin = (f"Over de gehele horizon van {years} jaar bedragen de geraamde onderhoudskosten "
           f"EUR {_eur(total_min)} tot EUR {_eur(total_max)}, verdeeld over {mjop_regels} "
           f"maatregel-regels op {assets_count} assets.")
    if prioriteit_count:
        zin += (f" Hiervan vragen {prioriteit_count} assets prioriteit: zij verkeren in een "
                f"matige tot zeer slechte staat (conditiescore 4 of hoger).")
    else:
        zin += " Er zijn geen assets in een matige of slechtere staat aangetroffen."
    if piekjaar is not None:
        zin += (f" De hoogste kostenpiek valt in jaar {piekjaar} (tot EUR {_eur(piekjaar_max)}); "
                f"het verdient aanbeveling daar tijdig budget voor te reserveren.")
    if assets_zonder_score:
        zin += (f" Let op: {assets_zonder_score} assets hebben nog geen conditie-score en zijn "
                f"niet in deze raming meegenomen — een inspectie is nodig om het plan compleet "
                f"te maken.")
    zin += (" De gedetailleerde onderbouwing per asset, per jaar en per maatregel volgt in de "
            "hoofdstukken hierna.")
    return zin
