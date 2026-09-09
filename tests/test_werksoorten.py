"""Bewaakt de werksoort-catalogus en de kaartkleuren in het portaal.

De kaart kleurt markers in JavaScript, de importer schrijft de categorie weg
in Python. Beide halen hun waarheid uit werksoorten.py, maar de JS-map is een
handmatige kopie — deze test valt om zodra die uit de pas loopt.
"""
import codecs
import re
from pathlib import Path

from werksoorten import (
    ONBEPAALD,
    WERKSOORT_KLEUREN,
    WERKSOORTEN,
    kleur_voor,
)

PORTAAL = Path(__file__).resolve().parent.parent / "templates" / "portaal.html"


def _js_werksoort_kleuren() -> dict[str, str]:
    """De WERKSOORT_KLEUREN-map uit portaal.html uitlezen."""
    html = PORTAAL.read_text(encoding="utf-8")
    blok = re.search(r"var WERKSOORT_KLEUREN = \{(.*?)\};", html, re.S)
    assert blok, "WERKSOORT_KLEUREN niet gevonden in portaal.html"
    paren = re.findall(r"'([^']+)'\s*:\s*'(#[0-9a-fA-F]{6})'", blok.group(1))
    # De JS-bron schrijft het en-streepje als –.
    return {codecs.decode(k, "unicode_escape"): v for k, v in paren}


def test_js_kleuren_gelijk_aan_python():
    assert _js_werksoort_kleuren() == WERKSOORT_KLEUREN


def test_drie_werksoorten_plus_onbepaald():
    labels = [w["label"] for w in WERKSOORTEN]
    assert labels[:3] == ["Hotbox werkzaamheden", "Asfalt machinaal", "Scheuren vullen"]
    assert ONBEPAALD in labels


def test_kleuren_zijn_rood_groen_oranje():
    """De klant herkent de punten aan hun kleur — die ligt vast."""
    assert WERKSOORT_KLEUREN["Hotbox werkzaamheden"] == "#ff5252"    # rood
    assert WERKSOORT_KLEUREN["Asfalt machinaal"] == "#22c55e"        # groen
    assert WERKSOORT_KLEUREN["Scheuren vullen"] == "#ffab40"         # oranje


def test_kleur_voor_valt_terug_op_none():
    assert kleur_voor("Riolering") is None
    assert kleur_voor(None) is None
    assert kleur_voor(" Scheuren vullen ") == "#ffab40"


def test_legenda_toont_elke_werksoort():
    html = PORTAAL.read_text(encoding="utf-8")
    legenda = html.split('Werksoort</div>', 1)
    assert len(legenda) == 2, "werksoort-legenda ontbreekt op de kaart"
    blok = legenda[1][:1200]
    for w in WERKSOORTEN:
        assert w["kleur"] in blok, f"kleur van {w['label']} ontbreekt in de legenda"


def test_markers_kleuren_via_gedeelde_helper():
    """Beide kaarten moeten dezelfde helper gebruiken, anders loopt een van de
    twee achter zodra de werksoorten wijzigen."""
    html = PORTAAL.read_text(encoding="utf-8")
    assert html.count("var color = meldingKleur(m);") == 2
    assert "WERKSOORT_KLEUREN[m.category] || PRIORITEIT_KLEUREN[m.priority]" in html


def test_bulkactie_biedt_elke_werksoort():
    html = PORTAAL.read_text(encoding="utf-8")
    keuze = html.split('id="bulkWerksoortSelect"', 1)
    assert len(keuze) == 2, "bulk-actie 'Werksoort wijzigen' ontbreekt"
    blok = keuze[1][:1200]
    for w in WERKSOORTEN:
        # In een HTML-attribuut telt alleen het echte teken: een \\u2013-escape
        # wordt daar niet uitgelezen en zou een categorie opleveren die nergens
        # op matcht.
        assert f'value="{w["label"]}"' in blok, f"{w['label']} ontbreekt in de bulk-actie"
