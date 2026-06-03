"""Tests voor CSV-meldingen-import mét foto's (F5)."""
import base64
import io
import zipfile

from database import SessionLocal
from models import Melding
from tests.conftest import auth

# Geldige 1x1 PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _photos_zip(names):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as zf:
        for n in names:
            zf.writestr(n, _PNG)
    return b.getvalue()


def _import(client, user, csv_text, photos_zip=None):
    files = {"file": ("m.csv", csv_text.encode("utf-8"), "text/csv")}
    if photos_zip is not None:
        files["photos"] = ("photos.zip", photos_zip, "application/zip")
    return client.post("/api/meldingen/import/csv", files=files, headers=auth(user))


def _meldingen(org_id):
    db = SessionLocal()
    try:
        return db.query(Melding).filter(Melding.organization_id == org_id).all()
    finally:
        db.close()


def test_foto_kolom_met_url(client, admin_user):
    csv = "title,foto\nScheur in wegdek,https://example.com/foto1.jpg\n"
    r = _import(client, admin_user, csv)
    assert r.status_code == 200, r.text
    assert r.json()["photos_matched"] == 1
    m = _meldingen(admin_user.organization_id)
    assert len(m) == 1
    assert m[0].photo_url == "https://example.com/foto1.jpg"


def test_foto_uit_zip_wordt_base64(client, admin_user):
    csv = "title,foto\nKuil in fietspad,kuil.png\n"
    r = _import(client, admin_user, csv, _photos_zip(["kuil.png"]))
    assert r.status_code == 200, r.text
    assert r.json()["photos_matched"] == 1
    m = _meldingen(admin_user.organization_id)[0]
    assert m.photo_url.startswith("data:image/png;base64,")


def test_foto_subpad_in_zip_matcht_op_basename(client, admin_user):
    # CSV verwijst naar 'kuil.png'; zip bevat 'fotos/kuil.png' → basename-match.
    csv = "title,foto\nObstakel,kuil.png\n"
    r = _import(client, admin_user, csv, _photos_zip(["fotos/kuil.png"]))
    assert r.status_code == 200, r.text
    assert r.json()["photos_matched"] == 1


def test_foto_ontbreekt_geeft_warning(client, admin_user):
    csv = "title,foto\nLosse tegel,bestaat_niet.jpg\n"
    r = _import(client, admin_user, csv)  # geen zip meegegeven
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["photos_matched"] == 0
    assert any("foto" in w["warning"] for w in body["warnings"])
    assert _meldingen(admin_user.organization_id)[0].photo_url is None


def test_zonder_foto_kolom_werkt_gewoon(client, admin_user):
    r = _import(client, admin_user, "title\nGeen foto hier\n")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 1
    assert body["photos_matched"] == 0
