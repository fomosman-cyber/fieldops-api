"""Het portaal mag geen asset-endpoints aanroepen die niet bestaan.

Aanleiding: de bulk-actie "Archiveren" riep `PATCH /api/assets/{id}/archive`
aan. Dat endpoint heeft nooit bestaan -- assets_router kent alleen DELETE met
`?hard=` als schakelaar -- dus elke asset gaf 404 en de gebruiker kreeg
"0 gearchiveerd (N mislukt)". Niets ving dat af, want het portaal wordt niet
door de backend-tests aangeraakt.

Tegelijk beloofde de bulk-actie "Verwijderen" in het dialoogvenster
"permanent verwijderen -- dit kan niet ongedaan worden gemaakt", maar riep
DELETE aan ZONDER `?hard=true`. Dat is een soft-archive: de assets verdwenen
uit de lijst, dus het leek gelukt, terwijl de data er nog stond. Dat is de
gevaarlijkste van de twee, want je denkt dat je iets hebt opgeruimd.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
PORTAAL = (_ROOT / "templates" / "portaal.html").read_text(encoding="utf-8")
ASSETS_ROUTER = (_ROOT / "routers" / "assets_router.py").read_text(encoding="utf-8")


def _router_paden() -> set:
    """De padsuffixen die assets_router daadwerkelijk aanbiedt."""
    paden = set()
    for methode, pad in re.findall(r'@router\.(get|post|put|patch|delete)\("([^"]*)"', ASSETS_ROUTER):
        paden.add((methode.upper(), pad))
    return paden


def test_router_heeft_geen_archive_endpoint():
    """Vastleggen waaróm de bulk-actie stuk was: dit endpoint bestaat niet."""
    paden = {p for _, p in _router_paden()}
    assert "/{asset_id}/archive" not in paden, (
        "Er is nu wel een archive-endpoint; werk deze test en het portaal bij.")


def test_portaal_roept_geen_archive_endpoint_aan():
    assert "/archive'" not in PORTAAL, (
        "Het portaal roept een /archive-endpoint aan dat niet bestaat. "
        "Soft-archiveren gaat via DELETE zonder ?hard=true.")


def test_bulk_verwijderen_verwijdert_echt():
    """De bulk-actie die 'permanent' belooft moet ?hard=true meesturen.

    Zonder die parameter archiveert de backend alleen, en dan liegt het
    dialoogvenster tegen de gebruiker.
    """
    # Het blok van de bulk-verwijderactie: vanaf de melding tot de api-aanroep.
    blok = re.search(
        r"title:\s*ids\.length \+ ' assets verwijderen\?'.*?api\('DELETE',\s*'/api/assets/'\s*\+\s*id([^)]*)\)",
        PORTAAL, re.DOTALL)
    assert blok, "bulk-verwijderactie niet gevonden — is de knop hernoemd?"
    assert "hard=true" in blok.group(1), (
        "Bulk 'Verwijderen' roept DELETE aan zonder ?hard=true en archiveert dus "
        "alleen, terwijl het dialoogvenster permanent verwijderen belooft.")


def test_bulk_archiveren_archiveert_en_verwijdert_niet():
    blok = re.search(
        r"title:\s*ids\.length \+ ' assets archiveren\?'.*?api\('DELETE',\s*'/api/assets/'\s*\+\s*id([^)]*)\)",
        PORTAAL, re.DOTALL)
    assert blok, "bulk-archiveeractie niet gevonden — is de knop hernoemd?"
    assert "hard" not in blok.group(1), (
        "Bulk 'Archiveren' stuurt ?hard=true mee en verwijdert dus permanent.")


def test_enkelvoudige_knoppen_blijven_correct():
    """De losse knoppen deden het al goed; die mogen niet meeschuiven."""
    assert "api('DELETE', '/api/assets/'+id+'?hard=true')" in PORTAAL, \
        "deleteAssetHard moet permanent verwijderen"
    assert "api('DELETE', '/api/assets/'+id)" in PORTAAL, \
        "archiveAsset moet soft-archiveren"


def test_alle_asset_aanroepen_bestaan_in_de_router():
    """Elk vast padstuk dat het portaal onder /api/assets/ aanroept moet een
    route zijn. Dit is de guard die de archive-bug had gevangen."""
    bekende_suffixen = {p.replace("/{asset_id}", "").strip("/")
                        for _, p in _router_paden()}
    bekende_suffixen.discard("")

    # Patronen als: '/api/assets/' + id + '/split'  →  suffix "split"
    fouten = []
    for suffix in re.findall(r"'/api/assets/'\s*\+\s*\w+\s*\+\s*'/([a-z0-9\-/]+)'", PORTAAL):
        kop = suffix.split("?")[0].strip("/")
        if kop and kop not in bekende_suffixen:
            fouten.append(kop)
    assert not fouten, (
        "Het portaal roept asset-endpoints aan die de router niet heeft: "
        + ", ".join(sorted(set(fouten))))
