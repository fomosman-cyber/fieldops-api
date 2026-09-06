"""Welke setup-documenten het portaal publiek serveert.

``/{doc}.md`` had een allowlist van vijf documenten en geen auth. Daar zat
RENDER-SETUP.md bij, en dat bestand bevatte een echte VAPID-private-key plus
Google-sleutels. Iedereen op internet kon die opvragen op
``https://portaal.fieldopsapp.nl/RENDER-SETUP.md``.

De twee overgebleven documenten moeten publiek blijven: het portaal opent ze met
een gewone ``<a href="/GOOGLE-SETUP.md">`` en stuurt daar geen token bij. Dat kan
alleen zolang er geen echte waarden in staan, en dat bewaakt de tweede test.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PUBLIEK = ("GOOGLE-SETUP", "MICROSOFT-SETUP")
INTERN = ("RENDER-SETUP", "DEPLOYMENT", "IOS_BUILD_GUIDE")


@pytest.mark.parametrize("doc", INTERN)
def test_interne_docs_zijn_niet_publiek(client, doc):
    assert client.get(f"/{doc}.md").status_code == 404


@pytest.mark.parametrize("doc", PUBLIEK)
def test_integratiehandleidingen_blijven_bereikbaar(client, doc):
    r = client.get(f"/{doc}.md")
    assert r.status_code == 200, r.text
    assert r.text.strip()


@pytest.mark.parametrize("doc", PUBLIEK)
def test_publieke_docs_bevatten_geen_echte_waarden(doc):
    """Een env-regel in een publiek document mag geen credential bevatten.

    Placeholders (``<...>``, ``je-``, ``your-``, leeg), URL's en korte literals
    zoals ``MS_OAUTH_TENANT=common`` zijn prima. Wat overblijft is een lange,
    ondoorzichtige waarde — en dat is precies hoe een sleutel eruitziet.
    """
    tekst = (REPO / f"{doc}.md").read_text(encoding="utf-8")
    verdacht = []
    for regel in tekst.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]{3,})=(.*)$", regel)
        if not m:
            continue
        waarde = m.group(2).strip()
        if not waarde or waarde.startswith(("<", "je-", "your-", "http://", "https://", "mailto:")):
            continue
        if len(waarde) < 16:
            continue
        verdacht.append(m.group(1))
    assert not verdacht, f"{doc}.md lijkt echte waarden te bevatten: {verdacht}"


def test_render_setup_bevat_geen_vapid_sleutel():
    """Het document blijft bestaan voor eigen gebruik, maar zonder sleutels."""
    tekst = (REPO / "RENDER-SETUP.md").read_text(encoding="utf-8")
    for regel in tekst.splitlines():
        if regel.startswith("VAPID_PRIVATE_KEY="):
            assert regel.split("=", 1)[1].strip().startswith("<"), regel
