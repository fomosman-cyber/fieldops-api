"""Controleert of productie de laatste commit van main ook echt draait.

Op 9 november 2026 stond de live service nog op de commit van 24 september: elf
commits en zeven weken achter, zonder dat iemand het merkte. Een merge die
groen is, is niet hetzelfde als een merge die draait, en dat verschil was tot nu
toe alleen te zien door met de hand `/api/version` op te vragen en de hash te
herkennen. Dat is precies het soort controle dat je overslaat zodra het druk is.

Gebruik:

    python scripts/deploy_drift.py
    python scripts/deploy_drift.py --url https://portaal.fieldopsapp.nl --respijt 2

Afloopcodes:

    0  productie draait de verwachte commit, of mag nog uitrollen (respijt)
    1  productie loopt achter
    2  niet vast te stellen: endpoint onbereikbaar, of productie meldt geen commit

Twee is bewust geen een. "Ik kon het niet zien" is iets anders dan "het klopt
niet"; als die twee op een hoop gaan, ga je bij elke netwerkstoring in Render
naar een uitrolprobleem zoeken dat er niet is.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

BASIS_STANDAARD = "https://portaal.fieldopsapp.nl"
RESPIJT_STANDAARD = 2.0
TIJDSLIMIET = 15.0


class Onbereikbaar(RuntimeError):
    """Het endpoint gaf geen leesbaar antwoord — dat is code 2, geen drift."""


@dataclass
class Uitkomst:
    """Wat de controle vond. Een object, zodat de tests de regels kunnen nalezen."""

    code: int
    regels: list[str] = field(default_factory=list)


def zelfde_commit(een: str | None, ander: str | None) -> bool:
    """Vergelijkt hashes van verschillende lengte.

    `/api/version` meldt twaalf tekens, `git rev-parse` veertig. Vergelijken op
    de kortste van de twee houdt dat bij elkaar. Onder zeven tekens stopt het:
    korter dan dat accepteert git zelf een afkorting ook niet, en dan vergelijk
    je niets meer.
    """
    if not een or not ander:
        return False
    kort = min(len(een), len(ander))
    if kort < 7:
        return False
    return een[:kort].lower() == ander[:kort].lower()


def respijt_uit_omgeving(rauw: str | None, standaard: float = RESPIJT_STANDAARD) -> float:
    """DRIFT_RESPIJT_UUR lezen zonder de controle te laten omvallen op een typefout.

    Een onleesbare waarde mag de controle niet uitschakelen: dan verdwijnt juist
    het signaal waar het hier om gaat.
    """
    if rauw is None or not rauw.strip():
        return standaard
    try:
        waarde = float(rauw.strip().replace(",", "."))
    except ValueError:
        print(f"[drift] DRIFT_RESPIJT_UUR={rauw!r} is geen getal; ik houd {standaard:g} uur aan")
        return standaard
    return max(0.0, waarde)


def beoordeel(
    verwacht: str,
    gemeld: str | None,
    *,
    ouderdom_uur: float | None = None,
    respijt_uur: float = RESPIJT_STANDAARD,
    achterstand: int | None = None,
    gemeld_datum: str | None = None,
    gestart_op: str | None = None,
) -> Uitkomst:
    """Weegt de gemelde commit tegen de verwachte. Geen netwerk, geen git: puur rekenen."""
    if not gemeld:
        return Uitkomst(2, [
            "Productie meldt geen commit.",
            "RENDER_GIT_COMMIT staat niet op de service; zonder die variabele is een deploy niet te controleren.",
        ])

    if zelfde_commit(verwacht, gemeld):
        regels = [f"Productie draait {gemeld[:12]}: dat is de verwachte commit."]
        if gestart_op:
            regels.append(f"Deze instantie loopt sinds {gestart_op}.")
        return Uitkomst(0, regels)

    if ouderdom_uur is not None and ouderdom_uur < respijt_uur:
        return Uitkomst(0, [
            f"Productie draait nog {gemeld[:12]}, verwacht is {verwacht[:12]}.",
            f"Die verwachte commit is pas {ouderdom_uur:.1f} uur oud, binnen het respijt van {respijt_uur:g} uur: "
            "de uitrol mag nog onderweg zijn.",
        ])

    meervoud = "s" if achterstand != 1 else ""
    regels = [f"Productie loopt achter: {gemeld[:12]} draait, {verwacht[:12]} hoort te draaien."]
    if achterstand is None:
        regels.append(
            "De draaiende commit zit niet in de historie van deze checkout: een andere branch, "
            "of een commit die hier nooit is geweest."
        )
    elif achterstand == 0:
        regels.append(
            "De draaiende commit ligt niet achter de verwachte: productie draait iets anders, "
            "geen oudere versie van hetzelfde."
        )
    else:
        regels.append(f"Dat is een verschil van {achterstand} commit{meervoud}.")
    if gemeld_datum:
        regels.append(f"De draaiende commit is van {gemeld_datum}.")
    if ouderdom_uur is not None:
        regels.append(f"De verwachte commit staat er al {ouderdom_uur / 24:.1f} dagen.")
    if gestart_op:
        regels.append(
            f"De instantie is gestart op {gestart_op}. Let op: herstarten haalt geen nieuwe code binnen, "
            "uitrollen doet dat."
        )
    regels.append("Kijk in Render bij de service: staat Auto-Deploy aan, en wat zegt het tabblad Events?")
    return Uitkomst(1, regels)


def haal_versie(basis: str, *, pogingen: int = 2, wachten: float = 2.0) -> dict:
    """Vraagt `/api/version` op. Werpt Onbereikbaar als het na alle pogingen niet lukt.

    Twee pogingen, want een geplande controle die op een enkele netwerkhik
    afgaat, leert iedereen binnen een week om de melding te negeren.
    """
    url = basis.rstrip("/") + "/api/version"
    laatste = "geen"
    for poging in range(1, pogingen + 1):
        try:
            with urllib.request.urlopen(url, timeout=TIJDSLIMIET) as antwoord:
                rauw = antwoord.read().decode("utf-8", errors="replace")
            geladen = json.loads(rauw)
            if not isinstance(geladen, dict):
                raise ValueError(f"antwoord is geen object maar {type(geladen).__name__}")
            return geladen
        except (urllib.error.URLError, OSError, ValueError) as fout:
            laatste = f"{type(fout).__name__}: {fout}"
            if poging < pogingen:
                time.sleep(wachten)
    raise Onbereikbaar(f"{url} gaf na {pogingen} pogingen geen leesbaar antwoord ({laatste})")


def _git(*argumenten: str) -> str | None:
    """Git aanroepen; None bij elke fout. Een onbekende commit is hier normaal, geen crash."""
    try:
        klaar = subprocess.run(["git", *argumenten], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if klaar.returncode != 0:
        return None
    return klaar.stdout.strip() or None


def _ouderdom_in_uren(commit: str) -> float | None:
    """Hoe lang staat deze commit er al? None als git hem niet kent."""
    stempel = _git("log", "-1", "--format=%ct", commit)
    if not stempel:
        return None
    try:
        gemaakt = datetime.fromtimestamp(int(stempel), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return (datetime.now(tz=timezone.utc) - gemaakt).total_seconds() / 3600


def _achterstand(gemeld: str, verwacht: str) -> int | None:
    """Aantal commits tussen wat draait en wat zou moeten draaien. None als git het niet weet."""
    aantal = _git("rev-list", "--count", f"{gemeld}..{verwacht}")
    if aantal is None:
        return None
    try:
        return int(aantal)
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    ontleder = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ontleder.add_argument("--url", default=os.getenv("DRIFT_BASIS_URL") or BASIS_STANDAARD,
                          help=f"basis-URL van de live omgeving (standaard {BASIS_STANDAARD})")
    ontleder.add_argument("--commit", default=os.getenv("DRIFT_VERWACHTE_COMMIT") or None,
                          help="welke commit zou moeten draaien (standaard: HEAD van deze checkout)")
    ontleder.add_argument("--respijt", type=float, default=respijt_uit_omgeving(os.getenv("DRIFT_RESPIJT_UUR")),
                          help="hoeveel uur een verse commit mag uitrollen voordat dit achterstand heet")
    argumenten = ontleder.parse_args(argv)

    verwacht = argumenten.commit or _git("rev-parse", "HEAD")
    if not verwacht:
        print("Geen commit om tegen te vergelijken: geef --commit mee of draai dit in een git-checkout.")
        return 2

    print(f"Controle van {argumenten.url}/api/version, verwacht {verwacht[:12]}")

    try:
        gemeld = haal_versie(argumenten.url)
    except Onbereikbaar as fout:
        print(f"Niet vast te stellen: {fout}")
        return 2

    draait = (gemeld.get("commit_volledig") or gemeld.get("commit") or "").strip() or None
    uitkomst = beoordeel(
        verwacht,
        draait,
        ouderdom_uur=_ouderdom_in_uren(verwacht),
        respijt_uur=argumenten.respijt,
        achterstand=_achterstand(draait, verwacht) if draait else None,
        gemeld_datum=_git("log", "-1", "--format=%cs", draait) if draait else None,
        gestart_op=(gemeld.get("gestart_op") or "").strip() or None,
    )
    for regel in uitkomst.regels:
        print(regel)
    return uitkomst.code


if __name__ == "__main__":
    sys.exit(main())
