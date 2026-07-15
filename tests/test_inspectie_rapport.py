"""Narratieve tekstgeneratie voor het inspectierapport (pure functies)."""

import inspectie_rapport as rapport


def test_inleiding_bevat_object_en_norm():
    txt = rapport.inleiding(
        kw_label="Brug", obj_naam="KW-12 Brug Naaldwijk-Oost",
        inspectie_type="Hoofdinspectie", datum_str="14-05-2026",
        inspecteur="J. de Vries", norm_ref="NEN 2767-2 en CROW 134")
    assert "KW-12 Brug Naaldwijk-Oost" in txt
    assert "NEN 2767-2 en CROW 134" in txt
    assert "14-05-2026" in txt
    assert "J. de Vries" in txt


def test_objectbeschrijving_neemt_metadata_mee():
    txt = rapport.objectbeschrijving(
        kw_label="Brug", obj_naam="KW-12", bouwjaar="1978",
        beheerder="Gemeente Testdam", wegnr="N220", locatie_oms="Nabij rotonde",
        coords="52.000000, 4.000000")
    assert "1978" in txt
    assert "Gemeente Testdam" in txt
    assert "N220" in txt
    assert "52.000000, 4.000000" in txt


def test_objectbeschrijving_zonder_metadata_blijft_geldig():
    txt = rapport.objectbeschrijving(
        kw_label=None, obj_naam="KW-9", bouwjaar=None, beheerder=None,
        wegnr=None, locatie_oms=None, coords=None)
    assert txt.startswith("KW-9 betreft een kunstwerk")
    assert txt.endswith(".")


def test_werkwijze_noemt_methodiek():
    txt = rapport.werkwijze()
    assert "NEN 2767-2" in txt
    assert "worst-defect" in txt
    assert "CROW 134" in txt


def test_element_alinea_meervoud_en_enkelvoud():
    assert "geen gebreken" in rapport.element_alinea(
        naam="Landhoofd", code="LH", score=2, defect_count=0)
    assert "1 gebrek" in rapport.element_alinea(
        naam="Landhoofd", code="LH", score=3, defect_count=1)
    drie = rapport.element_alinea(naam="Landhoofd", code="LH", score=4, defect_count=3)
    assert "3 gebreken" in drie
    assert "conditiescore 4" in drie


def test_defect_zin_bevat_classificatie_en_maatregel():
    txt = rapport.defect_zin(
        gebrek_naam="Scheurvorming", ernst=3, intensiteit=2, omvang=4,
        defect_score=5, crow_klasse="M2", locatie="zuidzijde",
        omschrijving="Langsscheur over 2 meter", maatregel="Polymeer-vulling")
    assert "Scheurvorming" in txt
    assert "zuidzijde" in txt
    assert "ernst 3" in txt and "intensiteit 2" in txt
    assert "CROW-klasse M2" in txt
    assert "Polymeer-vulling" in txt


def test_conditie_analyse_none_en_normaal():
    leeg = rapport.conditie_analyse(
        eind=None, defecten_totaal=0, defecten_kritiek=0,
        elementen_beoordeeld=0, elementen_totaal=0, slechtste_naam=None)
    assert "nog niet worden vastgesteld" in leeg

    txt = rapport.conditie_analyse(
        eind=4, defecten_totaal=7, defecten_kritiek=2,
        elementen_beoordeeld=5, elementen_totaal=6, slechtste_naam="Brugdek")
    assert "conditie" not in txt.lower() or "4" in txt
    assert "Brugdek" in txt
    assert "7 gebreken" in txt
    assert "2 als kritiek" in txt


def test_conclusie_bevat_categorie_en_vrije_tekst():
    advies = {"categorie": "groot-onderhoud", "actie": "Groot onderhoud nodig", "termijn_jaren": 1}
    txt = rapport.conclusie(
        eind=4, advies=advies,
        samenvatting_vrij="Brug structureel nog veilig",
        aanbevolen_vrij="Plan voegrenovatie", volgende_str="14-05-2027")
    assert "groot-onderhoud" in txt
    assert "binnen 1 jaar" in txt
    assert "Brug structureel nog veilig" in txt
    assert "Plan voegrenovatie" in txt
    assert "14-05-2027" in txt
