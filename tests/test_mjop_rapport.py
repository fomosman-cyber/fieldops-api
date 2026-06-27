"""Narratieve tekstgeneratie voor het MJOP-rapport (pure functies)."""

import mjop_rapport as rapport


def test_eur_nederlandse_duizendscheiding():
    assert rapport._eur(12345) == "12.345"
    assert rapport._eur(0) == "0"
    assert rapport._eur(None) == "0"


def test_inleiding_bevat_scope_horizon_en_norm():
    txt = rapport.inleiding(
        scope_naam="Gemeente Testdam (organisatie-breed)", years=10,
        assets_in_scope=480, include_score_2=False)
    assert "Gemeente Testdam" in txt
    assert "10 jaar" in txt
    assert "NEN 2767-2" in txt
    assert "480" in txt
    assert "score 3 en hoger" in txt or "conditiescore 3" in txt


def test_inleiding_preventief_variant():
    txt = rapport.inleiding(
        scope_naam="Project X", years=5, assets_in_scope=10, include_score_2=True)
    assert "Preventieve maatregelen" in txt


def test_managementsamenvatting_bevat_kerncijfers():
    txt = rapport.managementsamenvatting(
        total_min=120000, total_max=185000, years=10, mjop_regels=42,
        assets_count=30, assets_zonder_score=5, prioriteit_count=7,
        piekjaar=2029, piekjaar_max=60000)
    assert "EUR 120.000" in txt
    assert "EUR 185.000" in txt
    assert "42" in txt
    assert "7 assets prioriteit" in txt
    assert "jaar 2029" in txt
    assert "5 assets" in txt and "geen conditie-score" in txt


def test_managementsamenvatting_zonder_prioriteit_of_piek():
    txt = rapport.managementsamenvatting(
        total_min=0, total_max=0, years=10, mjop_regels=0,
        assets_count=0, assets_zonder_score=0, prioriteit_count=0,
        piekjaar=None, piekjaar_max=0)
    assert "geen assets in een matige of slechtere staat" in txt.lower()
