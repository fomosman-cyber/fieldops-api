"""De exportknoppen in het portaal wijzen naar routes die bestaan, in de huisstijl.

Wat hier vastligt:

1. **Elke exportknop heeft een route.** Een Excel-knop die op een 404 uitkomt
   merkt niemand tot een klant erop drukt.
2. **De huisstijl in de browser is die van de server.** Dezelfde kleuren en
   maten, zodat een PDF uit het portaal naast een PDF van de server hetzelfde
   leest.
3. **Geen paarse terugval meer** als een organisatie geen kleur heeft.
"""

import io
import re

import pytest
import export_huisstijl as h

from .conftest import auth

with io.open("templates/portaal.html", encoding="utf-8") as _f:
    PORTAAL = _f.read()

# (methode, route zoals FastAPI hem kent, stuk tekst dat in het portaal staat)
EXPORTS = [
    ("POST", "/api/export/tabel.xlsx", "'/api/export/tabel.' + formaat"),
    ("POST", "/api/export/tabel.pdf", "'/api/export/tabel.' + formaat"),
    ("GET", "/api/daybook/export.xlsx", "'/api/daybook/export.' + formaat"),
    ("GET", "/api/daybook/export.pdf", "'/api/daybook/export.pdf?'"),
    ("GET", "/api/audit/logs/export.xlsx", "'/api/audit/logs/export.xlsx'"),
    ("GET", "/api/audit/logs/export.pdf", "'/api/audit/logs/export.pdf'"),
    ("GET", "/api/mjop/export.xlsx", "'/api/mjop/export.xlsx?years='"),
    ("GET", "/api/bouw/{bouw_id}/export.xlsx", "'/api/bouw/' + _bouwHuidig.id + '/export.xlsx'"),
    ("GET", "/api/toolbox/{toolbox_id}/export.xlsx", "'/api/toolbox/' + _tbHuidig.id + '/export.xlsx'"),
    ("GET", "/api/wpi/{wpi_id}/export.xlsx", "'/api/wpi/' + _wpiHuidig.id + '/export.xlsx'"),
    ("GET", "/api/kunstwerken-inspecties/{inspection_id}/export.xlsx",
     "'/api/kunstwerken-inspecties/' + _currentKunstwerk.id + '/export.xlsx'"),
    ("GET", "/api/kunstwerken-inspecties/{inspection_id}/export.pdf",
     "'/api/kunstwerken-inspecties/' + _currentKunstwerk.id + '/export.pdf'"),
    ("GET", "/api/meldingen/import/template.csv", "exportBestand(\\'/api/meldingen/import/template.csv\\'"),
]


@pytest.mark.parametrize("methode,pad,in_portaal", EXPORTS, ids=[e[1] for e in EXPORTS])
def test_exportknop_heeft_een_route(client, admin_user, methode, pad, in_portaal):
    assert in_portaal in PORTAAL, f"portaal roept {pad} niet (meer) aan"
    # Echt aanroepen: een ontbrekende route geeft FastAPI's eigen 404 ("Not
    # Found") of 405. Een onbekend id, een lege body of een uitgezette module
    # geeft iets anders -- dan bestaat de route wel.
    url = re.sub(r"\{[^}]+\}", "bestaat-niet", pad)
    r = client.request(methode, url, headers=auth(admin_user), json={} if methode == "POST" else None)
    assert r.status_code != 405, f"{methode} {pad}: methode bestaat niet"
    ontbreekt = r.status_code == 404 and r.headers.get("content-type", "").startswith(
        "application/json") and r.json().get("detail") == "Not Found"
    assert not ontbreekt, f"{methode} {pad} bestaat niet"


def test_browser_en_server_delen_de_huisstijl():
    for naam, hexkleur in (("blauw", h.BLAUW), ("inkt", h.INKT), ("grijs", h.GRIJS),
                           ("lijn", h.LIJN), ("streep", h.STREEP), ("vlak", h.VLAK)):
        r, g, b = h.rgb(hexkleur)
        assert f"{naam}: [{r}, {g}, {b}]" in PORTAAL, naam
    assert f"marge: {h.HuisstijlPDF.MARGE:g}" in PORTAAL
    assert f"kopOnder: {h.HuisstijlPDF.KOP_ONDER:g}" in PORTAAL
    assert f"inhoudStart: {h.HuisstijlPDF.INHOUD_START:g}" in PORTAAL


def test_geen_paarse_terugval_meer():
    assert "[124, 58, 237]" not in PORTAAL


def test_browser_pdfs_tekenen_het_briefhoofd():
    for functie in ("function generatePdfBlob", "function _buildMeldingDocPdf",
                    "function exportCurrentOpleveringPDF"):
        begin = PORTAAL.index(functie)
        stuk = PORTAAL[begin:PORTAAL.index("\n    function ", begin + 10)]
        assert "huisstijlKop(doc)" in stuk and "huisstijlVoet(doc" in stuk, functie
