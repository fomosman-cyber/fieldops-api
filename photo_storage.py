"""Foto-storage offload naar S3.

Probleem: meldingen + inspecties + opleveringen slaan foto's op als
**base64 data-URL in TEXT-kolom**. Dat werkt voor MVP maar:
  - DB groeit ~50KB-1MB per foto → schaalt slecht
  - Backups worden enorm
  - Query-performance lijdt eronder

Oplossing: upload-foto's gaan naar S3, in DB blijft alleen de S3-URL.
**Backwards compat:** bestaande base64 data-URLs blijven werken (frontend
herkent ze als zodanig). Nieuwe uploads gaan naar S3 als geconfigureerd.

Env-vars (hergebruikt van backup_service):
- S3_BUCKET, S3_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
- S3_PHOTO_PREFIX (default 'photos/')
- S3_PHOTO_CDN_BASE (optioneel — CloudFront-URL voor snellere reads)

Zonder env-vars: photos blijven als base64 in DB (backwards compat).
"""
from __future__ import annotations
import os
import base64
import hashlib
import secrets
import re
from datetime import datetime, timezone
from typing import Optional


S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_REGION = os.getenv("S3_REGION", "eu-central-1")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", None)
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
PHOTO_PREFIX = os.getenv("S3_PHOTO_PREFIX", "photos")
PHOTO_CDN_BASE = os.getenv("S3_PHOTO_CDN_BASE", "").rstrip("/")

# Mime → extension mapping voor key-generatie
_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/jpg":  "jpg",
    "image/png":  "png",
    "image/webp": "webp",
    "image/gif":  "gif",
    "image/svg+xml": "svg",
}

# Max size voor offload — boven dit grens upload we, anders laten we base64
# (kleine icoontjes/thumbnails niet onnodig naar S3)
MIN_OFFLOAD_BYTES = int(os.getenv("PHOTO_OFFLOAD_MIN_BYTES", "10240"))  # 10 KB


def is_configured() -> bool:
    """S3 photo-offload is alleen actief als alle credentials gezet zijn."""
    return bool(S3_BUCKET and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)


def is_data_url(value: Optional[str]) -> bool:
    return bool(value and isinstance(value, str) and value.startswith("data:image/"))


def is_s3_url(value: Optional[str]) -> bool:
    """Detect een al-eerder-ge-uploadede S3-URL (om dubbel-upload te voorkomen)."""
    if not value or not isinstance(value, str):
        return False
    return value.startswith("https://") and (
        ".s3." in value or ".s3-" in value or
        (PHOTO_CDN_BASE and value.startswith(PHOTO_CDN_BASE))
    )


def offload_photo(data_url: str, *, organization_id: str, kind: str = "melding") -> Optional[str]:
    """Upload een data:image/...;base64,... naar S3. Returnt public/signed URL.

    Returns None bij fout — caller moet dan de originele data-URL behouden
    (geen data-loss). is_configured() check eerst.
    """
    if not is_configured():
        return None
    if not is_data_url(data_url):
        return None

    # Parse data-URL: "data:image/png;base64,iVBORw0..."
    m = re.match(r"^data:([\w/+-]+);base64,(.+)$", data_url, flags=re.S)
    if not m:
        return None
    mime = m.group(1).lower()
    b64 = m.group(2)
    try:
        blob = base64.b64decode(b64)
    except Exception:
        return None

    # Skip kleine plaatjes (icons/thumbnails) — laat als base64
    if len(blob) < MIN_OFFLOAD_BYTES:
        return None

    ext = _MIME_TO_EXT.get(mime, "bin")
    # Content-hash voor dedup + cache-busting
    sha = hashlib.sha256(blob).hexdigest()[:16]
    ts = datetime.now(timezone.utc).strftime("%Y/%m")
    key = f"{PHOTO_PREFIX}/{kind}/{organization_id}/{ts}/{sha}.{ext}"

    try:
        import boto3
        s3_kwargs = {
            "region_name": S3_REGION,
            "aws_access_key_id": AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": AWS_SECRET_ACCESS_KEY,
        }
        if S3_ENDPOINT_URL:
            s3_kwargs["endpoint_url"] = S3_ENDPOINT_URL
        s3 = boto3.client("s3", **s3_kwargs)
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=blob,
            ContentType=mime,
            CacheControl="public, max-age=31536000, immutable",
            # SSE op bucket-niveau via lifecycle aanbevolen i.p.v. per-object
            Metadata={
                "kind": kind,
                "organization_id": organization_id,
                "sha256_prefix": sha,
            },
        )
        # CDN-URL als gezet, anders direct S3
        if PHOTO_CDN_BASE:
            return f"{PHOTO_CDN_BASE}/{key}"
        if S3_ENDPOINT_URL:
            # S3-compatible (Wasabi, MinIO etc)
            return f"{S3_ENDPOINT_URL.rstrip('/')}/{S3_BUCKET}/{key}"
        return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{key}"
    except Exception as e:
        import sys
        print(f"[photo_storage] offload failed: {e}", file=sys.stderr)
        return None


def maybe_offload(value: Optional[str], *, organization_id: str, kind: str = "melding") -> Optional[str]:
    """Convenience: offload als configureerd + data-URL, anders return as-is.

    Gebruikt door routers bij melding-create/update:
        m.photo_url = maybe_offload(payload.photo_url, organization_id=org_id)
    """
    if not value:
        return value
    if not is_data_url(value):
        return value  # Al een URL of leeg
    if not is_configured():
        return value  # Backwards compat: blijf base64

    s3_url = offload_photo(value, organization_id=organization_id, kind=kind)
    return s3_url if s3_url else value  # Bij upload-fout: behoud base64
