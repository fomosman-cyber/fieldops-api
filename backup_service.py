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


def run_backup() -> dict:
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
        return {
            "success": True,
            "bucket": S3_BUCKET,
            "key": key,
            "size_bytes": size,
            "size_mb": round(size / 1024 / 1024, 2),
        }
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
    #   python -m backup_service
    import json as _json
    print("[backup_service] Start backup...")
    result = run_backup()
    print(_json.dumps(result, indent=2))
    raise SystemExit(0 if result.get("success") else 1)
