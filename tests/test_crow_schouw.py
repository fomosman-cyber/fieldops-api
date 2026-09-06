"""Beeldkwaliteit op gebiedsniveau — de schoon-kant van de CROW-schouw.

Wat hier echt bewaakt wordt:

1. **Niet beoordeeld is niet hetzelfde als schoon.** Een meetlat zonder
   ingestelde drempels komt terug onder `niet_beoordeeld` en telt niet mee in
   het eindoordeel. Een schouw die stilzwijgend "B" invult waar niemand iets
   heeft ingesteld, laat een gebied slagen dat nooit bekeken is.
2. **Binnen een vak geldt de slechtste meetlat, daarboven niet.** Een straat is
   zo schoon als zijn vuilste meetlat; een wijk is dat niet, anders is een hele
   wijk D door één straat.
"""

import pytest

import crow_schouw as cs


@pytest.fixture
def drempels():
    """Voorbeeldgrenzen. De echte komen uit het bestek van de opdrachtgever."""
    return cs.Drempels({
        "zwerfafval": {"A+": 0, "A": 2, "B": 5, "C": 10},
        "onkruid_verharding": {"A+": 0, "A": 5, "B": 15, "C": 30},
        "bekladding": {"A+": 0, "A": 1, "B": 3, "C": 10},
    })


# ---------------------------------------------------------------------------
# Drempels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("waarde,verwacht", [
    (0, "A+"), (1, "A"), (2, "A"), (3, "B"), (5, "B"),
    (6, "C"), (10, "C"), (11, "D"), (250, "D"),
])
def test_drempel_is_de_bovengrens_van_een_niveau(drempels, waarde, verwacht):
    assert drempels.klasse_voor("zwerfafval", waarde) == verwacht


def test_zonder_drempels_geen_oordeel(drempels):
    """Liever geen uitspraak dan een verzonnen uitspraak."""
    assert drempels.klasse_voor("blad", 3) is None
    assert drempels.klasse_voor("zwerfafval", None) is None


# ---------------------------------------------------------------------------
# Een vak beoordelen
# ---------------------------------------------------------------------------

def test_vak_krijgt_de_klasse_van_zijn_vuilste_meetlat(drempels):
    r = cs.beoordeel_vak(
        {"zwerfafval": 1, "onkruid_verharding": 40},   # A en D
        drempels=drempels, gebiedstype="woonwijk")
    assert r["per_meetlat"]["zwerfafval"]["klasse"] == "A"
    assert r["per_meetlat"]["onkruid_verharding"]["klasse"] == "D"
    assert r["beeldkwaliteit"] == "D"
    assert r["voldoet"] is False
    assert r["onder_ambitie"] == ["onkruid_verharding"]


def test_niet_beoordeelde_meetlat_telt_niet_mee_maar_verdwijnt_ook_niet(drempels):
    r = cs.beoordeel_vak({"zwerfafval": 1, "blad": 90},
                         drempels=drempels, gebiedstype="woonwijk")
    assert r["beeldkwaliteit"] == "A"          # blad drukt de score niet
    assert r["niet_beoordeeld"] == ["blad"]    # maar staat er wel
    assert "blad" not in r["per_meetlat"]


def test_onbekende_meetlat_wordt_apart_gemeld(drempels):
    r = cs.beoordeel_vak({"zwerfafval": 1, "verzonnen_meetlat": 3},
                         drempels=drempels)
    assert r["onbekende_meetlatten"] == ["verzonnen_meetlat"]


def test_ambitie_volgt_het_gebiedstype(drempels):
    """Een centrum wordt strenger beoordeeld dan een bedrijventerrein."""
    waarneming = {"zwerfafval": 4}             # dat is niveau B
    centrum = cs.beoordeel_vak(waarneming, drempels=drempels, gebiedstype="centrum")
    terrein = cs.beoordeel_vak(waarneming, drempels=drempels, gebiedstype="bedrijventerrein")

    assert centrum["ambitie"] == "A" and centrum["voldoet"] is False
    assert terrein["ambitie"] == "C" and terrein["voldoet"] is True


def test_expliciete_ambitie_wint_van_het_gebiedstype(drempels):
    r = cs.beoordeel_vak({"zwerfafval": 4}, drempels=drempels,
                         gebiedstype="bedrijventerrein", ambitie="A+")
    assert r["ambitie"] == "A+"
    assert r["voldoet"] is False


def test_leeg_vak_geeft_geen_oordeel(drempels):
    r = cs.beoordeel_vak({}, drempels=drempels, gebiedstype="woonwijk")
    assert r["beeldkwaliteit"] is None
    assert r["voldoet"] is None


# ---------------------------------------------------------------------------
# Meerdere vakken
# ---------------------------------------------------------------------------

def test_gebied_middelt_wel_ook_al_doet_een_vak_dat_niet(drempels):
    """De slechtste-aspect-regel geldt binnen een vak, niet erboven.

    Anders is een wijk van honderd schone straten D omdat er één vervuild is,
    en daar kan een opdrachtgever niets mee sturen.
    """
    schoon = [cs.beoordeel_vak({"zwerfafval": 1}, drempels=drempels,
                               gebiedstype="woonwijk") for _ in range(3)]
    vuil = cs.beoordeel_vak({"zwerfafval": 50}, drempels=drempels,
                            gebiedstype="woonwijk")

    s = cs.samenvatting(schoon + [vuil])
    assert s["beoordeeld"] == 4
    assert s["voldoet"] == 3
    assert s["voldoet_pct"] == 75.0
    assert s["verdeling"] == {"A": 3, "D": 1}
    assert s["meest_tekort"][0]["meetlat"] == "zwerfafval"


def test_samenvatting_zonder_beoordeelde_vakken(drempels):
    leeg = cs.beoordeel_vak({}, drempels=drempels)
    s = cs.samenvatting([leeg, leeg])
    assert s["beoordeeld"] == 0
    assert s["voldoet_pct"] is None


# ---------------------------------------------------------------------------
# De catalogus zelf
# ---------------------------------------------------------------------------

def test_elke_meetlat_heeft_een_eenheid_en_een_groep():
    """Zonder eenheid weet niemand of 3 stuks, procenten of centimeters zijn."""
    for m in cs.MEETLATTEN:
        assert m.get("eenheid"), m["code"]
        assert m.get("groep") in ("schoon", "heel", "groen"), m["code"]


def test_detecteerbare_meetlatten_zijn_een_deelverzameling():
    """Wat een camera niet betrouwbaar ziet, hoort niet te doen alsof.

    Uitwerpselen staan bewust op niet-detecteerbaar; die blijven mensenwerk.
    """
    alle = {m["code"] for m in cs.meetlatten_voor()}
    detect = {m["code"] for m in cs.meetlatten_voor(alleen_detecteerbaar=True)}
    assert detect < alle
    assert "hondenpoep" not in detect


def test_gangbare_ambitie_valt_terug_op_de_standaard():
    assert cs.gangbare_ambitie("centrum") == "A"
    assert cs.gangbare_ambitie(None) == cs.DEFAULT_AMBITIE
    assert cs.gangbare_ambitie("bestaat-niet") == cs.DEFAULT_AMBITIE
