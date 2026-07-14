"""Statische bewaking van de auth-opslag in portaal.html.

Sinds de "ingelogd blijven"-opt-in leeft het token standaard in sessionStorage
(wachtwoord elke keer opnieuw invullen); alleen bij een expliciete keuze op de
loginpagina (hash-param remember=1) gaat het naar localStorage. Deze tests
voorkomen dat er later weer directe localStorage-reads/writes bijkomen die
die keuze omzeilen.
"""
import re
from pathlib import Path

PORTAAL = (Path(__file__).resolve().parent.parent / "templates" / "portaal.html").read_text(
    encoding="utf-8"
)


def test_geen_directe_token_reads_buiten_helper():
    # Alleen de _authSet-helper mag localStorage.getItem('fieldops_token') aanraken.
    hits = re.findall(r"localStorage\.getItem\('fieldops_token'\)", PORTAAL)
    assert len(hits) == 1, (
        "Directe localStorage-reads van fieldops_token gevonden buiten de _authSet-helper; "
        "gebruik _authGet('fieldops_token') zodat de sessie-vs-onthouden-keuze blijft werken."
    )


def test_geen_directe_user_storage_writes():
    assert "localStorage.setItem('fieldops_user'" not in PORTAAL, (
        "Schrijf fieldops_user via _authSet(...) zodat de opslag het token volgt."
    )
    assert "localStorage.setItem('fieldops_token'" not in PORTAAL.replace(
        "store.setItem('fieldops_token'", ""
    ), "Schrijf fieldops_token alleen in init via de remember-keuze (store.setItem)."


def test_remember_keuze_en_sessionstorage_aanwezig():
    assert "params.remember === '1' ? localStorage : sessionStorage" in PORTAAL
    for helper in ("_authGet", "_authSet", "_authClear"):
        assert f"function {helper}(" in PORTAAL, f"helper {helper} ontbreekt"


def test_401_redirect_met_sessie_verlopen_uitleg():
    assert "https://fieldopsapp.nl/#sessie=verlopen" in PORTAAL, (
        "Bij 401 hoort de gebruiker met uitleg terug te komen op de loginpagina "
        "(hash #sessie=verlopen opent daar de login-modal met melding)."
    )
