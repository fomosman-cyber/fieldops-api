"""IoT-sensor data-ingestion endpoint (starter).

Voor kritieke kunstwerken met sensoren (vibratie, doorbuiging,
temperatuur, lichtsterkte, etc.). Sensoren posten readings via
LoRaWAN / NB-IoT gateway → onze ingestion-endpoint slaat ze op en
genereert automatisch een melding bij drempelwaarde-overschrijding.

Dit is een **skelet** — voor productie hebben we nog:
  - SensorReading-model (tijdreeks-tabel) — niet in deze MVP
  - Threshold-regels per sensor-type
  - Alert-pipeline naar push-notificaties

Endpoints:
  POST /api/iot/ingest                Reading binnenkomend (geauth. via API-key)
  GET  /api/iot/sensors               Lijst geregistreerde sensoren
  GET  /api/iot/readings/{sensor_id}  Recent readings van één sensor

Auth: in productie via X-API-Key + per-sensor scope. In MVP via reguliere
JWT-auth voor admins.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import User, Melding
from auth import get_current_user
from audit import log_action

router = APIRouter(prefix="/api/iot", tags=["IoT-sensoren"])


# ─────────────────────────────────────────────────────────────────────────────
# Threshold-regels per sensor-type
# ─────────────────────────────────────────────────────────────────────────────

THRESHOLDS = {
    "vibratie_mm_s": {"warning": 5.0, "critical": 15.0, "unit": "mm/s",
                       "norm": "ISO 4866 trillingsmonitoring"},
    "doorbuiging_mm": {"warning": 5.0, "critical": 15.0, "unit": "mm",
                        "norm": "NEN 6720 toegestane doorbuiging"},
    "temperatuur_c": {"warning_low": -10, "warning_high": 50,
                      "critical_low": -25, "critical_high": 70,
                      "unit": "°C"},
    "waterhoogte_cm": {"warning": 200, "critical": 250, "unit": "cm"},
    "scheef_graad": {"warning": 2.0, "critical": 5.0, "unit": "°",
                     "norm": "Mattheck VTA kantelhoek"},
    "vochtigheid_pct": {"warning": 80, "critical": 95, "unit": "%"},
}


class IoTReading(BaseModel):
    sensor_id: str = Field(..., description="Unieke sensor-ID")
    asset_id: Optional[str] = Field(None, description="Asset waaraan sensor gekoppeld")
    sensor_type: str = Field(..., description="vibratie_mm_s / doorbuiging_mm / temperatuur_c / waterhoogte_cm / scheef_graad / vochtigheid_pct")
    value: float
    timestamp: Optional[datetime] = Field(None, description="ISO 8601; default = nu")


def _evaluate_threshold(sensor_type: str, value: float) -> Optional[str]:
    """Returns 'warning' | 'critical' | None."""
    cfg = THRESHOLDS.get(sensor_type)
    if not cfg:
        return None
    # Boundless-range type
    if "warning_low" in cfg:
        if value <= cfg["critical_low"] or value >= cfg["critical_high"]:
            return "critical"
        if value <= cfg["warning_low"] or value >= cfg["warning_high"]:
            return "warning"
        return None
    # Single-threshold type
    if "critical" in cfg and value >= cfg["critical"]:
        return "critical"
    if "warning" in cfg and value >= cfg["warning"]:
        return "warning"
    return None


@router.post("/ingest")
def ingest_reading(
    reading: IoTReading,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sensor-reading binnen → controleer threshold + genereer melding indien nodig.

    In MVP slaan we niet de hele tijdreeks op (geen aparte tabel) — we
    pakken alleen drempel-overschrijdingen op en converteren naar Meldingen.
    """
    severity = _evaluate_threshold(reading.sensor_type, reading.value)
    if not severity:
        return {
            "received": True,
            "sensor_id": reading.sensor_id,
            "value": reading.value,
            "severity": "ok",
            "action": "ignored",
        }

    # Drempel overschreden → maak melding
    priority = "kritiek" if severity == "critical" else "hoog"
    title = f"IoT-alert: {reading.sensor_type} = {reading.value}"
    cfg = THRESHOLDS.get(reading.sensor_type, {})
    description = (f"Sensor {reading.sensor_id} rapporteert {reading.sensor_type} = "
                   f"{reading.value} {cfg.get('unit','')}. Drempel: {severity}. "
                   f"Norm: {cfg.get('norm', '—')}.")

    asset_id_to_use = reading.asset_id
    m = Melding(
        title=title[:255],
        description=description,
        category="iot_alert",
        priority=priority,
        status="open",
        asset_id=asset_id_to_use,
        organization_id=current_user.organization_id,
        created_by=current_user.id,
    )
    db.add(m); db.commit(); db.refresh(m)

    log_action(db, request, current_user,
               action="iot.alert",
               entity_type="melding", entity_id=m.id,
               extra={
                   "sensor_id": reading.sensor_id,
                   "sensor_type": reading.sensor_type,
                   "value": reading.value,
                   "severity": severity,
               })
    return {
        "received": True,
        "sensor_id": reading.sensor_id,
        "value": reading.value,
        "severity": severity,
        "action": "melding_created",
        "melding_id": m.id,
    }


@router.get("/thresholds")
def list_thresholds(current_user: User = Depends(get_current_user)):
    """Beschikbare sensor-types + drempelwaarden + norm-referenties."""
    return {
        "thresholds": [
            {"type": k, **v} for k, v in THRESHOLDS.items()
        ],
        "note": "Drempel-overschrijding genereert automatisch een Melding met IoT-alert categorie.",
    }


@router.get("/sensors")
def list_sensors(
    asset_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lijst van sensoren afgeleid uit recente IoT-meldingen.

    In MVP: groepeer Meldingen met category='iot_alert' op sensor-ID
    uit de title/description. Voor productie komt een aparte Sensor-tabel.
    """
    q = db.query(Melding).filter(
        Melding.organization_id == current_user.organization_id,
        Melding.category == "iot_alert",
    )
    if asset_id:
        q = q.filter(Melding.asset_id == asset_id)
    meldingen = q.order_by(Melding.created_at.desc()).limit(200).all()

    sensors = {}
    for m in meldingen:
        info = sensors.setdefault(m.asset_id or "no-asset", {
            "asset_id": m.asset_id,
            "alert_count": 0,
            "last_alert_at": None,
            "highest_priority": "normaal",
        })
        info["alert_count"] += 1
        if not info["last_alert_at"] or m.created_at > info["last_alert_at"]:
            info["last_alert_at"] = m.created_at
            info["highest_priority"] = m.priority
    return {
        "count": len(sensors),
        "sensors": [
            {**v, "last_alert_at": v["last_alert_at"].isoformat() if v["last_alert_at"] else None}
            for v in sensors.values()
        ],
        "note": "MVP — afgeleid uit IoT-meldingen. Productie-versie heeft aparte Sensor-tabel.",
    }
