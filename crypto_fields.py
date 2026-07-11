"""App-level veld-encryptie voor gevoelige tokens (OAuth) — I7.

Probleem: OAuth access/refresh-tokens stonden als **plaintext** in de DB, terwijl
de compliance-pagina app-level encryptie suggereert. Een DB-dump of read-only-lek
zou dan direct bruikbare Google/Microsoft-tokens prijsgeven.

Oplossing: een SQLAlchemy ``TypeDecorator`` (``EncryptedText``) die de waarde
transparant versleutelt bij schrijven en ontsleutelt bij lezen (Fernet =
AES-128-CBC + HMAC-SHA256). Alle bestaande lees/schrijf-plekken blijven
ongewijzigd.

Sleutelbeheer: afgeleid van ``SECRET_KEY`` (al gezet op Render) zodat er geen
extra secret beheerd hoeft te worden; een expliciete ``TOKEN_ENCRYPTION_KEY``
heeft voorrang als die gezet is. Backwards-compat: bestaande **plaintext**-
waarden (zonder prefix) blijven leesbaar en worden bij de eerstvolgende write
versleuteld. Een onleesbare waarde (bv. na key-rotatie) wordt ruw teruggegeven
i.p.v. een crash — de OAuth-refresh/herauth lost dat dan vanzelf op.
"""
from __future__ import annotations
import base64
import hashlib
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, Text

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    secret = (os.getenv("TOKEN_ENCRYPTION_KEY")
              or os.getenv("SECRET_KEY")
              or "dev-only-not-for-production")
    # Leid een stabiele 32-byte Fernet-sleutel af; domein-prefix scheidt deze
    # van eventuele andere SECRET_KEY-afgeleiden.
    digest = hashlib.sha256(("fieldops-token-enc:" + secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_str(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return value
    if value.startswith(_PREFIX):
        return value  # al versleuteld → idempotent
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_str(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return value
    if not value.startswith(_PREFIX):
        return value  # legacy plaintext — backwards compat
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return value  # onleesbaar (bv. key-rotatie) → ruw terug i.p.v. crashen


class EncryptedText(TypeDecorator):
    """TEXT-kolom die transparant versleuteld wordt opgeslagen (Fernet)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_str(value)

    def process_result_value(self, value, dialect):
        return decrypt_str(value)
