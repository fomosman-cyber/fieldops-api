"""Wekelijkse kopie van de back-up in Google Drive.

Wat hier vastligt:

1. **Zonder instellingen gebeurt er niets**, en dat staat er ook bij. Geen
   stille "gelukt".
2. **Het bestand gaat in de afgesproken map**, met de naam van de dump.
3. **Oude kopieën gaan weg**: meer dan GOOGLE_DRIVE_BEWAAR laat de Drive van
   de eigenaar ongemerkt vollopen.
4. **Een mislukte kopie maakt de back-up niet geslaagd.** De bucket heeft hem
   wel, maar de tweede plek is de reden dat deze job bestaat.
"""

import json

import pytest

import backup_service as bs


@pytest.fixture
def drive(monkeypatch):
    """Nep-Drive: onthoudt wat er is verstuurd en verwijderd."""
    monkeypatch.setattr(bs, "DRIVE_SA_JSON", json.dumps(
        {"client_email": "fieldops@proef.iam.gserviceaccount.com", "private_key": "-----NEP-----"}))
    monkeypatch.setattr(bs, "DRIVE_MAP_ID", "map-123")
    monkeypatch.setattr(bs, "DRIVE_BEWAAR", 2)
    monkeypatch.setattr(bs, "_drive_token", lambda: "test-token")
    staat = {"geupload": [], "verwijderd": [], "bestanden": [
        {"id": "a", "name": "fieldops-2026-09-20.sql.gz"},
        {"id": "b", "name": "fieldops-2026-09-13.sql.gz"},
        {"id": "c", "name": "fieldops-2026-09-06.sql.gz"},
        {"id": "d", "name": "fieldops-2026-08-30.sql.gz"},
    ]}

    class _Antwoord:
        def __init__(self, data=None, status=200):
            self._data = data or {}
            self.status_code = status

        def json(self):
            return self._data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    def post(url, **kw):
        staat["geupload"].append({"url": url, "body": kw.get("content"), "kop": kw.get("headers")})
        return _Antwoord({"id": "nieuw", "name": "fieldops-nu.sql.gz"})

    def get(url, **kw):
        staat["query"] = kw.get("params", {}).get("q")
        return _Antwoord({"files": staat["bestanden"]})

    def delete(url, **kw):
        staat["verwijderd"].append(url.rsplit("/", 1)[-1])
        return _Antwoord(status=204)

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "delete", delete)
    return staat


def test_zonder_instellingen_gebeurt_er_niets(monkeypatch):
    monkeypatch.setattr(bs, "DRIVE_SA_JSON", "")
    monkeypatch.setattr(bs, "DRIVE_MAP_ID", "")
    assert bs.drive_is_configured() is False
    uit = bs.naar_drive(b"dump", "fieldops.sql.gz")
    assert uit["skipped"] is True and "GOOGLE_DRIVE_SA_JSON" in uit["reason"]


def test_de_kopie_gaat_in_de_afgesproken_map(drive):
    uit = bs.naar_drive(b"de-dump", "fieldops-2026-09-27.sql.gz")
    assert uit["success"] and uit["id"] == "nieuw" and uit["size_bytes"] == len(b"de-dump")
    verstuurd = drive["geupload"][0]
    assert "uploadType=multipart" in verstuurd["url"]
    assert b'"parents": ["map-123"]' in verstuurd["body"]
    assert b"fieldops-2026-09-27.sql.gz" in verstuurd["body"]
    assert b"de-dump" in verstuurd["body"]
    assert verstuurd["kop"]["Authorization"] == "Bearer test-token"


def test_oude_kopieen_gaan_weg(drive):
    uit = bs.naar_drive(b"dump", "fieldops-nieuw.sql.gz")
    # Twee bewaren, dus de twee oudste weg.
    assert uit["opgeruimd"] == 2 and drive["verwijderd"] == ["c", "d"]
    assert "map-123" in drive["query"] and "trashed = false" in drive["query"]


def test_mislukte_kopie_maakt_de_backup_niet_geslaagd(monkeypatch, drive):
    monkeypatch.setattr(bs, "_drive_token", lambda: (_ for _ in ()).throw(RuntimeError("geen toegang")))
    monkeypatch.setattr(bs, "is_configured", lambda: True)
    monkeypatch.setattr(bs, "_dump_postgres_to_bytes", lambda: b"dump")
    monkeypatch.setattr(bs, "DATABASE_URL", "postgresql://x/y")

    class _S3:
        def put_object(self, **kw):
            return {}

    monkeypatch.setitem(__import__("sys").modules, "boto3",
                        type("m", (), {"client": staticmethod(lambda *a, **k: _S3())}))
    uit = bs.run_backup(ook_naar_drive=True)
    assert uit["success"] is False and "Drive" in uit["error"]
    assert uit["drive"]["success"] is False


def test_bewaar_nul_gooit_de_laatste_kopie_niet_weg(drive, monkeypatch):
    """GOOGLE_DRIVE_BEWAAR=0 betekende: alles weg, inclusief de kopie van nu.

    De lijst wordt ná de upload opgehaald, dus bij nul liep de opruiming over
    de hele map. Er blijft altijd minstens één kopie staan.
    """
    monkeypatch.setattr(bs, "DRIVE_BEWAAR", 0)
    uit = bs.naar_drive(b"dump", "fieldops-nieuw.sql.gz")
    assert uit["success"] is True
    # Vier bestaande kopieën, nieuwste eerst: drie weg, de nieuwste blijft.
    assert uit["opgeruimd"] == 3
    assert drive["verwijderd"] == ["b", "c", "d"]
    assert "a" not in drive["verwijderd"]


def test_een_geweigerd_verwijderen_wordt_gemeld(drive, monkeypatch):
    """Een 403 op het opruimen werd stil ingeslikt: `opgeruimd: 0` en verder
    niets, terwijl de Drive ongemerkt volloopt."""
    import httpx

    class _Geweigerd:
        status_code = 403

        def json(self):
            return {}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx, "delete", lambda url, **kw: _Geweigerd())
    uit = bs.naar_drive(b"dump", "fieldops-nieuw.sql.gz")
    # De kopie staat er, dus de back-up is gelukt...
    assert uit["success"] is True
    assert uit["opgeruimd"] == 0
    # ...maar het mislukte opruimen is zichtbaar.
    assert uit["opruimen_mislukt"] == 2
    assert "rechten" in uit["waarschuwing"]


@pytest.mark.parametrize("rauw, verwacht", [
    ("3", 3),
    ("  5  ", 5),
    ("", 8),
    (None, 8),
    ("acht", 8),
    ("8,5", 8),
])
def test_onleesbare_bewaarwaarde_laat_de_module_staan(rauw, verwacht):
    """`int(os.getenv(...))` op moduleniveau liet bij een typefout de hele
    import klappen — en `backup_service` wordt ook geïmporteerd door
    endpoints die niets met Drive te maken hebben."""
    assert bs._bewaar_uit_omgeving(rauw) == verwacht


def test_een_mislukte_lijst_maakt_de_kopie_niet_mislukt(drive, monkeypatch):
    """De upload is al klaar wanneer het opruimen stukloopt.

    Meldde deze functie dan `success: False`, dan gaat de beheerder zoeken naar
    een back-up die er wel staat -- en de volgende run zet er een tweede naast.
    """
    import httpx

    class _Stuk:
        status_code = 500

        def json(self):
            return {}

        def raise_for_status(self):
            raise RuntimeError("HTTP 500")

    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Stuk())
    uit = bs.naar_drive(b"dump", "fieldops-nu.sql.gz")
    assert uit["success"] is True, uit
    assert uit["id"] == "nieuw"
    assert uit["opgeruimd"] == 0
    assert "RuntimeError" in uit["opruimen_fout"]
    assert uit["waarschuwing"]
    assert drive["verwijderd"] == []


def test_een_netwerkfout_bij_opruimen_laat_de_kopie_ook_staan(drive, monkeypatch):
    """Dezelfde grens, andere fout: httpx komt niet eens tot een antwoord."""
    import httpx

    def _weg(url, **kw):
        raise OSError("verbinding weg")

    monkeypatch.setattr(httpx, "get", _weg)
    uit = bs.naar_drive(b"dump", "fieldops-nu.sql.gz")
    assert uit["success"] is True, uit
    assert "OSError" in uit["opruimen_fout"]


def test_een_mislukte_upload_blijft_mislukt(drive, monkeypatch):
    """De andere kant van die grens: hier hoort niets te worden ingeslikt.

    Zonder deze test is de fix hierboven een vrijbrief om straks ook echte
    mislukkingen als geslaagd te melden.
    """
    import httpx

    class _Stuk:
        status_code = 503

        def json(self):
            return {}

        def raise_for_status(self):
            raise RuntimeError("HTTP 503")

    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Stuk())
    uit = bs.naar_drive(b"dump", "fieldops-nu.sql.gz")
    assert uit["success"] is False
    assert "RuntimeError" in uit["error"]
