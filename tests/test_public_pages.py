"""Smoke-tests voor publieke HTML-pagina's (geen login vereist).

Vangt o.a. een ontbrekende of onleesbare template-file in de deploy: de routes
lezen het bestand bij elke request, dus een verkeerd gekopieerde template zou
hier meteen een 500 geven.
"""


def test_releasenotes_page_empty(client):
    # Pagina blijft bestaan maar is tijdelijk leeggehaald (pre-launch).
    r = client.get("/releasenotes")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Nog geen release notes" in r.text


def test_handleiding_page(client):
    r = client.get("/handleiding")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_prijzen_page_removed(client):
    # Prijzenpagina is op verzoek verwijderd -> 404.
    r = client.get("/prijzen")
    assert r.status_code == 404
