"""OAuth-token-encryptie (audit I7).

Verifieert dat GoogleOAuthToken.access_token/refresh_token versleuteld op de DB
staan (Fernet) en transparant ontsleuteld worden bij ORM-reads, met backwards-
compat voor bestaande plaintext-waarden.
"""

from sqlalchemy import text

from crypto_fields import encrypt_str, decrypt_str
from database import SessionLocal
from models import GoogleOAuthToken


def test_crypto_roundtrip():
    enc = encrypt_str("ya29.super-secret-token")
    assert enc.startswith("enc:v1:")
    assert "super-secret-token" not in enc
    assert decrypt_str(enc) == "ya29.super-secret-token"


def test_crypto_plaintext_passthrough_and_none():
    # Legacy plaintext (geen prefix) blijft leesbaar; None/'' onveranderd.
    assert decrypt_str("legacy-plaintext") == "legacy-plaintext"
    assert encrypt_str(None) is None
    assert encrypt_str("") == ""
    # Idempotent: dubbel encrypten verandert niets.
    enc = encrypt_str("abc")
    assert encrypt_str(enc) == enc


def test_oauth_token_stored_encrypted_but_reads_plain(admin_user):
    db = SessionLocal()
    try:
        tok = GoogleOAuthToken(
            user_id=admin_user.id, organization_id=admin_user.organization_id,
            access_token="plain-access", refresh_token="plain-refresh",
            google_email="x@test.nl",
        )
        db.add(tok)
        db.commit()
        tok_id = tok.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        # Ruwe kolom = versleuteld (geen plaintext op disk)
        raw = db.execute(
            text("SELECT access_token, refresh_token FROM google_oauth_tokens WHERE id=:i"),
            {"i": tok_id},
        ).first()
        assert raw[0].startswith("enc:v1:")
        assert "plain-access" not in raw[0]
        assert raw[1].startswith("enc:v1:")

        # ORM-read ontsleutelt transparant
        obj = db.query(GoogleOAuthToken).filter(GoogleOAuthToken.id == tok_id).first()
        assert obj.access_token == "plain-access"
        assert obj.refresh_token == "plain-refresh"
    finally:
        db.close()
