"""Het versie-endpoint.

Dit bestaat om één reden: van buitenaf kunnen vaststellen of een merge ook echt
is uitgerold. Zonder dit is een deploy controleren giswerk -- je zoekt een
toevallig zichtbaar neveneffect en hoopt dat het klopt.

Bewust publiek. Een commit-hash verraadt niets bij een publieke repository; wat
wél gevoelig is hoort er dus ook niet in, en dat bewaakt de laatste test.
"""


def test_versie_is_publiek(client):
    """Zonder token bruikbaar, anders kun je hem niet gebruiken om te controleren."""
    r = client.get("/api/version")
    assert r.status_code == 200, r.text


def test_versie_meldt_de_starttijd(client):
    d = client.get("/api/version").json()
    assert d["gestart_op"]
    # Twee aanroepen geven dezelfde starttijd; anders meet je niets.
    assert client.get("/api/version").json()["gestart_op"] == d["gestart_op"]


def test_commit_is_none_zonder_render(client, monkeypatch):
    """Lokaal draait er geen Render, en dan hoort er geen nep-hash te staan."""
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    d = client.get("/api/version").json()
    assert d["commit"] is None and d["branch"] is None


def test_commit_wordt_afgekapt_maar_ook_volledig_gegeven(client, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123456789abcdef0123456789abcdef01234567")
    d = client.get("/api/version").json()
    assert d["commit"] == "0123456789ab"
    assert d["commit_volledig"] == "0123456789abcdef0123456789abcdef01234567"


def test_geen_geheimen_in_het_antwoord(client, monkeypatch):
    """Wat hier per ongeluk bij komt, staat meteen op straat."""
    monkeypatch.setenv("SECRET_KEY", "geheim-mag-niet-lekken")
    monkeypatch.setenv("MOLLIE_API_KEY", "live_mag-niet-lekken")
    tekst = client.get("/api/version").text
    assert "geheim-mag-niet-lekken" not in tekst
    assert "live_mag-niet-lekken" not in tekst
    # Alleen deze vier sleutels, zodat uitbreiden een bewuste keuze blijft.
    assert set(client.get("/api/version").json()) == {
        "commit", "commit_volledig", "branch", "gestart_op"}
