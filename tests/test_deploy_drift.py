"""De drift-controle: draait productie wel wat er in main staat?

Deze tests draaien geen netwerk en geen git. Het oordeel zit in `beoordeel()`
en dat is bewust een pure functie, zodat juist de gevallen die je in het echt
niet kunt opzetten -- productie weken achter, een onbekende hash, een service
die zijn commit niet meldt -- hier gewoon na te rekenen zijn.

De laatste test is de storing van 9 november 2026 zelf, met de echte hashes.
Die is de reden dat dit script bestaat.
"""
import json
import urllib.error

import pytest

from scripts.deploy_drift import (
    Onbereikbaar,
    beoordeel,
    haal_versie,
    respijt_uit_omgeving,
    zelfde_commit,
)

VERWACHT = "e2951477a783c6c6d1218bc622fe2e8b0c68f5aa"
OUD = "e6b2dc13a52863c6c6d1218bc622fe2e8b0c68f5"


def test_dezelfde_commit_is_goed():
    uit = beoordeel(VERWACHT, VERWACHT, ouderdom_uur=100.0)
    assert uit.code == 0
    assert "e2951477a783" in uit.regels[0]


def test_twaalf_tekens_tellen_als_dezelfde_commit():
    """Productie meldt twaalf tekens, git veertig; dat mag geen vals alarm geven."""
    assert zelfde_commit(VERWACHT, VERWACHT[:12])
    assert beoordeel(VERWACHT, VERWACHT[:12], ouderdom_uur=100.0).code == 0


def test_een_hash_die_pas_laat_afwijkt_is_niet_dezelfde():
    assert not zelfde_commit(VERWACHT, VERWACHT[:11] + "0")


def test_een_te_korte_hash_vergelijken_we_niet():
    """Op drie tekens lijkt alles op elkaar; dan liever geen uitspraak."""
    assert not zelfde_commit(VERWACHT, "e29")


def test_een_verse_commit_krijgt_respijt():
    """Vlak na een merge is achterlopen normaal -- anders meldt dit bij elke merge."""
    uit = beoordeel(VERWACHT, OUD, ouderdom_uur=0.5, respijt_uur=2.0, achterstand=11)
    assert uit.code == 0
    assert "respijt" in " ".join(uit.regels)


def test_na_het_respijt_is_het_achterstand():
    uit = beoordeel(VERWACHT, OUD, ouderdom_uur=5.0, respijt_uur=2.0, achterstand=11)
    assert uit.code == 1
    assert "11 commits" in " ".join(uit.regels)


def test_een_commit_verschil_blijft_enkelvoud():
    uit = beoordeel(VERWACHT, OUD, ouderdom_uur=5.0, achterstand=1)
    assert "1 commit." in " ".join(uit.regels)


def test_zonder_gemelde_commit_is_het_code_twee():
    """Niet kunnen kijken is iets anders dan achterlopen, en dat moet je in de logs zien."""
    uit = beoordeel(VERWACHT, None)
    assert uit.code == 2
    assert "RENDER_GIT_COMMIT" in " ".join(uit.regels)


def test_een_onbekende_hash_wordt_als_onbekend_gemeld():
    """Niet in de historie: dan is 'zoveel commits achter' een verzonnen getal."""
    uit = beoordeel(VERWACHT, "0000000deadbeef", ouderdom_uur=50.0, achterstand=None)
    assert uit.code == 1
    assert "niet in de historie" in " ".join(uit.regels)


def test_productie_kan_ook_iets_heel_anders_draaien():
    """Nul commits verschil maar een andere hash: geen oudere versie, een andere."""
    uit = beoordeel(VERWACHT, OUD, ouderdom_uur=50.0, achterstand=0)
    assert uit.code == 1
    assert "iets anders" in " ".join(uit.regels)


@pytest.mark.parametrize("rauw,verwacht", [
    ("3", 3.0),
    ("  2,5  ", 2.5),
    ("", 2.0),
    (None, 2.0),
    ("twee", 2.0),
    ("-1", 0.0),
])
def test_respijt_uit_de_omgeving_houdt_stand(rauw, verwacht):
    """Een typefout in de variabele mag de controle niet uitschakelen."""
    assert respijt_uit_omgeving(rauw) == verwacht


class _Antwoord:
    """Minimale nabootsing van wat urlopen teruggeeft."""

    def __init__(self, inhoud: bytes):
        self._inhoud = inhoud

    def read(self):
        return self._inhoud

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_haal_versie_leest_het_antwoord(monkeypatch):
    monkeypatch.setattr(
        "scripts.deploy_drift.urllib.request.urlopen",
        lambda *a, **k: _Antwoord(json.dumps({"commit": "abc123def456"}).encode()),
    )
    assert haal_versie("https://voorbeeld.test")["commit"] == "abc123def456"


def test_haal_versie_probeert_het_twee_keer(monkeypatch):
    """Een enkele netwerkhik mag geen melding worden; daar leert niemand van."""
    pogingen = []

    def _stuk(*_a, **_k):
        pogingen.append(1)
        raise urllib.error.URLError("tijdelijk weg")

    monkeypatch.setattr("scripts.deploy_drift.urllib.request.urlopen", _stuk)
    with pytest.raises(Onbereikbaar):
        haal_versie("https://voorbeeld.test", wachten=0)
    assert len(pogingen) == 2


def test_een_html_foutpagina_is_onbereikbaar_en_geen_drift(monkeypatch):
    """Render zet er bij een storing een HTML-pagina neer; die is geen versie-antwoord."""
    monkeypatch.setattr(
        "scripts.deploy_drift.urllib.request.urlopen",
        lambda *a, **k: _Antwoord(b"<html>Service unavailable</html>"),
    )
    with pytest.raises(Onbereikbaar):
        haal_versie("https://voorbeeld.test", wachten=0)


def test_de_storing_van_negen_november():
    """Het echte geval: zeven weken achter, en de herstart die niets uitrolde.

    Productie stond op e6b2dc1 (24 september) terwijl main op e295147 stond, en
    de instantie was diezelfde avond nog opnieuw opgestart. Als deze test ooit
    groen blijft terwijl de code dit niet meer meldt, is de controle stuk.
    """
    uit = beoordeel(
        "e2951477a783c6c6d1218bc622fe2e8b0c68f5aa",
        "e6b2dc13a52863c6c6d1218bc622fe2e8b0c68f5",
        ouderdom_uur=10.0,
        respijt_uur=2.0,
        achterstand=11,
        gemeld_datum="2026-09-24",
        gestart_op="2026-11-09T20:23:38+00:00",
    )
    tekst = " ".join(uit.regels)
    assert uit.code == 1
    assert "11 commits" in tekst
    assert "2026-09-24" in tekst
    assert "herstarten haalt geen nieuwe code binnen" in tekst
    assert "Auto-Deploy" in tekst
