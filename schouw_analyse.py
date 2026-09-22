"""Opgenomen schouwbeelden analyseren, op de achtergrond.

Een rijdende schouw neemt alleen op: het toestel stuurt elke paar meter een
verpixeld beeld en wacht nergens op. Hier worden die beelden daarna
beoordeeld, een paar tegelijk. Zo rijdt de inspecteur door op 30 tot 50 km/u
en verschijnen de schades een paar minuten later in de ronde.

- **Een beeld wordt precies één keer beoordeeld.** Een opname gaat van
  `wacht` naar `bezig` in één update; wie die niet wint, laat hem liggen.
- **Een haperend model is geen leeg stuk weg.** Mislukt de aanroep, dan komt
  het beeld terug in de wachtrij (tot MAX_POGINGEN keer), in plaats van als
  "niets gezien" te worden geboekt.
- **Na een herstart gaat het verder.** Wat bleef liggen, wordt bij het
  opstarten opnieuw aangeboden; het beeld zelf staat in de foto-opslag.

In tests (SCHOUW_ANALYSE_INLINE=1) gebeurt het meteen, in dezelfde aanroep.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

PARALLEL = max(1, int(os.environ.get("SCHOUW_ANALYSE_PARALLEL", "4")))
MAX_POGINGEN = 3
WACHT_NA_FOUT_S = 15

_pool: Optional[ThreadPoolExecutor] = None
_slot = threading.Lock()


def _inline() -> bool:
    return os.environ.get("SCHOUW_ANALYSE_INLINE") == "1"


def aanbieden(opname_id: str, beeld: Optional[bytes] = None) -> None:
    """Een opname in de wachtrij zetten. `beeld` scheelt het terughalen uit
    de opslag; zonder beeld haalt de analyse hem zelf op."""
    if _inline():
        verwerk(opname_id, beeld)
        return
    global _pool
    with _slot:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=PARALLEL,
                                       thread_name_prefix="schouw-analyse")
    _pool.submit(_veilig, opname_id, beeld)


def _veilig(opname_id: str, beeld: Optional[bytes]) -> None:
    try:
        verwerk(opname_id, beeld)
    except Exception as exc:  # noqa: BLE001 — een draad mag nooit stil sterven
        print(f"[schouw-analyse] {opname_id}: {type(exc).__name__}: {exc}")


def verwerk(opname_id: str, beeld: Optional[bytes] = None) -> None:
    from database import SessionLocal
    from models import SchouwOpname
    from routers.schouw_router import analyseer_opname

    db = SessionLocal()
    try:
        geclaimd = (db.query(SchouwOpname)
                      .filter(SchouwOpname.id == opname_id, SchouwOpname.status == "wacht")
                      .update({"status": "bezig", "pogingen": SchouwOpname.pogingen + 1},
                              synchronize_session=False))
        db.commit()
        if not geclaimd:
            return
        o = db.get(SchouwOpname, opname_id)
        try:
            analyseer_opname(db, o, beeld)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            o = db.get(SchouwOpname, opname_id)
            if o is None:
                return
            o.fout = f"{type(exc).__name__}: {exc}"[:300]
            opnieuw = o.pogingen < MAX_POGINGEN
            o.status = "wacht" if opnieuw else "mislukt"
            db.commit()
            if opnieuw and not _inline():
                timer = threading.Timer(WACHT_NA_FOUT_S * o.pogingen, aanbieden,
                                        args=(opname_id, beeld))
                timer.daemon = True
                timer.start()
    finally:
        db.close()


def herstel_wachtrij() -> int:
    """Bij het opstarten: opnames die bleven liggen weer aanbieden. Wat op
    `bezig` stond, was bezig toen de server stopte; dat begint opnieuw."""
    from database import SessionLocal
    from models import SchouwOpname

    db = SessionLocal()
    try:
        (db.query(SchouwOpname).filter(SchouwOpname.status == "bezig")
           .update({"status": "wacht"}, synchronize_session=False))
        db.commit()
        ids = [i for (i,) in db.query(SchouwOpname.id)
                                .filter(SchouwOpname.status == "wacht")
                                .order_by(SchouwOpname.created_at).all()]
    except Exception as exc:  # noqa: BLE001 — bv. tabel bestaat nog niet
        print(f"[schouw-analyse] herstel overgeslagen: {exc}")
        return 0
    finally:
        db.close()
    for i in ids:
        aanbieden(i)
    if ids:
        print(f"[schouw-analyse] {len(ids)} opnames opnieuw in de wachtrij")
    return len(ids)
