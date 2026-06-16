"""Tests voor voor- én na-foto bij meldingen (must).

Beide foto's (photo_url = vóór/inspectie, photo_after_url = na uitvoering)
moeten via create én update opgeslagen en teruggegeven worden, met de
has_photo/has_photo_after-vlaggen in de light-lijst.
"""
from tests.conftest import auth

_IMG = "data:image/jpeg;base64,/9j/AAAQSkZJRg=="


def test_create_stores_both_photos(client, org, admin_user):
    r = client.post("/api/meldingen/", headers=auth(admin_user), json={
        "title": "Voor + na",
        "photo_url": _IMG,
        "photo_after_url": _IMG,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["photo_url"].startswith("data:image/")
    assert body["photo_after_url"].startswith("data:image/")
    assert body["has_photo"] is True
    assert body["has_photo_after"] is True


def test_list_light_flags_both_photos(client, org, admin_user):
    client.post("/api/meldingen/", headers=auth(admin_user), json={
        "title": "Met beide", "photo_url": _IMG, "photo_after_url": _IMG,
    })
    m = client.get("/api/meldingen/", headers=auth(admin_user)).json()[0]
    # Light: geen base64 in de lijst, wél de vlaggen
    assert m["photo_url"] is None and m["photo_after_url"] is None
    assert m["has_photo"] is True and m["has_photo_after"] is True


def test_update_adds_after_photo(client, org, admin_user):
    r = client.post("/api/meldingen/", headers=auth(admin_user),
                    json={"title": "Alleen voor", "photo_url": _IMG})
    mid = r.json()["id"]
    assert r.json()["has_photo_after"] is False
    r = client.put(f"/api/meldingen/{mid}", headers=auth(admin_user),
                   json={"photo_after_url": _IMG})
    assert r.status_code == 200, r.text
    assert r.json()["photo_after_url"].startswith("data:image/")
    assert r.json()["has_photo_after"] is True
