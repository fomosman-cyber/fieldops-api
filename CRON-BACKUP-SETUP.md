# DB-Backup naar S3 — setup-handleiding

Dagelijkse automatische snapshots van Postgres-DB naar EU-region S3-bucket. Voor disaster-recovery + ISO 27001-traject.

## Architectuur

```
Render Cron Job (03:00 NL)  ─►  python -m backup_service  ─►  pg_dump | gzip
                                       │
                                       ▼
                              S3 (eu-central-1, SSE-AES256)
                              s3://fieldops-backups/backups/fieldops/
                              fieldops-YYYY-MM-DD-HHMMSS.sql.gz
                                       │
                                       ▼
                              Lifecycle-policy: 90 dagen retentie
                              (90+ = archief naar Glacier, 1 jaar = delete)
```

## Stap 1 — Maak S3-bucket aan

In AWS Console (of bij Wasabi/Backblaze B2 als budget-vriendelijker alternatief):

1. **Bucket-naam**: `fieldops-backups-eu-central-1` (of vergelijkbaar)
2. **Region**: `eu-central-1` (Frankfurt — EU GDPR-compliant)
3. **Encryption**: SSE-S3 (AES-256) — default aan
4. **Block public access**: aan
5. **Versioning**: aan (extra bescherming tegen accidental delete)

## Stap 2 — IAM-user met restricted scope

In AWS IAM:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "s3:PutObject",
      "s3:GetObject",
      "s3:ListBucket",
      "s3:DeleteObject"
    ],
    "Resource": [
      "arn:aws:s3:::fieldops-backups-eu-central-1",
      "arn:aws:s3:::fieldops-backups-eu-central-1/*"
    ]
  }]
}
```

**Geen wildcard-permissions** — alleen deze bucket. Genereer access-key + secret voor deze user.

## Stap 3 — Lifecycle-policy (S3 → bucket → Management)

```
Rule: fieldops-backup-retention
  Filter: prefix = backups/fieldops/
  Transitions:
    - Day 30:  Standard → Standard-IA   (cost-saving, nog snel beschikbaar)
    - Day 90:  Standard-IA → Glacier    (long-term archief)
  Expiration:
    - Day 365: delete                    (1 jaar retentie)
```

90 dagen "snel terug te halen", 1 jaar totaal.

## Stap 4 — Env-vars in Render

Render dashboard → fieldops-api service → Environment:

```
S3_BUCKET                  = fieldops-backups-eu-central-1
S3_REGION                  = eu-central-1
AWS_ACCESS_KEY_ID          = AKIA********************
AWS_SECRET_ACCESS_KEY      = ****************************************
S3_BACKUP_PREFIX           = backups/fieldops  (default — kan weggelaten)
```

Voor S3-compatible providers (Wasabi, Backblaze B2, MinIO):

```
S3_ENDPOINT_URL  = https://s3.eu-central-1.wasabisys.com
```

## Stap 5 — Render Cron Job aanmaken

In Render dashboard → New → Cron Job:

```
Name:          fieldops-backup-daily
Region:        Frankfurt (EU)
Schedule:      0 1 * * *           (01:00 UTC = 02:00 NL winter / 03:00 NL zomer)
Command:       cd /opt/render/project/src/fieldops-api && python -m backup_service
Environment:   Link aan dezelfde service-group als fieldops-api
                (zo erft hij alle DB + S3 env-vars)
```

**Plan**: Starter ($1/mnd voor cron). Run-tijd typisch <30 sec voor <1GB DB.

## Stap 6 — Verificatie

### Eenmalig handmatig testen

In Render Shell van fieldops-api service:

```bash
python -m backup_service
```

Verwachte output:

```json
{
  "success": true,
  "bucket": "fieldops-backups-eu-central-1",
  "key": "backups/fieldops/fieldops-2026-05-20-150532.sql.gz",
  "size_bytes": 2456789,
  "size_mb": 2.34
}
```

### Via admin-endpoint (alleen FieldOps super-admin)

```bash
# Status
curl https://portaal.fieldopsapp.nl/api/admin/backup/status \
  -H "Authorization: Bearer $YOUR_TOKEN"

# Trigger
curl -X POST https://portaal.fieldopsapp.nl/api/admin/backup/trigger \
  -H "Authorization: Bearer $YOUR_TOKEN"
```

## Restore-procedure (in geval van nood)

1. Download laatste backup uit S3:
   ```bash
   aws s3 cp s3://fieldops-backups-eu-central-1/backups/fieldops/fieldops-2026-05-20-XXXXXX.sql.gz ./
   ```

2. Decomprimeer + restore:
   ```bash
   gunzip fieldops-2026-05-20-XXXXXX.sql.gz
   psql $DATABASE_URL < fieldops-2026-05-20-XXXXXX.sql
   ```

3. Service herstarten in Render.

## Compliance-checklist

- ✅ **GDPR Art. 32** (security of processing): SSE-AES256 encryption at rest
- ✅ **GDPR Art. 32**: data blijft in EU (eu-central-1 = Frankfurt)
- ✅ **GDPR Art. 30** (records of processing): audit-log van elke backup-actie
- ✅ **ISO 27001 A.12.3** (backup): regelmatige tests + restore-procedure gedocumenteerd
- ✅ **ISO 27001 A.18.1.3** (records protection): 1 jaar retentie + immutable via versioning

## Kosten-inschatting

Voor een typische FieldOps-org (50-200 users, ~500MB DB):

| Item | Maandkosten |
|---|---|
| Render Cron Job | $1 |
| S3 storage (30× 500MB compressed → ~200MB) | $0.01 |
| S3 PUT requests (30/mnd) | $0.00 (binnen free tier) |
| S3 lifecycle-overgangen | $0.00 |
| **Totaal** | **< $2/mnd** |

Voor grotere klanten (5GB+ DB) schaalt het lineair — ruwweg $5/mnd voor 50GB.

## Monitoring

`/api/admin/backup/status` retourneert:

```json
{
  "status": {
    "configured": true,
    "bucket": "fieldops-backups-eu-central-1",
    "last_run_at": "2026-05-20T01:00:00Z",
    "last_success_at": "2026-05-20T01:00:31Z",
    "last_error": null,
    "last_size_bytes": 2456789,
    "last_filename": "backups/fieldops/fieldops-2026-05-20-010000.sql.gz",
    "running": false
  },
  "recent_backups": [...]
}
```

Voor Pingdom/UptimeRobot: HTTP-check op deze endpoint. Als `last_success_at` ouder dan 25 uur is = alarm.

## Wat dit NIET doet

- **Geen point-in-time-recovery (PITR)** — voor dat heb je Postgres WAL-archiving nodig (RPO 1 min). Roadmap.
- **Geen multi-region replication** — alleen eu-central-1. Voor ISO 27001 Tier 4 is multi-region wenselijk.
- **Geen automated restore-test** — handmatig 1×/kwartaal aanbevolen om backups bruikbaar te houden.
- **Geen klant-specifieke export** — dit is een full-DB-dump. Per-org export = GDPR Art. 20 (data portability), apart endpoint.
