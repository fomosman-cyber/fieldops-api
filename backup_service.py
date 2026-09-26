"""DB-backup naar S3-compatibele bucket.

Doel: dagelijkse automatische snapshots van de Postgres-DB naar een
EU-region S3-bucket. Voor disaster-recovery + ISO 27001-traject.

Twee operating modes:

1. **Automatisch dagelijks** via Render Cron Job (zie CRON-BACKUP-SETUP.md):
   - Render-job triggert deze module elke nacht
   - Connectie naar DATABASE_URL, pg_dump output
   - Upload naar s3://$S3_BUCKET/backups/fieldops-YYYY-MM-DD.sql.gz
   - Retentie: 90 dagen automatisch via S3 lifecycle-policy

2. **Manual trigger** via super-admin endpoint:
   - POST /api/admin/backup/trigger (alleen FieldOps-org admin)
   - Sync uitgevoerd in background-task
   - Status via GET /api/admin/backup/status

Env-vars vereist:
  S3_BUCKET                S3-bucket-naam (bv. 'fieldops-backups-eu-central-1')
  S3_REGION                AWS-region (default 'eu-central-1' = Frankfurt)
  AWS_ACCESS_KEY_ID        IAM key (read+write op bucket alleen)
  AWS_SECRET_ACCESS_KEY    bijbehorende secret
  DATABASE_URL             Postgres URL (al gezet)
  S3_ENDPOINT_URL          optioneel — voor S3-compatible (Wasabi, MinIO, etc.)

Zonder env-vars: backup-functies returnen 'skipped' status; geen fout.
"""
from __future__ import annotations
import os
import gzip
import io
import json
import subprocess
import shutil
from datetime import datetime, timezone


S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_REGION = os.getenv("S3_REGION", "eu-central-1")  # Frankfurt — EU-compliant
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", None)
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Backup-prefix in bucket
BACKUP_PREFIX = os.getenv("S3_BACKUP_PREFIX", "backups/fieldops")

# Wekelijks ook een kopie in Google Drive, in een map die de eigenaar zelf
# kan openen. Een back-up die alleen in dezelfde wolk staat als de server, is
# geen back-up tegen een fout account of een verlopen kaart.
#   GOOGLE_DRIVE_SA_JSON   de sleutel van een serviceaccount (de hele JSON)
#   GOOGLE_DRIVE_MAP_ID    de map waarin hij mag schrijven (gedeeld met dat
#                          serviceaccount-e-mailadres)
#   GOOGLE_DRIVE_BEWAAR    hoeveel wekelijkse kopieën blijven staan
DRIVE_SA_JSON = os.getenv("GOOGLE_DRIVE_SA_JSON", "")
DRIVE_MAP_ID = os.getenv("GOOGLE_DRIVE_MAP_ID", "")
DRIVE_BEWAAR = int(os.getenv("GOOGLE_DRIVE_BEWAAR", "8"))

# Globale status voor /status endpoint (in-memory, niet persistent)
_LAST_BACKUP_STATUS: dict = {
    "last_run_at": None,
    "last_success_at": None,
    "last_error": None,
    "last_size_bytes": 0,
    "last_filename": None,
    "running": False,
}


def is_configured() -> bool:
    """Backup is alleen actief als S3 + AWS-credentials zijn gezet."""
    return bool(S3_BUCKET and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and DATABASE_URL)


def get_status() -> dict:
    """Status voor monitoring/health-check."""
    return {
        "configured": is_configured(),
        "bucket": S3_BUCKET if S3_BUCKET else None,
        "region": S3_REGION,
        **_LAST_BACKUP_STATUS,
    }


def _parse_postgres_url(url: str) -> dict:
    """Parse postgres://user:pass@host:port/dbname naar dict voor pg_dump env."""
    import re
    m = re.match(r"postgres(?:ql)?://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/([^?]+)", url)
    if not m:
        raise ValueError("DATABASE_URL niet in verwacht postgres://user:pass@host/db formaat")
    return {
        "user": m.group(1),
        "password": m.group(2),
        "host": m.group(3),
        "port": m.group(4) or "5432",
        "database": m.group(5),
    }


def _dump_postgres_to_bytes() -> bytes:
    """Dump Postgres naar gzip-gecomprimeerde bytes via pg_dump.

    pg_dump moet beschikbaar zijn op het systeem (Render heeft 'em standaard).
    """
    if not shutil.which("pg_dump"):
        raise RuntimeError("pg_dump niet beschikbaar — install postgresql-client")
    pg = _parse_postgres_url(DATABASE_URL)
    env = os.environ.copy()
    env["PGPASSWORD"] = pg["password"]
    cmd = [
        "pg_dump",
        "--host=" + pg["host"],
        "--port=" + pg["port"],
        "--username=" + pg["user"],
        "--dbname=" + pg["database"],
        "--no-owner",
        "--no-acl",
        "--no-comments",
        "--format=plain",
    ]
    result = subprocess.run(
        cmd, env=env, capture_output=True, check=False, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pg_dump faalde (exit {result.returncode}): " + result.stderr.decode("utf-8", errors="replace")[:500]
        )
    # gzip in-memory
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        gz.write(result.stdout)
    return buf.getvalue()


def _sqlite_backup_to_bytes(sqlite_path: str = "fieldops.db") -> bytes:
    """Fallback voor lokale dev (SQLite). Snapshot de .db file gzipped."""
    if not os.path.exists(sqlite_path):
        raise RuntimeError(f"SQLite-file niet gevonden: {sqlite_path}")
    with open(sqlite_path, "rb") as f:
        raw = f.read()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        gz.write(raw)
    return buf.getvalue()


def drive_is_configured() -> bool:
    """Alleen met een serviceaccount én een map waarin hij mag schrijven."""
    return bool(DRIVE_SA_JSON and DRIVE_MAP_ID)


def _drive_token() -> str:
    """Een toegangstoken voor het serviceaccount. Geen Google-bibliotheek
    nodig: een ondertekende claim is genoeg."""
    import time

    import httpx
    from jose import jwt

    sa = json.loads(DRIVE_SA_JSON)
    nu = int(time.time())
    claim = {
        "iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/drive.file",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": nu,
        "exp": nu + 3600,
    }
    assertie = jwt.encode(claim, sa["private_key"], algorithm="RS256")
    antwoord = httpx.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertie,
    })
    antwoord.raise_for_status()
    return antwoord.json()["access_token"]


def naar_drive(inhoud: bytes, naam: str) -> dict:
    """Eén bestand in de Drive-map zetten, en oude kopieën opruimen."""
    import uuid

    import httpx

    if not drive_is_configured():
        return {"success": False, "skipped": True,
                "reason": "GOOGLE_DRIVE_SA_JSON en GOOGLE_DRIVE_MAP_ID moeten gezet zijn"}
    try:
        token = _drive_token()
        grens = "fieldops-" + uuid.uuid4().hex
        meta = json.dumps({"name": naam, "parents": [DRIVE_MAP_ID]})
        lichaam = (
            f"--{grens}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{grens}\r\nContent-Type: application/gzip\r\n\r\n"
        ).encode("utf-8") + inhoud + f"\r\n--{grens}--\r\n".encode("utf-8")
        antwoord = httpx.post(
            "https://www.googleapis.com/upload/drive/v3/files"
            "?uploadType=multipart&supportsAllDrives=true&fields=id,name",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": f"multipart/related; boundary={grens}"},
            content=lichaam, timeout=900)
        antwoord.raise_for_status()
        bestand = antwoord.json()
        opgeruimd = _drive_opruimen(token)
        return {"success": True, "id": bestand.get("id"), "naam": bestand.get("name"),
                "size_bytes": len(inhoud), "opgeruimd": opgeruimd}
    except Exception as e:  # noqa: BLE001 — een back-up die faalt, moet het zeggen
        return {"success": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}


def _drive_opruimen(token: str) -> int:
    """Meer dan DRIVE_BEWAAR kopieën: de oudste weg. Anders groeit de Drive
    van de eigenaar ongemerkt vol."""
    import httpx

    kop = {"Authorization": f"Bearer {token}"}
    lijst = httpx.get("https://www.googleapis.com/drive/v3/files", headers=kop, timeout=60, params={
        "q": f"'{DRIVE_MAP_ID}' in parents and trashed = false and name contains 'fieldops-'",
        "orderBy": "createdTime desc", "fields": "files(id,name,createdTime)", "pageSize": 100,
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
    })
    lijst.raise_for_status()
    bestanden = lijst.json().get("files", [])
    weg = 0
    for bestand in bestanden[DRIVE_BEWAAR:]:
        verwijderd = httpx.delete(f"https://www.googleapis.com/drive/v3/files/{bestand['id']}",
                                  headers=kop, timeout=60, params={"supportsAllDrives": "true"})
        if verwijderd.status_code in (200, 204):
            weg += 1
    return weg


def run_backup(ook_naar_drive: bool = False) -> dict:
    """Voer een backup uit. Returns status-dict.

    Atomair: bij elke fout krijg je 'success': False + error-bericht.
    """
    global _LAST_BACKUP_STATUS
    _LAST_BACKUP_STATUS["running"] = True
    _LAST_BACKUP_STATUS["last_run_at"] = datetime.now(timezone.utc).isoformat()

    try:
        if not is_configured():
            _LAST_BACKUP_STATUS["last_error"] = "S3-credentials niet geconfigureerd"
            _LAST_BACKUP_STATUS["running"] = False
            return {
                "success": False,
                "skipped": True,
                "reason": "S3_BUCKET + AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY moeten gezet zijn",
            }

        # Dump
        if DATABASE_URL.startswith(("postgres://", "postgresql://")):
            blob = _dump_postgres_to_bytes()
            ext = "sql.gz"
        else:
            blob = _sqlite_backup_to_bytes()
            ext = "db.gz"

        size = len(blob)

        # Upload
        try:
            import boto3
        except ImportError:
            raise RuntimeError("boto3 niet geïnstalleerd — voeg toe aan requirements.txt")

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        key = f"{BACKUP_PREFIX}/fieldops-{ts}.{ext}"

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
            ContentType="application/gzip",
            ContentEncoding="gzip",
            ServerSideEncryption="AES256",  # SSE-S3 default encryption
            Metadata={
                "source": "fieldops-portaal",
                "ts": ts,
            },
        )

        _LAST_BACKUP_STATUS.update({
            "last_success_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
            "last_size_bytes": size,
            "last_filename": key,
            "running": False,
        })
        uit = {
            "success": True,
            "bucket": S3_BUCKET,
            "key": key,
            "size_bytes": size,
            "size_mb": round(size / 1024 / 1024, 2),
        }
        if ook_naar_drive:
            uit["drive"] = naar_drive(blob, f"fieldops-{ts}.{ext}")
            if not uit["drive"].get("success") and not uit["drive"].get("skipped"):
                # De kopie in de bucket staat er; dat de tweede plek faalde,
                # mag niet stilletjes goed lijken.
                uit["success"] = False
                uit["error"] = "kopie naar Drive mislukt: " + str(uit["drive"].get("error"))
        return uit
    except Exception as e:
        err_msg = f"{type(e).__name__}: {str(e)[:300]}"
        _LAST_BACKUP_STATUS["last_error"] = err_msg
        _LAST_BACKUP_STATUS["running"] = False
        return {"success": False, "error": err_msg}


def list_recent_backups(limit: int = 30) -> list:
    """Lijst recent uploaded backups (voor admin-dashboard)."""
    if not is_configured():
        return []
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
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=BACKUP_PREFIX, MaxKeys=limit)
        items = resp.get("Contents", []) or []
        items.sort(key=lambda x: x.get("LastModified"), reverse=True)
        return [{
            "key": x["Key"],
            "size_bytes": x["Size"],
            "size_mb": round(x["Size"] / 1024 / 1024, 2),
            "last_modified": x["LastModified"].isoformat() if x.get("LastModified") else None,
        } for x in items[:limit]]
    except Exception as e:
        print(f"[backup_service] list failed: {e}")
        return []


if __name__ == "__main__":
    # CLI-modus voor Render Cron Job:
    #   python -m backup_service           nachtelijk, naar de bucket
    #   python -m backup_service --drive   wekelijks, ook naar Google Drive
    import sys
    import json as _json
    naar_drive_ook = "--drive" in sys.argv
    print("[backup_service] Start backup..." + (" (ook naar Drive)" if naar_drive_ook else ""))
    result = run_backup(ook_naar_drive=naar_drive_ook)
    print(_json.dumps(result, indent=2))
    raise SystemExit(0 if result.get("success") else 1)
