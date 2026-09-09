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


def test_werksoorten_en_hun_maatregel():
    labels = [w["label"] for w in WERKSOORTEN]
    assert labels[:3] == ["Hotbox werkzaamheden", "Asfalt machinaal", "Scheuren vullen"]
    assert ONBEPAALD in labels
    # Zonder CROW-maatregel valt een melding buiten het clusteren; de
    # job-orchestratie filtert op gw_term. Alleen "nog in te delen" mag leeg zijn.
    import crow_kosten
    for w in WERKSOORTEN:
        if w["label"] == ONBEPAALD:
            assert w["maatregel"] is None
            continue
        assert w["maatregel"] in crow_kosten.MAATREGEL_TO_SKILL, w["label"]
        assert w["gw_term"] and w["kosten_orde"]


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
    blok = legenda[1][:2400]
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
    blok = keuze[1][:2400]
    for w in WERKSOORTEN:
        # In een HTML-attribuut telt alleen het echte teken: een \\u2013-escape
        # wordt daar niet uitgelezen en zou een categorie opleveren die nergens
        # op matcht.
        assert f'value="{w["label"]}"' in blok, f"{w['label']} ontbreekt in de bulk-actie"


def test_kaart_vult_de_hoogte_van_het_scherm():
    """De kaart stond op een vaste 500px terwijl er ruimte zat tot de footer.

    Een height:100% tegen een ouder die zijn hoogte uit flex-grow haalt lost
    niet overal op; dan viel #map terug op zijn min-height en bleef er onder de
    tegels een lege strook staan. Absoluut positioneren binnen de relatieve
    wrapper doet dat wel.
    """
    html = PORTAAL.read_text(encoding="utf-8")
    blok = html.split('<div id="map"', 1)
    assert len(blok) == 2, "kaartcontainer niet gevonden"
    stijl = blok[1][:200]
    assert "position:absolute" in stijl and "inset:0" in stijl
    assert "min-height:500px" not in stijl, "de kaart hoort niet meer op een vaste hoogte te staan"
    assert "min-height:calc(100vh" in blok[0][-400:], "de wrapper moet met het scherm meegroeien"
    # Leaflet meet zijn container een keer; zonder dit blijven de tegels op de
    # oude maat staan als het venster verandert.
    assert "window.addEventListener('resize'" in html and "map.invalidateSize()" in html


def test_legenda_blijft_compact_en_leesbaar():
    """De legenda hoort de kaart te verklaren, niet te bedekken."""
    html = PORTAAL.read_text(encoding="utf-8")
    blok = html.split('id="kaartLegenda"', 1)
    assert len(blok) == 2, "legenda niet gevonden"
    legenda = blok[1].split("<!-- Map Legend", 1)[0][:5000]
    # Het paneel is altijd donker, ook in het lichte thema. Themakleuren maken
    # de tekst daar onleesbaar — dat was precies wat er misging.
    kop = legenda.split(">", 1)[0]
    assert "var(--text)" not in legenda, "vaste lichte kleuren op een donker paneel"
    assert "var(--border)" not in kop
    # Inklapbaar, en die keuze wordt onthouden.
    assert "toggleLegenda(" in legenda
    assert html.count("function toggleLegenda") == 1
    assert "kaartLegendaDicht" in html
    # Elke werksoort staat er nog in — compact maken mag niets weglaten.
    for w in WERKSOORTEN:
        assert w["kleur"] in legenda, f"kleur van {w['label']} ontbreekt in de legenda"
