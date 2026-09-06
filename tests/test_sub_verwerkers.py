"""De sub-verwerkerslijst mag niet achterlopen op de code.

Deze lijst is een keer stil onwaar geworden. Er stond dat de AI-functies lokaal
draaiden en dat er geen klantdata naar een externe AI ging. Dat klopte toen de
fotoanalyse nog een sjabloon was; sinds die echt naar Claude kijkt klopte het
niet meer. Niemand die de tekst had geschreven kwam er nog langs, en zo stond er
maandenlang een onwaarheid in een document dat bedoeld is voor aanbestedingen en
voor de AVG-verantwoording van onze klanten.

Een lijst die met de hand wordt bijgehouden loopt vroeg of laat achter. Deze
test kijkt daarom naar wat de code werkelijk doet: elke externe host die in een
Python-bestand wordt aangeroepen moet terug te vinden zijn bij een sub-verwerker.
Zet iemand er een nieuwe dienst in, dan faalt de suite tot die dienst ook op de
lijst staat.
"""

import re
from pathlib import Path

from routers.compliance_router import SUB_PROCESSORS

REPO = Path(__file__).resolve().parent.parent

# Hosts die geen verwerking namens ons zijn: onze eigen domeinen, documentatie,
# schema-verwijzingen en voorbeelden uit commentaar en tests.
GEEN_VERWERKER = {
    "fieldopsapp.nl", "www.fieldopsapp.nl", "portaal.fieldopsapp.nl",
    "app.fieldopsapp.nl", "localhost", "127.0.0.1",
    "schema.org", "www.schema.org", "example.com", "www.example.com",
    "github.com", "www.github.com", "raw.githubusercontent.com",
    "crow.nl", "www.crow.nl", "nen.nl", "www.nen.nl",
    "sqlalche.me", "errors.pydantic.dev", "docs.pytest.org",
    "python.org", "www.python.org", "fastapi.tiangolo.com",
    "dashboard.render.com", "console.cloud.google.com", "portal.azure.com",
    # Deep-links die een organisatie zelf instelt: wij roepen ze niet aan, de
    # gebruiker klikt erop. Ze staan in de code als voorbeeld in een sjabloon.
    "viewer.geovisia.com", "www.gbiworld.nl", "www.digigo.nl",
    "linear.app", "docs.github.com",
}

HOST = re.compile(r"https://([a-z0-9][a-z0-9.-]*\.[a-z]{2,})", re.I)


def _documentatie_hosts() -> set[str]:
    """De privacyverklaringen waar we naar linken.

    Die staan in deze lijst zelf en zijn dus geen aanroep maar een verwijzing;
    ze automatisch overslaan scheelt handmatig onderhoud aan de uitzonderingen.
    """
    uit = set()
    for p in SUB_PROCESSORS:
        m = HOST.search(p.get("policy_url") or "")
        if m:
            uit.add(m.group(1).lower())
    return uit


def _verklaarde_hosts() -> set[str]:
    uit = set()
    for p in SUB_PROCESSORS:
        for h in (p.get("hosts") or []):
            uit.add(h.lower())
    return uit


def _gedekt(host: str, verklaard: set[str]) -> bool:
    """Een host is gedekt als hij gelijk is aan of onder een verklaarde host valt.

    Zo dekt `amazonaws.com` ook `mijn-bucket.s3.eu-central-1.amazonaws.com`, en
    `ingest.sentry.io` ook de regionale varianten.
    """
    host = host.lower()
    return any(host == v or host.endswith("." + v) for v in verklaard)


def _python_bestanden():
    for p in REPO.rglob("*.py"):
        s = str(p).replace("\\", "/")
        if "/tests/" in s or "__pycache__" in s or "/wt-" in s:
            continue
        yield p


def test_elke_sub_verwerker_heeft_hosts():
    """Zonder host kan deze test niets controleren, en dan glipt er iets door."""
    zonder = [p["name"] for p in SUB_PROCESSORS if not p.get("hosts")]
    assert zonder == [], f"sub-verwerkers zonder hosts: {zonder}"


def test_elke_uitgaande_host_staat_op_de_lijst():
    """De kern: wat de code aanroept, hoort verklaard te zijn."""
    verklaard = _verklaarde_hosts()
    documentatie = _documentatie_hosts()
    onbekend: dict[str, set[str]] = {}

    for pad in _python_bestanden():
        try:
            tekst = pad.read_text(encoding="utf-8")
        except Exception:
            continue
        for host in HOST.findall(tekst):
            host = host.lower().rstrip(".")
            if (host in GEEN_VERWERKER or host in documentatie
                    or _gedekt(host, verklaard)):
                continue
            onbekend.setdefault(host, set()).add(pad.name)

    assert not onbekend, (
        "Deze hosts worden aangeroepen maar staan bij geen enkele sub-verwerker. "
        "Zet ze op de lijst in compliance_router.SUB_PROCESSORS, of voeg ze toe "
        "aan GEEN_VERWERKER als er geen persoonsgegevens heen gaan: "
        + ", ".join(f"{h} ({', '.join(sorted(b))})" for h, b in sorted(onbekend.items()))
    )


def test_anthropic_staat_erop_zolang_de_fotoanalyse_bestaat():
    """De regressie die deze test bestaat te voorkomen.

    Zolang inspections.py naar api.anthropic.com stuurt, hoort Anthropic op de
    lijst met foto's als gegevenscategorie. Verdwijnt die aanroep ooit, dan mag
    deze test mee verdwijnen -- maar dan bewust.
    """
    stuurt_fotos = "api.anthropic.com" in (REPO / "inspections.py").read_text(
        encoding="utf-8")
    if not stuurt_fotos:
        return

    anthropic = [p for p in SUB_PROCESSORS if "Anthropic" in p["name"]]
    assert anthropic, "inspections.py stuurt naar Anthropic maar die staat niet op de lijst"
    p = anthropic[0]
    assert any("foto" in c.lower() for c in p["data_categories"]), \
        "foto's horen als gegevenscategorie bij Anthropic te staan"
    assert "buiten de EER" in (p.get("opmerking") or ""), \
        "doorgifte buiten de EER hoort expliciet vermeld"


def test_geen_claim_meer_dat_er_niets_naar_ai_gaat():
    """De onwaarheid die dit hele bestand heeft veroorzaakt.

    In de marketingdocumentatie stond dat AI-features lokaal draaien en dat er
    geen klantdata naar een externe AI gaat. Zolang de fotoanalyse echt naar
    Claude kijkt, mag die zin nergens meer staan.
    """
    kandidaten = [REPO.parent / "marketing" / "compliance" / "03-sub-processor-lijst.md"]
    for pad in kandidaten:
        if not pad.exists():
            continue
        tekst = pad.read_text(encoding="utf-8").lower()
        assert "geen klantdata naar third-party ai" not in tekst, (
            f"{pad.name} beweert nog dat er geen klantdata naar een externe AI gaat, "
            "terwijl inspections.py foto's naar Anthropic stuurt")


def test_partijen_buiten_de_eer_dragen_een_grondslag():
    """Doorgifte naar buiten de EER mag, maar niet zonder mechanisme."""
    binnen_eer = ("nl", "eu", "nederland", "netherlands", "frankfurt",
                  "ierland", "ireland", "microsoft eu data boundary")
    for p in SUB_PROCESSORS:
        locatie = (p.get("data_location") or "").lower()
        if any(x in locatie for x in binnen_eer):
            continue
        opmerking = (p.get("opmerking") or "").lower()
        assert p.get("scc_in_place") or "adequaatheidsbesluit" in opmerking \
            or "geen verwerkersovereenkomst" in opmerking, \
            (f"{p['name']} verwerkt buiten de EER ({p['data_location']}) zonder "
             "modelcontractbepalingen of adequaatheidsbesluit")
