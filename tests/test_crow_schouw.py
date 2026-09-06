"""Beeldkwaliteit op gebiedsniveau — meetlatten per verschijnsel én drager.

Wat hier vastligt:

1. **Een meetlat is een verschijnsel op een drager.** Graffiti op een verkeers-
   bord is een andere meetlat dan graffiti op een viaduct, want de norm en de
   maatregel verschillen. Wie ze samenvoegt levert een rapport waar geen
   aannemer op aangesproken kan worden.
2. **Niet beoordeeld is niet hetzelfde als schoon.** Een meetlat zonder
   drempels telt niet mee én verdwijnt niet.
3. **Binnen een vak geldt de slechtste meetlat, daarboven niet.** Een straat is
   zo schoon als zijn vuilste meetlat; een wijk is dat niet.
"""

import pytest

import crow_schouw as cs

AFVAL = "zwerfafval.elementenverharding"
GRAFFITI_BORD = "bekladding.verkeersbord"
ONKRUID = "onkruid.elementenverharding"


@pytest.fixture
def drempels():
    """Voorbeeldgrenzen. De echte komen uit het bestek van de opdrachtgever."""
    return cs.Drempels(
        per_meetlat={
            AFVAL: {"A+": 0, "A": 2, "B": 5, "C": 10},
            ONKRUID: {"A+": 0, "A": 5, "B": 15, "C": 30},
        },
        # Eén afspraak voor alle graffiti, ongeacht waar hij op zit.
        per_verschijnsel={"bekladding": {"A+": 0, "A": 1, "B": 3, "C": 10}},
    )


# ---------------------------------------------------------------------------
# De structuur zelf
# ---------------------------------------------------------------------------

def test_graffiti_is_per_drager_een_eigen_meetlat():
    """Een sticker van een bord halen is iets anders dan een viaduct reinigen."""
    codes = {m["code"] for m in cs.meetlatten_voor(verschijnsel="bekladding")}
    assert {"bekladding.verkeersbord", "bekladding.viaduct_brug",
            "bekladding.nutskast", "bekladding.afvalbak"} <= codes
    assert len(codes) >= 8


def test_zwerfafval_is_per_ondergrond_een_eigen_meetlat():
    codes = {m["code"] for m in cs.meetlatten_voor(verschijnsel="zwerfafval")}
    assert {"zwerfafval.gesloten_verharding", "zwerfafval.elementenverharding",
            "zwerfafval.groen", "zwerfafval.water"} <= codes


def test_elke_meetlat_verwijst_naar_een_bestaand_verschijnsel_en_drager():
    for m in cs.MEETLATTEN:
        assert m["verschijnsel"] in cs.VERSCHIJNSELEN, m["code"]
        assert m["drager"] in cs.DRAGERS, m["code"]
        assert m["code"] == f"{m['verschijnsel']}.{m['drager']}"


def test_detecteerbare_meetlatten_zijn_een_deelverzameling():
    """Wat een camera niet betrouwbaar ziet, hoort niet te doen alsof."""
    alle = {m["code"] for m in cs.meetlatten_voor()}
    detect = {m["code"] for m in cs.meetlatten_voor(alleen_detecteerbaar=True)}
    assert detect < alle
    assert not any(c.startswith("uitwerpselen.") for c in detect)


# ---------------------------------------------------------------------------
# Van detectie naar meetlat
# ---------------------------------------------------------------------------

def test_detectieklasse_plus_drager_wordt_een_meetlat():
    assert cs.meetlat_voor("graffiti", "nutskast") == "bekladding.nutskast"
    assert cs.meetlat_voor("onkruid", "boomspiegel") == "onkruid.boomspiegel"


def test_klasse_met_standaarddrager_werkt_zonder_drager():
    """Afval op straat is de normale situatie; die hoef je niet te benoemen."""
    assert cs.meetlat_voor("afval_los") == "zwerfafval.elementenverharding"


def test_graffiti_zonder_drager_levert_geen_meetlat():
    """"Er zit graffiti" zonder te zeggen waarop is niet te verhelpen."""
    assert cs.meetlat_voor("graffiti") is None
    assert cs.meetlat_voor("scheefstand") is None


def test_drager_die_niet_bij_de_klasse_hoort_geeft_een_fout():
    with pytest.raises(cs.OnbekendeDrager):
        cs.meetlat_voor("graffiti", "gras")


def test_onbekende_detectieklasse_levert_niets():
    assert cs.meetlat_voor("verzonnen_klasse", "gras") is None


# ---------------------------------------------------------------------------
# Drempels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("waarde,verwacht", [
    (0, "A+"), (2, "A"), (3, "B"), (5, "B"), (10, "C"), (11, "D"),
])
def test_drempel_is_de_bovengrens_van_een_niveau(drempels, waarde, verwacht):
    assert drempels.klasse_voor(AFVAL, waarde) == verwacht


def test_drempel_per_verschijnsel_geldt_voor_alle_dragers(drempels):
    """Eén afspraak voor alle graffiti hoef je niet tien keer in te vullen."""
    assert drempels.klasse_voor(GRAFFITI_BORD, 2) == "B"
    assert drempels.klasse_voor("bekladding.tunnel", 2) == "B"


def test_specifieke_drempel_wint_van_de_algemene():
    d = cs.Drempels(
        per_meetlat={"bekladding.viaduct_brug": {"A+": 0, "A": 10, "B": 40}},
        per_verschijnsel={"bekladding": {"A+": 0, "A": 1, "B": 3}},
    )
    assert d.klasse_voor(GRAFFITI_BORD, 5) == "D"           # algemene regel
    assert d.klasse_voor("bekladding.viaduct_brug", 5) == "A"   # eigen regel


def test_zonder_drempels_geen_oordeel(drempels):
    assert drempels.klasse_voor("blad.elementenverharding", 3) is None
    assert drempels.klasse_voor(AFVAL, None) is None


# ---------------------------------------------------------------------------
# Een vak beoordelen
# ---------------------------------------------------------------------------

def test_vak_krijgt_de_klasse_van_zijn_vuilste_meetlat(drempels):
    r = cs.beoordeel_vak({AFVAL: 1, ONKRUID: 40},
                         drempels=drempels, gebiedstype="woonwijk")
    assert r["per_meetlat"][AFVAL]["klasse"] == "A"
    assert r["per_meetlat"][ONKRUID]["klasse"] == "D"
    assert r["beeldkwaliteit"] == "D"
    assert r["voldoet"] is False
    assert r["onder_ambitie"] == [ONKRUID]


def test_resultaat_draagt_verschijnsel_en_drager_mee(drempels):
    """Een rapport moet kunnen zeggen wát er waarop zit."""
    r = cs.beoordeel_vak({GRAFFITI_BORD: 2}, drempels=drempels)
    regel = r["per_meetlat"][GRAFFITI_BORD]
    assert regel["verschijnsel"] == "bekladding"
    assert regel["drager"] == "verkeersbord"
    assert "verkeersbord" in regel["naam"].lower()


def test_directe_klasse_voor_wat_je_niet_meet(drempels):
    """Scheefstand meet je niet op straat, die zie je."""
    r = cs.beoordeel_vak({AFVAL: 1},
                         directe_klassen={"scheefstand.lichtmast": "C"},
                         drempels=drempels, gebiedstype="woonwijk")
    assert r["per_meetlat"]["scheefstand.lichtmast"]["klasse"] == "C"
    assert r["per_meetlat"]["scheefstand.lichtmast"]["waarde"] is None
    assert r["beeldkwaliteit"] == "C"


def test_ongeldige_directe_klasse_telt_als_niet_beoordeeld(drempels):
    r = cs.beoordeel_vak(directe_klassen={"scheefstand.lichtmast": "Z"},
                         drempels=drempels)
    assert r["beeldkwaliteit"] is None
    assert "scheefstand.lichtmast" in r["niet_beoordeeld"]


def test_niet_beoordeelde_meetlat_telt_niet_mee_maar_verdwijnt_niet(drempels):
    r = cs.beoordeel_vak({AFVAL: 1, "blad.elementenverharding": 90},
                         drempels=drempels, gebiedstype="woonwijk")
    assert r["beeldkwaliteit"] == "A"
    assert r["niet_beoordeeld"] == ["blad.elementenverharding"]


def test_onbekende_meetlat_wordt_apart_gemeld(drempels):
    r = cs.beoordeel_vak({AFVAL: 1, "verzonnen.meetlat": 3}, drempels=drempels)
    assert r["onbekende_meetlatten"] == ["verzonnen.meetlat"]


def test_ambitie_volgt_het_gebiedstype(drempels):
    waarneming = {AFVAL: 4}                     # niveau B
    centrum = cs.beoordeel_vak(waarneming, drempels=drempels, gebiedstype="centrum")
    terrein = cs.beoordeel_vak(waarneming, drempels=drempels,
                               gebiedstype="bedrijventerrein")
    assert centrum["ambitie"] == "A" and centrum["voldoet"] is False
    assert terrein["ambitie"] == "C" and terrein["voldoet"] is True


def test_expliciete_ambitie_wint_van_het_gebiedstype(drempels):
    r = cs.beoordeel_vak({AFVAL: 4}, drempels=drempels,
                         gebiedstype="bedrijventerrein", ambitie="A+")
    assert r["ambitie"] == "A+" and r["voldoet"] is False


def test_leeg_vak_geeft_geen_oordeel(drempels):
    r = cs.beoordeel_vak({}, drempels=drempels, gebiedstype="woonwijk")
    assert r["beeldkwaliteit"] is None and r["voldoet"] is None


# ---------------------------------------------------------------------------
# Meerdere vakken
# ---------------------------------------------------------------------------

def test_gebied_middelt_wel_ook_al_doet_een_vak_dat_niet(drempels):
    """De slechtste-aspect-regel geldt binnen een vak, niet erboven."""
    schoon = [cs.beoordeel_vak({AFVAL: 1}, drempels=drempels,
                               gebiedstype="woonwijk") for _ in range(3)]
    vuil = cs.beoordeel_vak({AFVAL: 50}, drempels=drempels, gebiedstype="woonwijk")

    s = cs.samenvatting(schoon + [vuil])
    assert s["beoordeeld"] == 4 and s["voldoet"] == 3
    assert s["voldoet_pct"] == 75.0
    assert s["verdeling"] == {"A": 3, "D": 1}
    assert s["meest_tekort"][0]["meetlat"] == AFVAL


def test_samenvatting_zonder_beoordeelde_vakken(drempels):
    leeg = cs.beoordeel_vak({}, drempels=drempels)
    s = cs.samenvatting([leeg, leeg])
    assert s["beoordeeld"] == 0 and s["voldoet_pct"] is None


def test_gangbare_ambitie_valt_terug_op_de_standaard():
    assert cs.gangbare_ambitie("centrum") == "A"
    assert cs.gangbare_ambitie(None) == cs.DEFAULT_AMBITIE
    assert cs.gangbare_ambitie("bestaat-niet") == cs.DEFAULT_AMBITIE
