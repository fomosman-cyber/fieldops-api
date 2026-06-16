"""Tests voor het melding-thumbnail-endpoint (foto's in de meldingen-lijst).

De lijst stuurt geen base64 meer mee (perf); de thumbnail wordt lazy per
zichtbare rij geladen via GET /api/meldingen/{id}/thumbnail (Pillow-resize).
"""
import base64
import io

from PIL import Image

from database import SessionLocal
from models import Melding, Organization, User, SubscriptionPlan, AccountStatus, UserRole
from auth import hash_password
from tests.conftest import auth


def _img_data_url(color=(200, 100, 50)):
    img = Image.new("RGB", (12, 12), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _melding(org, admin_user, *, photo=True):
    db = SessionLocal()
    try:
        m = Melding(title="Foto-melding", organization_id=org.id,
                    created_by=admin_user.id,
                    photo_url=_img_data_url() if photo else None)
        db.add(m); db.commit(); db.refresh(m)
        return m.id
    finally:
        db.close()


def test_thumbnail_returns_resized_jpeg(client, org, admin_user):
    mid = _melding(org, admin_user, photo=True)
    r = client.get(f"/api/meldingen/{mid}/thumbnail", headers=auth(admin_user))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    # Echt een JPEG (SOI-marker) en niet leeg
    assert r.content[:2] == b"\xff\xd8"
    # Past binnen de 96px-thumbnail
    img = Image.open(io.BytesIO(r.content))
    assert max(img.size) <= 96


def test_thumbnail_404_without_photo(client, org, admin_user):
    mid = _melding(org, admin_user, photo=False)
    r = client.get(f"/api/meldingen/{mid}/thumbnail", headers=auth(admin_user))
    assert r.status_code == 404


def test_thumbnail_cross_org_blocked(client, org, admin_user):
    mid = _melding(org, admin_user, photo=True)
    # Andere org/gebruiker mag de thumbnail niet zien
    db = SessionLocal()
    try:
        other = Organization(name="OtherOrg", plan=SubscriptionPlan.PROFESSIONAL,
                             status=AccountStatus.ACTIVE, max_users=10)
        db.add(other); db.flush()
        u = User(email="intruder@other.nl", hashed_password=hash_password("x"),
                 first_name="In", last_name="Truder", role=UserRole.ADMIN,
                 is_org_admin=True, organization_id=other.id)
        db.add(u); db.commit(); db.refresh(u)
        intruder = u
    finally:
        db.close()
    r = client.get(f"/api/meldingen/{mid}/thumbnail", headers=auth(intruder))
    assert r.status_code == 404
